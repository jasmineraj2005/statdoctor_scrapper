"""
STEP 1c — Resolve each VIC hospital to an official mail domain.

Inputs:
  data/hospitals_vic_raw.csv   (from fetch_aihw_hospitals.py, 257 rows)
  data/healthvic_directory.csv (from fetch_vic_health_directory.py, 67 rows)

Approach:
  1. Exact-match AIHW.lhn_name → healthvic.health_service_name (normalised)
  2. Fuzzy-match with rapidfuzz for near-misses (threshold 85)
  3. Manual overrides for known-problem cases
  4. Private hospitals (not in healthvic): DuckDuckGo HTML fallback search
  5. MX-verify the resolved domain; drop if no MX record

Output:
  data/hospitals_vic.csv
    reporting_unit_code, name, private, latitude, longitude, lhn_name,
    phn_name, health_service_name, domain, tier, resolution_method, mx_ok
"""
from __future__ import annotations
import csv
import re
import sys
import time
from urllib.parse import urlparse, quote_plus

import dns.resolver
import requests
from bs4 import BeautifulSoup
from rapidfuzz import process, fuzz

import config
from common import append_csv, delay, read_csv

FIELDS = [
    "reporting_unit_code", "name", "private", "latitude", "longitude",
    "lhn_name", "phn_name", "health_service_name", "domain",
    "tier", "resolution_method", "mx_ok",
]

# Known edge-case joins that fuzzy matching doesn't reliably catch.
# key = normalised LHN name; value = healthvic health_service_name
MANUAL_LHN_OVERRIDES = {
    "alfred health": "Bayside Health - Alfred Care Group",
    "peninsula health": "Bayside Health - Peninsula Care Group",
    "st vincent s hospital limited": "St Vincent's Health",
    "mercy public hospital inc": "Mercy Public Hospitals Inc.",
    "royal women s hospital": "The Royal Women's Hospital",
    "royal children s hospital": "The Royal Children's Hospital",
    "royal victorian eye and ear hospital": "The Royal Victorian Eye and Ear Hospital",
    "melbourne health": "Melbourne Health",
    "victorian institute of forensic mental health": "Forensicare",  # not in healthvic; will fall through to DDG
}

# Domains where the published website domain has no MX but a known sibling does.
# (website_domain → mail_domain)
MAIL_DOMAIN_REMAP = {
    "mercyhealth.com.au": "mercy.com.au",
}

# Name-keyword → tier hint (applied BEFORE public/private/segment defaults).
# These are the big tertiary centres where specialist-affinity ranking really matters.
TERTIARY_KEYWORDS = [
    "alfred", "royal melbourne", "monash medical centre", "royal children",
    "st vincent", "austin", "peter mac", "royal women", "eye and ear",
]

# Private hospital operators: keyword in name → domain.
# Ordered — longest/most-specific keywords first so they match before generic ones.
PRIVATE_OPERATOR_KEYWORDS = [
    ("epworth",            "epworth.org.au"),
    ("cabrini",            "cabrini.com.au"),
    ("st john of god",     "sjog.org.au"),
    ("st vincent's private", "svha.org.au"),
    ("beleura",            "ramsayhealth.com.au"),
    ("masada",             "ramsayhealth.com.au"),
    ("mitcham private",    "ramsayhealth.com.au"),
    ("knox private",       "ramsayhealth.com.au"),
    ("ringwood private",   "ramsayhealth.com.au"),
    ("peninsula private",  "ramsayhealth.com.au"),
    ("frances perry",      "ramsayhealth.com.au"),
    ("waverley private",   "ramsayhealth.com.au"),
    ("donvale rehabilitation", "healthscope.com.au"),
    ("dorset rehabilitation",  "healthscope.com.au"),
    ("geelong private",    "healthscope.com.au"),
    ("melbourne private",  "healthscope.com.au"),
    ("john fawkner",       "healthscope.com.au"),
    ("la trobe private",   "latrobeprivate.com.au"),
    ("holmesglen private", "healthscope.com.au"),
    ("bendigo day",        "bendigoprivatehospital.com.au"),
    ("delmont private",    "delmontprivate.com.au"),
    ("glen iris private",  "healthecare.com.au"),
    ("sir john monash",    "sjmph.com.au"),
    ("linacre private",    "healthecare.com.au"),
    ("warringal",          "healthecare.com.au"),
    ("brunswick private",  "brunswickprivatehospital.com.au"),
    ("jessie mcpherson",   "monashhealth.org"),
    ("albert road",        "ramsayhealth.com.au"),
    ("westernprivate",     "westernprivate.com.au"),
    ("western private",    "westernprivate.com.au"),
    ("wangaratta private", "ramsayhealth.com.au"),
    ("malvern private",    "malvernprivate.com.au"),
    ("vision eye",         "visioneyeinstitute.com.au"),
    ("vision day surgery", "visioneyeinstitute.com.au"),
]

# LHN-name → domain map. AIHW gives us LHN for public hospitals — much
# cleaner than name-based matching for non-VIC states.
LHN_DOMAINS = {
    # NSW — most LHD subdomains don't have MX; mail is centralized at health.nsw.gov.au
    # except for a few with their own mail (HNEH, SVHA, SCHN, JusticeHealth).
    "sydney": "health.nsw.gov.au",
    "south western sydney": "health.nsw.gov.au",
    "south eastern sydney": "health.nsw.gov.au",
    "northern sydney": "health.nsw.gov.au",
    "western sydney": "health.nsw.gov.au",
    "nepean blue mountains": "health.nsw.gov.au",
    "central coast": "health.nsw.gov.au",
    "hunter new england": "health.nsw.gov.au",
    "northern nsw": "health.nsw.gov.au",
    "mid north coast": "health.nsw.gov.au",
    "southern nsw": "health.nsw.gov.au",
    "murrumbidgee": "health.nsw.gov.au",
    "western nsw": "health.nsw.gov.au",
    "far west": "health.nsw.gov.au",
    "illawarra shoalhaven": "health.nsw.gov.au",
    "sydney children's hospitals network": "health.nsw.gov.au",
    "st vincent's health network": "svha.org.au",
    "justice health & forensic mental health": "health.nsw.gov.au",
    # QLD/SA/WA/TAS/NT LHN mappings — TODO when those state pilots run.
    # Each state's per-HHS subdomains need verification; using the umbrella
    # state-health domain produces wrong emails (firstname.lastname@health.qld.gov.au
    # is not a real QLD Health mailbox pattern).
}


# Public / other one-off overrides (by hospital-name substring)
PUBLIC_OVERRIDES = [
    ("royal dental hospital", "dhsv.org.au"),
    ("calvary health care bethlehem", "calvarycare.org.au"),
    ("bass coast health",      "basscoasthealth.com.au"),
    ("maryborough district",   "mdhs.vic.gov.au"),
    ("cohuna district",        "cohunahealth.com.au"),
    ("inglewood",              "idhs.vic.gov.au"),
    ("seymour district",       "seymourhealth.org.au"),
    ("gippsland southern",     "gshs.com.au"),
    ("kooweerup regional",     "kooweeruphealth.com.au"),
    ("queen elizabeth centre", "qec.org.au"),
    ("victorian institute of forensic", "forensicare.vic.gov.au"),
    ("dame phyllis frost",     "correctionshealth.vic.gov.au"),
    ("wyndham early parenting", "tweddle.org.au"),
    ("terang",                 "tmhs.vic.gov.au"),
    ("moyne health",           "moynehealth.vic.gov.au"),
    ("heywood rural",          "heywoodruralhealth.vic.gov.au"),
]


def match_by_keyword(name: str, private: bool, lhn_name: str = "") -> tuple[str, str]:
    """Returns (domain, method) by keyword match. Empty string if no match."""
    n = name.lower()
    # Public overrides first (catches edge cases regardless of private flag)
    for kw, dom in PUBLIC_OVERRIDES:
        if kw in n:
            return dom, f"public_override:{kw}"
    # LHN match for non-VIC public hospitals (AIHW gives us the LHN field)
    lhn_lc = (lhn_name or "").strip().lower()
    if lhn_lc and lhn_lc in LHN_DOMAINS:
        return LHN_DOMAINS[lhn_lc], f"lhn:{lhn_lc}"
    if private:
        for kw, dom in PRIVATE_OPERATOR_KEYWORDS:
            if kw in n:
                return dom, f"operator_kw:{kw}"
    return "", ""


def normalise(s: str) -> str:
    s = re.sub(r"\(.*?\)", "", s or "")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def domain_of(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.").lower()


def mx_exists(domain: str) -> bool:
    if not domain:
        return False
    try:
        ans = dns.resolver.resolve(domain, "MX", lifetime=10)
        return len(list(ans)) > 0
    except Exception:
        return False


# ── DuckDuckGo HTML fallback ─────────────────────────────────────────────────

_DDG_URL = "https://html.duckduckgo.com/html/"
_SKIP_HOSTS = (
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "instagram.com", "wikipedia.org", "yelp.com", "google.com", "yellowpages.com",
    "ratemds.com", "whitecoat.com.au", "ahpra.gov.au", "betterhealth.vic.gov.au",
    "myhospitals.gov.au", "healthdirect.gov.au", "duckduckgo.com", "bing.com",
    "tripadvisor.com", "updoc.com.au", "mable.com.au",
)


def ddg_search(query: str, k: int = 5) -> list[str]:
    """Return top result URLs from DuckDuckGo HTML endpoint."""
    try:
        r = requests.post(
            _DDG_URL,
            data={"q": query},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT_S,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if not href:
                continue
            # DDG uses redirects; the actual URL may be encoded in the href
            parsed = urlparse(href)
            if parsed.path == "/l/" and parsed.query:
                from urllib.parse import parse_qs, unquote
                qs = parse_qs(parsed.query)
                if "uddg" in qs:
                    href = unquote(qs["uddg"][0])
            if href.startswith("http"):
                links.append(href)
            if len(links) >= k:
                break
        return links
    except Exception as e:
        print(f"  [ddg] error: {e}")
        return []


def resolve_via_ddg(hospital_name: str) -> tuple[str, str]:
    """Return (domain, resolution_method) for a hospital by DDG search."""
    query = f'"{hospital_name}" Victoria Australia official site'
    urls = ddg_search(query, k=6)
    for url in urls:
        host = urlparse(url).hostname or ""
        if any(s in host for s in _SKIP_HOSTS):
            continue
        dom = domain_of(url)
        if dom and "." in dom:
            return dom, "ddg_search"
    return "", "ddg_search_failed"


# ── Tier ─────────────────────────────────────────────────────────────────────

def assign_tier(hospital_name: str, private: bool, hv_segment: str) -> str:
    n = hospital_name.lower()
    if any(k in n for k in TERTIARY_KEYWORDS):
        return "tertiary"
    if private:
        return "private"
    if hv_segment == "metro":
        return "metro_public"
    if hv_segment == "rural":
        return "rural_public"
    return "unknown"


# ── Main resolver ────────────────────────────────────────────────────────────

def load_healthvic_map() -> dict:
    """Returns dict keyed by normalised health_service_name → {name, domain, segment}."""
    out = {}
    for r in read_csv(config.DATA_DIR / "healthvic_directory.csv"):
        key = normalise(r["health_service_name"])
        out[key] = {
            "name": r["health_service_name"],
            "domain": r["domain"],
            "segment": r["segment"],
        }
    return out


def match_healthvic(lhn_name: str, hospital_name: str, hv_map: dict) -> tuple[dict | None, str]:
    """Return (healthvic_row_or_None, method)."""
    lhn_n = normalise(lhn_name)

    # 1. Manual override
    if lhn_n in MANUAL_LHN_OVERRIDES:
        override_name = MANUAL_LHN_OVERRIDES[lhn_n]
        override_key = normalise(override_name)
        if override_key in hv_map:
            return hv_map[override_key], "manual_override"

    # 2. Exact match on LHN
    if lhn_n and lhn_n in hv_map:
        return hv_map[lhn_n], "exact_lhn"

    # 3. Exact match on hospital name (in case hospital == health-service)
    hn = normalise(hospital_name)
    if hn and hn in hv_map:
        return hv_map[hn], "exact_hospital_name"

    # 4. Fuzzy on LHN → healthvic name
    if lhn_n:
        choices = list(hv_map.keys())
        best = process.extractOne(lhn_n, choices, scorer=fuzz.ratio)
        if best and best[1] >= 85:
            return hv_map[best[0]], f"fuzzy_lhn_{int(best[1])}"

    # 5. Fuzzy on hospital name → healthvic name
    if hn:
        choices = list(hv_map.keys())
        best = process.extractOne(hn, choices, scorer=fuzz.ratio)
        if best and best[1] >= 85:
            return hv_map[best[0]], f"fuzzy_hospital_{int(best[1])}"

    return None, "no_match"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Only process first N AIHW rows (dry-test). 0 = all.")
    ap.add_argument("--state", default=None)
    args = ap.parse_args()

    state = config.state_lc(args.state)
    print(f"[resolve] state: {state.upper()}")
    aihw = read_csv(config.hospitals_raw_csv(state))
    if args.limit:
        aihw = aihw[: args.limit]
    # healthvic_directory only exists for VIC; other states fall through to keyword/DDG
    hv_map = load_healthvic_map() if state == "vic" else {}
    print(f"[resolve] AIHW rows: {len(aihw)}, healthvic entries: {len(hv_map)}")

    resolved = []
    stats = {"matched_public": 0, "ddg_private": 0, "ddg_public_fallback": 0, "unresolved": 0}

    domain_mx_cache: dict[str, bool] = {}

    for i, h in enumerate(aihw, 1):
        name = h["name"]
        private = h["private"].lower() == "true"
        lhn = h.get("lhn_name", "")

        hv_row, method = match_healthvic(lhn, name, hv_map)

        if hv_row:
            domain = hv_row["domain"]
            hv_segment = hv_row["segment"]
            hv_name = hv_row["name"]
            stats["matched_public"] += 1
        else:
            # Try keyword-based operator/public/LHN map BEFORE hitting DDG.
            domain, method = match_by_keyword(name, private, lhn)
            hv_segment = ""
            hv_name = ""
            if domain:
                stats["keyword"] = stats.get("keyword", 0) + 1
            elif state == "vic":
                # DDG fallback only for VIC (DDG times out reliably elsewhere).
                # For other states, accept the gap — those hospitals stay unresolved.
                print(f"[resolve] ddg-fallback {i}/{len(aihw)}: {name}")
                domain, method = resolve_via_ddg(name)
                if domain:
                    stats["ddg_resolved"] = stats.get("ddg_resolved", 0) + 1
                else:
                    stats["unresolved"] = stats.get("unresolved", 0) + 1
                delay(*config.HTTP_JITTER_S)
            else:
                stats["unresolved"] = stats.get("unresolved", 0) + 1

        # Apply website→mail domain remap for known "A but no MX" cases
        if domain in MAIL_DOMAIN_REMAP:
            domain = MAIL_DOMAIN_REMAP[domain]
        # MX check, cached per domain
        if domain:
            if domain not in domain_mx_cache:
                domain_mx_cache[domain] = mx_exists(domain)
            mx_ok = domain_mx_cache[domain]
        else:
            mx_ok = False

        tier = assign_tier(name, private, hv_segment)

        resolved.append({
            "reporting_unit_code": h["reporting_unit_code"],
            "name": name,
            "private": private,
            "latitude": h["latitude"],
            "longitude": h["longitude"],
            "lhn_name": lhn,
            "phn_name": h.get("phn_name", ""),
            "health_service_name": hv_name,
            "domain": domain,
            "tier": tier,
            "resolution_method": method,
            "mx_ok": mx_ok,
        })

    out = config.hospitals_csv(state)
    if out.exists():
        out.unlink()
    append_csv(resolved, out, fieldnames=FIELDS)
    print()
    print(f"[resolve] wrote {out}")
    print(f"[resolve] stats: {stats}")
    print(f"[resolve] with domain      : {sum(1 for r in resolved if r['domain'])}/{len(resolved)}")
    print(f"[resolve] with valid MX    : {sum(1 for r in resolved if r['mx_ok'])}/{len(resolved)}")
    print(f"[resolve] distinct domains : {len({r['domain'] for r in resolved if r['domain']})}")

    from collections import Counter
    tiers = Counter(r["tier"] for r in resolved)
    print(f"[resolve] tiers            : {dict(tiers)}")


if __name__ == "__main__":
    main()
