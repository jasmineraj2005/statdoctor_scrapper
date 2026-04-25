"""
Re-verify all rows with email_confidence='unverified' in the enriched CSV.
Bypasses disify_verify.py's load_done dedup — re-runs Disify API on each email
and appends a fresh row to disify_probe_log.csv. apply_to_practitioners.py
picks the latest verified_at per email, so results auto-upgrade.
"""
from __future__ import annotations
import asyncio
import csv
from pathlib import Path

import disify_verify as dv

THIS_DIR = Path(__file__).resolve().parent
ENRICHED_CSV = THIS_DIR.parent / "db_ARPHA" / "vic_practitioners_enriched.csv"


async def main():
    import sys
    batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 2000

    # Tune for single-IP rate-limit avoidance
    dv.CONCURRENCY = 2
    dv.JITTER_S = 3.0

    with open(ENRICHED_CSV, newline="", encoding="utf-8") as f:
        targets = [
            {"practitioner_id": r["practitioner_id"], "email": r["candidate_email"]}
            for r in csv.DictReader(f)
            if r["candidate_email"] and r["email_confidence"] == "unverified"
        ][:batch_size]
    print(f"[reverify] processing {len(targets)} unverified rows (concurrency=2, jitter=3s)")

    import aiohttp
    connector = aiohttp.TCPConnector(limit=dv.CONCURRENCY)
    sem = asyncio.Semaphore(dv.CONCURRENCY)
    counts = {"catch_all": 0, "failed": 0, "unverified": 0}
    # Rate-limit detector: if last N results are all "failed" we bail
    recent = []
    FAIL_WINDOW = 30
    bail = False

    async with aiohttp.ClientSession(connector=connector,
                                      headers={"User-Agent": "arpha-email-enrichment/1.0"}) as session:
        tasks = [dv.verify_one(session, t["practitioner_id"], t["email"], sem) for t in targets]
        processed = 0
        for coro in asyncio.as_completed(tasks):
            row = await coro
            processed += 1
            conf = row["confidence"]
            counts[conf] = counts.get(conf, 0) + 1
            dv.append_row(row)
            recent.append(conf)
            if len(recent) > FAIL_WINDOW: recent.pop(0)

            if not bail and len(recent) == FAIL_WINDOW and all(c == "failed" for c in recent):
                print(f"\n⚠ RATE-LIMIT DETECTED — last {FAIL_WINDOW} all failed. Bailing.")
                bail = True
                break

            if processed % 50 == 0 or processed == len(targets):
                parts = "  ".join(f"{k}: {v}" for k, v in counts.items())
                print(f"  {processed}/{len(targets)}  |  {parts}", flush=True)

    print(f"\n[reverify] stopped at {processed}. {dict(counts)}")
    if bail:
        print(f"[reverify] Rate-limited. Switch IP and re-run.")


if __name__ == "__main__":
    asyncio.run(main())
