"""
For each unique GP clinic from gp_practices.csv:
  1. Generate candidate domains from clinic name (strip filler words, try TLDs)
  2. DNS-check MX records (fast, free, no captcha)
  3. For passing candidates, fetch homepage and confirm clinic name appears
  4. Record domain → clinic mapping

Output: data/gp_clinic_domains.json
"""
from __future__ import annotations
import csv
import json
import re
import time
from collections import defaultdict
from pathlib import Path

import dns.resolver
import requests
from bs4 import BeautifulSoup

THIS_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(THIS_DIR))
import config as _cfg
# Per-state paths via config.gp_practices_csv(state) / config.gp_clinic_domains_json(state)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

FILLER = {"the", "a", "an", "of", "and", "at", "on", "in", "for",
          "medical", "medicine", "clinic", "clinics", "centre", "center",
          "practice", "practices", "surgery", "surgeries", "doctors",
          "general", "family", "group", "health", "healthcare", "care",
          "services", "consulting", "consultants", "specialists", "specialist"}

TLDS = (".com.au", ".com", ".org.au", ".net.au", ".health.au")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower()).strip()


def tokenize(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def candidate_domains(clinic_name: str, suburb: str) -> list[str]:
    """Generate candidate domains. Only variants that include the clinic's
    DISTINCTIVE tokens (not just suburb or filler words) — this keeps precision high."""
    toks = tokenize(clinic_name)
    non_filler = [t for t in toks if t not in FILLER]
    suburb_toks = set(tokenize(suburb))
    # Truly distinctive = content tokens that aren't the suburb name
    distinctive = [t for t in non_filler if t not in suburb_toks]

    candidates = []
    # Full clinic slug (strongest — includes ALL tokens incl. "medical" "centre")
    if toks:
        candidates.append(slugify(clinic_name))
    # All content tokens (drops filler but keeps distinctive + suburb-in-name)
    if non_filler and non_filler != toks:
        candidates.append("".join(non_filler))
        candidates.append("-".join(non_filler))
    # Content-tokens first 2 and 3
    if len(non_filler) >= 2:
        candidates.append("".join(non_filler[:2]))
        candidates.append("-".join(non_filler[:2]))
    if len(non_filler) >= 3:
        candidates.append("".join(non_filler[:3]))
    # Distinctive-only variants
    if distinctive:
        candidates.append("".join(distinctive))
        candidates.append("-".join(distinctive))
        # Distinctive + medical/clinic suffixes
        base = "".join(distinctive[:2]) if len(distinctive) >= 2 else distinctive[0]
        for suffix in ("medical", "medicalcentre", "clinic", "medicalpractice"):
            candidates.append(base + suffix)

    # Dedupe, filter very short & suburb-only
    suburb_slug = slugify(suburb)
    seen = set()
    out = []
    for c in candidates:
        c = re.sub(r"[^a-z0-9-]", "", c)
        if not c or len(c) < 5 or c in seen:
            continue
        # REJECT suburb-only candidates — they match unrelated suburban sites
        if c == suburb_slug or c.replace("-", "") == suburb_slug:
            continue
        seen.add(c)
        for tld in TLDS:
            out.append(c + tld)
    return out


# DNS cache so repeated lookups are free
_dns_cache: dict[str, bool] = {}


def has_mx(domain: str) -> bool:
    if domain in _dns_cache:
        return _dns_cache[domain]
    try:
        dns.resolver.resolve(domain, "MX", lifetime=3)
        _dns_cache[domain] = True
        return True
    except Exception:
        _dns_cache[domain] = False
        return False


# Specific to AU medical/GP sites — avoids generic "health" which appears everywhere
TITLE_KEYWORDS = ("medical", "clinic", "doctor", "gp ", "general practi",
                  "surgery", "medicare", "bulk bill", "bulk-bill",
                  "family practice", "family medical", "medical centre")
BODY_SIGNALS = ("appointment", "medicare", "bulk bill", "bulk-bill",
                "book an appointment", "general practitioner", "our doctors",
                "our gp", "consultation")


def verify_domain_for_clinic(domain: str, clinic_name: str, suburb: str) -> tuple[bool, str]:
    """
    Strict precision — require ALL of:
      (a) a distinctive clinic-name token in title or body
      (b) a medical keyword in TITLE (not just body), OR 2+ body signals
    This kills false positives — whiskey brands, tourism sites, super funds.
    """
    for scheme in ("https://", "http://"):
        url = scheme + domain
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": UA}, allow_redirects=True)
            if r.status_code >= 400:
                continue
            text = r.text.lower()
        except Exception:
            continue
        try:
            soup = BeautifulSoup(r.text, "lxml")
            title = (soup.title.get_text(strip=True) if soup.title else "")
        except Exception:
            title = ""
        title_lc = title.lower()

        title_has_medical = any(kw in title_lc for kw in TITLE_KEYWORDS)
        body_signals = sum(1 for kw in BODY_SIGNALS if kw in text)
        has_medical_signal = title_has_medical or body_signals >= 2

        if not has_medical_signal:
            return False, f"not_medical title={title[:80]!r}"

        # Distinctive clinic token — non-filler, non-suburb, ≥4 chars
        suburb_toks = set(tokenize(suburb))
        distinctive = [t for t in tokenize(clinic_name)
                       if t not in FILLER and t not in suburb_toks and len(t) >= 4]
        if not distinctive:
            distinctive = [t for t in tokenize(clinic_name) if t not in FILLER and len(t) >= 4]

        hits = sum(1 for t in distinctive if t in text or t in title_lc)
        if distinctive and hits >= 1:
            return True, f"hits={hits}/{len(distinctive)} title_med={title_has_medical} body_sig={body_signals} title={title[:80]!r}"
        return False, f"no_distinctive_hit title={title[:80]!r}"
    return False, "http_fail"


def load_clusters(state: str) -> dict[tuple, list[dict]]:
    rows = list(csv.DictReader(open(_cfg.gp_practices_csv(state))))
    clusters: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["method"] != "halaxy_sitemap" or not r["clinic_name"]:
            continue
        key = (r["clinic_name"].strip().lower(),
               r["clinic_street"].strip().lower(),
               r["clinic_suburb"].strip().lower())
        clusters[key].append(r)
    return clusters


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=None)
    args = ap.parse_args()
    state = _cfg.state_lc(args.state)
    print(f"[guess] state: {state.upper()}")

    clusters = load_clusters(state)
    print(f"[guess] unique clinic clusters: {len(clusters)}")

    out_json = _cfg.gp_clinic_domains_json(state)
    if out_json.exists():
        known = json.loads(out_json.read_text())
    else:
        known = {}
    print(f"[guess] already resolved: {len(known)}")

    stats = {"resolved": 0, "no_mx_match": 0, "mx_but_no_content_match": 0}
    for i, ((name, street, suburb), gps) in enumerate(clusters.items(), 1):
        key = f"{name}||{street}||{suburb}"
        if key in known:
            continue

        record = {
            "clinic_name": name, "street": street, "suburb": suburb,
            "gp_count": len(gps),
            "domain": "", "evidence": "", "method": "",
            "tried_domains": [],
        }

        domains = candidate_domains(name, suburb)
        record["tried_domains"] = domains
        resolved = False
        for d in domains:
            if not has_mx(d):
                continue
            matched, evidence = verify_domain_for_clinic(d, name, suburb)
            if matched:
                record["domain"] = d
                record["evidence"] = evidence
                record["method"] = "dns+content"
                stats["resolved"] += 1
                resolved = True
                break
            else:
                # Keep best candidate in case nothing verifies
                if not record["domain"]:
                    record["domain"] = d
                    record["evidence"] = evidence
                    record["method"] = "mx_only"

        if not resolved:
            if record["domain"]:
                stats["mx_but_no_content_match"] += 1
            else:
                stats["no_mx_match"] += 1

        known[key] = record

        if i % 25 == 0 or i == len(clusters):
            out_json.write_text(json.dumps(known, indent=2))
            print(f"  [{i}/{len(clusters)}] {stats}")

    out_json.write_text(json.dumps(known, indent=2))
    print(f"\n[guess] DONE. {stats}")


if __name__ == "__main__":
    main()
