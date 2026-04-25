"""
STEP 3 — Discover email-format pattern per hospital domain.

Approach:
  For each MX-valid domain in hospitals_vic.csv:
    1. Fetch the homepage + a handful of common pages (/contact, /about, /staff,
       /media, /research, /staff-directory) that tend to expose personal emails.
    2. Regex-extract all @domain emails.
    3. Drop "generic" localparts (info, admin, reception, enquiries, ...).
    4. Classify each remaining localpart's structural pattern:
         "alice.smith"   → firstname.lastname
         "alice_smith"   → firstname_lastname
         "asmith"        → flastname  (first token length 1)
         "alicesmith"    → firstnamelastname (no separator, ambiguous)
    5. Majority vote → domain's inferred format.
    6. If fewer than 2 personal emails found, leave format=null; the synth
       step will fall back to (firstname.lastname, flastname) candidate pair.

Output:
  data/domain_formats.json
    { "alfredhealth.org.au": {
        "format": "firstname.lastname",
        "confidence": "high" | "medium" | "low" | null,
        "samples": ["..."],
        "pages_probed": [...]
      },
      ... }
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from collections import Counter
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import config
from common import delay, extract_emails, read_csv

CANDIDATE_PATHS = [
    "/", "/contact", "/contact-us", "/about", "/about-us",
    "/staff", "/staff-directory", "/our-staff", "/our-people",
    "/media", "/media-releases", "/research", "/leadership",
    "/executive", "/board",
]

GENERIC_LOCALPARTS = {
    "info", "admin", "contact", "enquiries", "enquiry", "reception", "media",
    "hello", "support", "help", "office", "reply", "noreply", "no-reply",
    "donotreply", "privacy", "marketing", "hr", "careers", "recruitment",
    "events", "general", "reception", "secretary", "accounts", "billing",
    "it", "ict", "press", "feedback", "webmaster", "mail", "postmaster",
    "communications", "corporate", "foundation", "fundraising", "donations",
    "research", "ethics", "audit", "payroll", "media", "news",
}

# Substrings that mark a localpart as a departmental/team mailbox (not personal).
# If the localpart contains any of these, skip — don't use it for format inference.
DEPT_MARKERS = (
    "payable", "invoices", "patientinfo", "patientservices", "ethics",
    "clinic", "billing", "referral", "bookings", "appointment", "general",
    "enquiries", "media", "audit", "news", "human.resources", "humanresources",
    "accounts.", "admin.", "info.", "service", "helpdesk", "support",
)

# Hand-curated formats for known VIC hospital domains (based on observed real
# emails). These override the web-scrape inference for domains where the
# scrape returns noisy team-mailbox signals.
KNOWN_FORMATS = {
    "monash.edu":          "firstname.lastname",
    "alfredhealth.org.au": "firstname.lastname",
    "monashhealth.org":    "firstname.lastname",
    "rch.org.au":          "firstname.lastname",
    "svhm.org.au":         "firstname.lastname",
    "svha.org.au":         "firstname.lastname",
    "thermh.org.au":       "firstname.lastname",
    "austin.org.au":       "firstname.lastname",
    "petermac.org":        "firstname.lastname",
    "thewomens.org.au":    "firstname.lastname",
    "eyeandear.org.au":    "firstname.lastname",
    "easternhealth.org.au": "firstname.lastname",
    "westernhealth.org.au": "firstname.lastname",
    "nh.org.au":           "firstname.lastname",
    "mercy.com.au":        "firstname.lastname",
    "epworth.org.au":      "firstname.lastname",
    "cabrini.com.au":      "firstname.lastname",
    "ramsayhealth.com.au": "firstname.lastname",
    "healthscope.com.au":  "firstname.lastname",
    "sjog.org.au":         "firstname.lastname",
    "bendigohealth.org.au": "firstname.lastname",
    "barwonhealth.org.au": "firstname.lastname",
    "peninsulahealth.org.au": "firstname.lastname",
}

DEFAULT_FORMAT = "firstname.lastname"


def classify_localpart(local: str) -> str | None:
    """Return a format-key guess or None if ambiguous / not personal-looking."""
    local = local.lower().strip()
    if not local or local in GENERIC_LOCALPARTS:
        return None
    if any(m in local for m in DEPT_MARKERS):
        return None
    # Skip very short or numeric or too-symbolic
    if len(local) < 3 or any(c.isdigit() for c in local):
        return None
    if "." in local:
        parts = local.split(".")
        if len(parts) == 2 and all(p.isalpha() and len(p) >= 2 for p in parts):
            if len(parts[0]) == 1:
                return "f.lastname"
            return "firstname.lastname"
    if "_" in local:
        parts = local.split("_")
        if len(parts) == 2 and all(p.isalpha() and len(p) >= 2 for p in parts):
            return "firstname_lastname"
    if "-" in local:
        return None  # don't infer hyphen patterns
    if local.isalpha() and len(local) <= 12 and len(local) >= 4:
        # Heuristic: localpart starts with 1 letter (initial) + rest = lastname
        # Hard to distinguish from firstnamelastname, so we label "flastname"
        # only when very short, else ambiguous.
        if len(local) <= 8:
            return "flastname"
        return "firstnamelastname"
    return None


def fetch_one(url: str) -> str | None:
    try:
        r = requests.get(
            url, headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT_S, allow_redirects=True,
        )
        if r.status_code == 200 and "text/html" in (r.headers.get("content-type", "") or ""):
            return r.text
    except Exception:
        pass
    return None


def scrape_domain(domain: str) -> tuple[list[str], list[str]]:
    """Return (emails_at_domain, pages_with_hits)."""
    base = f"https://{domain}"
    found_emails: set[str] = set()
    pages_with_hits: list[str] = []
    for path in CANDIDATE_PATHS:
        url = urljoin(base, path)
        html = fetch_one(url)
        if not html:
            delay(0.5, 1.2)
            continue
        emails = extract_emails(html)
        hits_here = [e for e in emails if e.split("@", 1)[1].lower() == domain.lower()]
        if hits_here:
            pages_with_hits.append(path)
        found_emails.update(hits_here)
        delay(*config.HTTP_JITTER_S)
    return sorted(found_emails), pages_with_hits


def infer_format(emails: list[str]) -> tuple[str | None, str, list[str]]:
    """Return (format_key, confidence, representative_samples)."""
    personal_emails = []
    votes = Counter()
    for e in emails:
        local = e.split("@", 1)[0]
        fmt = classify_localpart(local)
        if fmt:
            personal_emails.append(e)
            votes[fmt] += 1
    if not votes:
        return None, "none", []
    top_fmt, top_n = votes.most_common(1)[0]
    total = sum(votes.values())
    ratio = top_n / total
    if total >= 3 and ratio >= 0.7:
        conf = "high"
    elif total >= 2 and ratio >= 0.6:
        conf = "medium"
    else:
        conf = "low"
    return top_fmt, conf, personal_emails[:5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", default=[], help="Limit to these domains (dry-test)")
    args = ap.parse_args()

    hospitals = read_csv(config.HOSPITALS_CSV)
    domains = sorted({r["domain"] for r in hospitals if r["mx_ok"].lower() == "true" and r["domain"]})
    if args.only:
        domains = [d for d in domains if d in args.only]
    print(f"[formats] scanning {len(domains)} domains")

    results = {}
    for i, d in enumerate(domains, 1):
        if d in KNOWN_FORMATS:
            fmt = KNOWN_FORMATS[d]
            results[d] = {
                "format": fmt,
                "confidence": "known",
                "samples": [],
                "pages_probed": [],
                "raw_email_count": 0,
                "source": "hardcoded",
            }
            print(f"[formats] ({i}/{len(domains)}) {d}  [known → {fmt}]")
            continue
        print(f"[formats] ({i}/{len(domains)}) {d}")
        emails, pages = scrape_domain(d)
        fmt, conf, samples = infer_format(emails)
        if not fmt:
            fmt = DEFAULT_FORMAT
            conf = "default"
        print(f"  emails found: {len(emails)}  pages with hits: {pages}")
        print(f"  inferred: {fmt!r} ({conf})  samples: {samples}")
        results[d] = {
            "format": fmt,
            "confidence": conf,
            "samples": samples,
            "pages_probed": pages,
            "raw_email_count": len(emails),
            "source": "scraped" if conf != "default" else "default",
        }
    config.DOMAIN_FORMATS_JSON.write_text(json.dumps(results, indent=2))
    print()
    print(f"[formats] wrote {config.DOMAIN_FORMATS_JSON}")
    # Summary breakdown
    conf_counts = Counter(v["confidence"] for v in results.values())
    fmt_counts = Counter(v["format"] or "UNKNOWN" for v in results.values())
    print(f"[formats] confidence: {dict(conf_counts)}")
    print(f"[formats] formats   : {dict(fmt_counts)}")


if __name__ == "__main__":
    main()
