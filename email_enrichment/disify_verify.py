"""
STEP 5 (replacement) — Disify email verification.

Replaces smtp_verify.py. Uses https://www.disify.com/api/email/{email}
for domain-level validation. Disify confirms domain exists + has MX + is
not disposable, but cannot confirm individual mailboxes — results are
labelled catch_all (same as SMTP catch-all: domain accepts mail, inbox
unconfirmable). DNS MX fallback fires if Disify API fails 3x in a row.

Input:  data/smtp_targets.csv          (practitioner_id, email)
Output: data/disify_probe_log.csv      (appended per-row, crash-safe)
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import dns.resolver

# ── Paths ────────────────────────────────────────────────────────────────────
THIS_DIR = Path(__file__).resolve().parent
TARGETS_CSV  = THIS_DIR / "data" / "smtp_targets.csv"
PROBE_LOG    = THIS_DIR / "data" / "disify_probe_log.csv"
DECISIONS_LOG = THIS_DIR / "decisions.log"

DISIFY_URL   = "https://disify.com/api/email/{email}"

# ── Tunables ─────────────────────────────────────────────────────────────────
CONCURRENCY      = 5
JITTER_S         = 1.5   # delay per request (polite)
MAX_RETRIES      = 3     # consecutive failures before fallback
REQUEST_TIMEOUT  = 10    # seconds
PROGRESS_EVERY   = 50

LOG_FIELDS = [
    "practitioner_id", "candidate_email", "candidate_domain",
    "format", "domain_valid", "dns", "disposable",
    "confidence", "source", "verified_at",
]


# ── Confidence logic ─────────────────────────────────────────────────────────

def classify_disify(resp: dict) -> str:
    """
    Disify validates domain+MX, not individual mailboxes.
    → All passing results labelled catch_all (domain real, inbox unconfirmable).
    → disposable=true or domain/dns fail → failed.
    → API parse error → unverified.
    """
    if not isinstance(resp, dict):
        return "unverified"
    fmt       = resp.get("format", False)
    domain_ok = resp.get("domain", False)
    dns_ok    = resp.get("dns", False)
    disposable = resp.get("disposable", False)

    if not fmt or not domain_ok or not dns_ok:
        return "failed"
    if disposable:
        return "failed"
    return "catch_all"


def classify_dns_fallback(domain: str) -> str:
    """MX-only fallback when Disify is unreachable. Returns unverified (domain real) or failed."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return "unverified" if answers else "failed"
    except Exception:
        return "failed"


# ── I/O helpers ──────────────────────────────────────────────────────────────

def load_done() -> set[str]:
    """Return set of emails already in probe log (for resume)."""
    if not PROBE_LOG.exists():
        return set()
    with open(PROBE_LOG, newline="", encoding="utf-8") as f:
        return {r["candidate_email"] for r in csv.DictReader(f) if r.get("candidate_email")}


def load_targets() -> list[dict]:
    with open(TARGETS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_row(row: dict) -> None:
    exists = PROBE_LOG.exists()
    with open(PROBE_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)


# ── Core verifier ────────────────────────────────────────────────────────────

async def verify_one(
    session: aiohttp.ClientSession,
    pid: str,
    email: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    domain = email.split("@")[1] if "@" in email else ""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async with semaphore:
        await asyncio.sleep(JITTER_S)

        # Try Disify up to MAX_RETRIES times with backoff, then DNS fallback.
        # Per-request only — no shared state that permanently disables the API.
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(
                    DISIFY_URL.format(email=email),
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                    allow_redirects=True,
                ) as resp:
                    text = await resp.text()
                    data = json.loads(text)
                    conf = classify_disify(data)
                    return {
                        "practitioner_id": pid, "candidate_email": email,
                        "candidate_domain": domain,
                        "format":       str(data.get("format", "")),
                        "domain_valid": str(data.get("domain", "")),
                        "dns":          str(data.get("dns", "")),
                        "disposable":   str(data.get("disposable", "")),
                        "confidence":   conf,
                        "source":       "disify",
                        "verified_at":  now,
                    }
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(2 ** attempt)   # 1s, 2s, 4s backoff

        # All retries exhausted → DNS MX fallback for this row only
        conf = classify_dns_fallback(domain)
        return {
            "practitioner_id": pid, "candidate_email": email,
            "candidate_domain": domain,
            "format": "", "domain_valid": "", "dns": "", "disposable": "",
            "confidence": conf, "source": "dns_fallback", "verified_at": now,
        }


# ── Decisions log ─────────────────────────────────────────────────────────────

def _log_decision(point: str, options: str, chosen: str, reason: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(DECISIONS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] DECISION: {point} | OPTIONS: {options} | CHOSE: {chosen} | REASON: {reason}\n")


# ── Main ─────────────────────────────────────────────────────────────────────

async def run(limit: int | None = None, dry_run: bool = False) -> None:
    targets = load_targets()
    if limit:
        targets = targets[:limit]

    done = load_done()
    pending = [t for t in targets if t["email"] not in done]

    total = len(targets)
    skipped = len(targets) - len(pending)
    print(f"[disify] targets: {total}  already done: {skipped}  pending: {len(pending)}")

    if not pending:
        print("[disify] nothing to do.")
        return

    counts = {"catch_all": 0, "failed": 0, "unverified": 0}

    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    headers   = {"User-Agent": "arpha-email-enrichment/1.0"}
    semaphore = asyncio.Semaphore(CONCURRENCY)

    dry_rows = []   # collected only in dry-run mode for table print

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [
            verify_one(session, t["practitioner_id"], t["email"], semaphore)
            for t in pending
        ]
        processed = 0
        for coro in asyncio.as_completed(tasks):
            row = await coro
            processed += 1
            conf = row["confidence"]
            counts[conf] = counts.get(conf, 0) + 1

            if dry_run:
                dry_rows.append(row)
            else:
                append_row(row)

            if processed % PROGRESS_EVERY == 0 or processed == len(pending):
                parts = "  ".join(f"{k}: {v}" for k, v in counts.items())
                print(f"  {processed}/{len(pending)}  |  {parts}", flush=True)

    if dry_run:
        return dry_rows
    print(f"\n[disify] done. {dict(counts)}")


def dry_run_table(rows: list[dict]) -> None:
    print(f"\n{'EMAIL':<48} {'CONF':<12} {'FMT':<6} {'DOM':<6} {'DNS':<6} {'DISP':<6} {'SRC'}")
    print("-" * 110)
    for r in rows:
        print(
            f"{r['candidate_email']:<48} "
            f"{r['confidence']:<12} "
            f"{r['format']:<6} "
            f"{r['domain_valid']:<6} "
            f"{r['dns']:<6} "
            f"{r['disposable']:<6} "
            f"{r['source']}"
        )
    counts = {}
    for r in rows:
        counts[r["confidence"]] = counts.get(r["confidence"], 0) + 1
    total = len(rows)
    print()
    for k, v in sorted(counts.items()):
        print(f"  {k:<14} {v:>4}  ({v/total*100:.0f}%)")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Test on first 20 rows, print table, do not write log")
    p.add_argument("--limit",   type=int,            help="Cap rows processed (default: all)")
    args = p.parse_args()

    os.chdir(THIS_DIR)

    if args.dry_run:
        rows = asyncio.run(run(limit=args.limit or 20, dry_run=True))
        dry_run_table(rows)
    else:
        asyncio.run(run(limit=args.limit))
