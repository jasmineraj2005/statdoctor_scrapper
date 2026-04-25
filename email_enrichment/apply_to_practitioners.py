"""
STEP 6 — Join everything → vic_practitioners_enriched.csv.

Inputs:
  db_ARPHA/vic_practitioners.csv                          (24,989 rows)
  linkedin_outreach/data/vic_high_yield_subset.csv         (Task 1 LinkedIn subset, ~3,988)
  linkedin_outreach/data/vic_linkedin_classifications.csv  (Task 1 output; may not exist yet)
  email_enrichment/data/postcode_domains.json              (from build_postcode_index.py)
  email_enrichment/data/domain_formats.json                (from discover_formats.py)
  email_enrichment/data/smtp_probe_log.csv                 (from smtp_verify.py; may be empty)

Logic:
  For each of the 24,989 practitioners:
    - pipeline:
        linkedin   if id in subset AND linkedin_classifications says influencer/pending
        email      otherwise (or subset-id classified non_influencer)
    - If pipeline == linkedin:
        candidate_email / domain / confidence stay blank; confidence = "n_a"
    - If pipeline == email:
        Lookup postcode → candidates → pick top-ranked candidate domain.
        Get format for that domain; synthesise email.
        email_confidence = actual from smtp_probe_log if present, else "pending".

Output:
  db_ARPHA/vic_practitioners_enriched.csv
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import config
from common import parse_name, read_csv, synth_email

# Extract 4-digit Australian postcode from any string (e.g. location field)
_POSTCODE_RE = re.compile(r"\b(\d{4})\b")


def resolve_postcode(practitioner: dict) -> str:
    """
    Prefer `postcode_searched` if it's 4 digits. Otherwise parse from `location`
    (e.g. "Richmond, VIC, 3121" → "3121"). Returns "" if nothing usable.
    """
    ps = (practitioner.get("postcode_searched") or "").strip()
    if ps.isdigit() and len(ps) == 4:
        return ps
    loc = practitioner.get("location") or ""
    m = _POSTCODE_RE.search(loc)
    return m.group(1) if m else ""

FIELDS = [
    # original
    "practitioner_id", "name", "profession", "division", "registration_type",
    "speciality", "location", "postcode_searched",
    # added
    "pipeline", "candidate_domain", "candidate_email", "email_source",
    "email_format", "email_confidence", "linkedin_classification",
    "verified_at",
]


def _load_postcode_index():
    if not config.POSTCODE_DOMAINS_JSON.exists():
        return {}
    return json.loads(config.POSTCODE_DOMAINS_JSON.read_text())


def _load_domain_formats():
    if not config.DOMAIN_FORMATS_JSON.exists():
        return {}
    return json.loads(config.DOMAIN_FORMATS_JSON.read_text())


def _load_linkedin_subset_ids() -> set[str]:
    if not config.LINKEDIN_SUBSET_CSV.exists():
        return set()
    return {r["practitioner_id"] for r in read_csv(config.LINKEDIN_SUBSET_CSV)}


def _load_linkedin_classifications() -> dict[str, str]:
    """practitioner_id → classification (influencer | non_influencer | not_found | error)."""
    if not config.LINKEDIN_CLASSIFICATIONS_CSV.exists():
        return {}
    out = {}
    for r in read_csv(config.LINKEDIN_CLASSIFICATIONS_CSV):
        pid = r.get("practitioner_id", "")
        cls = r.get("classification", "")
        if pid and cls:
            out[pid] = cls
    return out


def _load_trusted_domains() -> set[str]:
    """Domains that have at least one catch_all verdict in the log are
    deemed deliverable; any unverified row on the same domain auto-flips."""
    disify_log = getattr(config, "DISIFY_PROBE_LOG_CSV", None)
    if not disify_log or not disify_log.exists():
        return set()
    out: set[str] = set()
    for r in read_csv(disify_log):
        if r.get("confidence") == "catch_all" and r.get("candidate_domain"):
            out.add(r["candidate_domain"].lower())
    return out


def _load_smtp_results() -> dict[str, dict]:
    """
    Load verification results from disify_probe_log.csv (primary).
    Falls back to smtp_probe_log.csv if disify log is absent.
    Returns email → {confidence/verdict, verified_at}.
    """
    # Primary: Disify log — confidence column already uses our tier labels
    disify_log = getattr(config, "DISIFY_PROBE_LOG_CSV", None)
    if disify_log and disify_log.exists():
        by_email: dict[str, dict] = {}
        for r in read_csv(disify_log):
            em = r.get("candidate_email", "")
            if not em:
                continue
            prior = by_email.get(em)
            ts = r.get("verified_at", "")
            if not prior or ts > prior.get("verified_at", ""):
                # normalise to keys that verdict_to_confidence expects
                by_email[em] = {"verdict": r.get("confidence", ""), "probed_at": ts}
        return by_email

    # Legacy fallback: smtp_probe_log.csv
    if not config.SMTP_PROBE_LOG_CSV.exists():
        return {}
    by_email = {}
    for r in read_csv(config.SMTP_PROBE_LOG_CSV):
        em = r.get("email", "")
        if not em:
            continue
        prior = by_email.get(em)
        if not prior or (r.get("probed_at", "") > prior.get("probed_at", "")):
            by_email[em] = r
    return by_email


# ── Confidence mapping ───────────────────────────────────────────────────────

def verdict_to_confidence(verdict: str) -> str:
    if verdict == "verified":
        return config.CONF_VERIFIED
    if verdict == "failed":
        return config.CONF_FAILED
    if verdict == "catch_all":
        return config.CONF_CATCH_ALL
    if verdict == "ip_blocked":
        return config.CONF_IP_BLOCKED
    if verdict == "unverified":
        return config.CONF_UNVERIFIED
    return "pending"


def pick_pipeline(practitioner_id: str, in_subset: bool, li_class: str | None) -> str:
    """
    Rule:
      - In subset + influencer → linkedin
      - In subset + non_influencer → email (LinkedIn won't connect, fall back)
      - In subset + not yet classified → linkedin (provisional; re-run when class lands)
      - Not in subset → email
    """
    if in_subset:
        if li_class == "influencer":
            return "linkedin"
        if li_class in ("non_influencer", "not_found", "error"):
            return "email"
        return "linkedin"  # pending classification
    return "email"


def _load_gp_clinic_lookup() -> dict:
    """Build practitioner_id → verified clinic domain (if any)."""
    gp_csv = config.DATA_DIR / "gp_practices.csv"
    domains_json = config.DATA_DIR / "gp_clinic_domains.json"
    if not gp_csv.exists() or not domains_json.exists():
        return {}
    domain_map = json.loads(domains_json.read_text())
    out = {}
    for r in read_csv(gp_csv):
        if r.get("method") != "halaxy_sitemap" or not r.get("clinic_name"):
            continue
        key = f"{r['clinic_name'].strip().lower()}||{r['clinic_street'].strip().lower()}||{r['clinic_suburb'].strip().lower()}"
        rec = domain_map.get(key)
        if rec and rec.get("method") == "dns+content" and rec.get("domain"):
            out[r["practitioner_id"]] = rec["domain"]
    return out


def synthesise_email(practitioner: dict, postcode_index: dict, domain_formats: dict,
                     gp_clinic_domains: dict | None = None) -> tuple[str, str, str, str]:
    """
    Returns (candidate_domain, candidate_email, email_source, email_format).
    email_source is "gp_clinic", "hospital_postcode", "gp_unresolved", or unresolved_*.
    """
    first, last = parse_name(practitioner.get("name", ""))
    if not first or not last:
        return "", "", "unresolved_name", ""

    speciality = (practitioner.get("speciality") or "").lower()
    is_gp = "general practice" in speciality
    if is_gp:
        # Try the GP clinic domain lookup first
        if gp_clinic_domains:
            domain = gp_clinic_domains.get(practitioner["practitioner_id"])
            if domain:
                fmt = "firstname.lastname"
                email = synth_email(first, last, domain, fmt)
                if email:
                    return domain, email, "gp_clinic", fmt
        # No verified clinic domain → don't fall back to hospital synthesis
        return "", "", "gp_unresolved", ""

    pc = resolve_postcode(practitioner)
    entry = postcode_index.get(pc)
    if not entry or not entry.get("candidates"):
        return "", "", "unresolved_postcode", ""

    # Top candidate domain (caller can expand to multiple candidates later)
    top = entry["candidates"][0]
    domain = top["domain"]
    fmt_entry = domain_formats.get(domain, {})
    fmt = fmt_entry.get("format") or "firstname.lastname"
    email = synth_email(first, last, domain, fmt)
    if not email:
        return domain, "", "unresolved_format", fmt
    return domain, email, "hospital_postcode", fmt


# ── Main ─────────────────────────────────────────────────────────────────────

def build():
    practitioners = read_csv(config.VIC_PRACTITIONERS_CSV)
    postcode_index = _load_postcode_index()
    gp_clinic_domains = _load_gp_clinic_lookup()
    trusted_domains = _load_trusted_domains()
    domain_formats = _load_domain_formats()
    subset_ids = _load_linkedin_subset_ids()
    li_classifications = _load_linkedin_classifications()
    smtp = _load_smtp_results()

    print(f"[apply] practitioners         : {len(practitioners)}")
    print(f"[apply] postcode-index size   : {len(postcode_index)}")
    print(f"[apply] domain-format entries : {len(domain_formats)}")
    print(f"[apply] LinkedIn subset ids   : {len(subset_ids)}")
    print(f"[apply] LinkedIn classifications: {len(li_classifications)}")
    print(f"[apply] SMTP log entries      : {len(smtp)}")

    out_rows = []
    pipeline_counter: Counter = Counter()
    source_counter: Counter = Counter()
    conf_counter: Counter = Counter()

    for p in practitioners:
        pid = p["practitioner_id"]
        in_subset = pid in subset_ids
        li_cls = li_classifications.get(pid, "")
        pipeline = pick_pipeline(pid, in_subset, li_cls)
        pipeline_counter[pipeline] += 1

        # Email synthesis runs for ALL practitioners regardless of pipeline —
        # LinkedIn-pipeline rows also get an email candidate as a parallel reach channel.
        dom, email, source, fmt = synthesise_email(p, postcode_index, domain_formats, gp_clinic_domains)
        source_counter[source] += 1
        confidence = "pending" if email else config.CONF_UNVERIFIED
        verified_at = ""
        if email and email in smtp:
            entry = smtp[email]
            confidence = verdict_to_confidence(entry.get("verdict", ""))
            verified_at = entry.get("probed_at", "")
        # Domain-trust promotion: if the row stayed unverified but its domain
        # has been catch_all elsewhere in the log, promote to catch_all.
        if confidence == config.CONF_UNVERIFIED and dom and dom.lower() in trusted_domains:
            confidence = config.CONF_CATCH_ALL
        conf_counter[confidence] += 1

        out_rows.append({
            **p,
            "pipeline": pipeline,
            "candidate_domain": dom,
            "candidate_email": email,
            "email_source": source,
            "email_format": fmt,
            "email_confidence": confidence,
            "linkedin_classification": li_cls or ("pending" if pipeline == "linkedin" else ""),
            "verified_at": verified_at,
        })

    # Write output
    if config.ENRICHED_CSV.exists():
        config.ENRICHED_CSV.unlink()
    with open(config.ENRICHED_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print(f"[apply] wrote {config.ENRICHED_CSV}")

    print()
    print(f"[apply] pipeline split : {dict(pipeline_counter)}")
    print(f"[apply] email_source   : {dict(source_counter)}")
    print(f"[apply] email_confidence:")
    for k, v in conf_counter.most_common():
        print(f"           {v:>7}  {k}")

    # Emit the targets CSV for Step 5 to consume (only pipeline=email + has email + not verified)
    targets = [
        {"practitioner_id": r["practitioner_id"], "email": r["candidate_email"]}
        for r in out_rows
        if r["candidate_email"]
        and r["email_confidence"] == "pending"
    ]
    targets_csv = config.DATA_DIR / "smtp_targets.csv"
    if targets_csv.exists():
        targets_csv.unlink()
    with open(targets_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["practitioner_id", "email"])
        w.writeheader()
        for t in targets:
            w.writerow(t)
    print(f"[apply] wrote {targets_csv}  ({len(targets)} targets pending SMTP verification)")


if __name__ == "__main__":
    build()
