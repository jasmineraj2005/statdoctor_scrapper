"""
STEP 4 (v3) — GP resolver via Halaxy public sitemap.

Halaxy exposes all practitioner profile URLs via 30 sitemap XML files
(public, robots-allowed). One-time bulk download → local name matching →
per-GP profile fetch → extract clinic from JSON-LD.

No search engines. No captcha. Bulk HTTP only.

Pipeline:
  1. Download all 30 practitioner sitemaps (60 HTTP requests)
  2. Build index: name-slug → [profile URLs]
  3. For each VIC GP: match by name-slug → fetch Halaxy profile
  4. Parse JSON-LD for clinic_name, address, phone

Output:
  data/halaxy_sitemap_index.json — cached full URL index (reusable)
  data/gp_practices.csv          — per-practitioner clinic info

Usage:
  python gp_resolver_sitemap.py --build-index      # Phase 0: download sitemaps
  python gp_resolver_sitemap.py --sample 10        # Phase 1: dry test
  python gp_resolver_sitemap.py --all              # Full run
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config
from common import parse_name, read_csv

THIS_DIR = Path(__file__).resolve().parent
DATA_DIR = THIS_DIR / "data"
SITEMAP_INDEX_JSON = DATA_DIR / "halaxy_sitemap_index.json"
# gp_practices CSV is per-state — accessed via config.gp_practices_csv(state)

SITEMAP_INDEX_URL = "https://www.halaxy.com/a/sitemap"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

FIELDS = [
    "practitioner_id", "first", "last", "suburb", "postcode",
    "halaxy_url", "halaxy_specialty",
    "clinic_name", "clinic_phone", "clinic_street", "clinic_suburb", "clinic_postcode",
    "method", "scraped_at", "notes",
]

GP_SPECIALTY_KEYWORDS = ("gp-general-practitioner", "general-practitioner",
                         "gp-", "general-practice")


# ── Session ──────────────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-AU,en;q=0.9",
    })
    return s


def polite_sleep():
    time.sleep(random.uniform(0.8, 1.8))


# ── Phase 0: sitemap download ────────────────────────────────────────────────
def build_sitemap_index(sess: requests.Session) -> dict:
    """Download index, then all 30 practitioner sitemaps. Return {name_slug: [urls]}."""
    print(f"[idx] fetching sitemap index: {SITEMAP_INDEX_URL}")
    r = sess.get(SITEMAP_INDEX_URL, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "xml")
    sitemap_urls = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    pract_sitemaps = [u for u in sitemap_urls if "/practitioners/" in u]
    print(f"[idx] practitioner sitemaps found: {len(pract_sitemaps)}")

    index: dict[str, list[dict]] = {}
    total_urls = 0
    total_gp = 0

    for i, sm_url in enumerate(pract_sitemaps, 1):
        try:
            r = sess.get(sm_url, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"[idx] ({i}/{len(pract_sitemaps)}) ERROR {sm_url}: {e}")
            continue
        s = BeautifulSoup(r.text, "xml")
        urls = [loc.get_text(strip=True) for loc in s.find_all("loc")]
        total_urls += len(urls)

        for u in urls:
            # Pattern: /profile/{honorific-first-last}/{speciality-slug}/{id}
            m = re.match(r"https://www\.halaxy\.com/profile/([^/]+)/([^/]+)/(\d+)", u)
            if not m:
                continue
            name_part, specialty, pid = m.group(1), m.group(2), m.group(3)
            # Keep ONLY GP entries
            if not any(k in specialty for k in GP_SPECIALTY_KEYWORDS):
                continue
            total_gp += 1
            # Strip honorific (dr-, mr-, mrs-, ms-, miss-, prof-, assoc-prof-, etc.)
            parts = name_part.split("-")
            # Drop leading honorific tokens
            honorifics = {"dr", "mr", "mrs", "ms", "miss", "prof", "professor",
                          "assoc", "associate", "clinical", "adj", "adjunct",
                          "sir", "dame", "rev"}
            while parts and parts[0].lower() in honorifics:
                parts = parts[1:]
            if len(parts) < 2:
                continue
            first_slug = parts[0].lower()
            last_slug = parts[-1].lower()  # multi-middle-names: last token = surname
            key = f"{first_slug}|{last_slug}"
            entry = {"url": u, "specialty": specialty, "id": pid, "full_slug": name_part}
            index.setdefault(key, []).append(entry)

        if i % 5 == 0 or i == len(pract_sitemaps):
            print(f"[idx] ({i}/{len(pract_sitemaps)}) scanned — total URLs: {total_urls}, GP entries: {total_gp}, unique names: {len(index)}")
        polite_sleep()

    print(f"[idx] done. total URLs scanned: {total_urls}  GP profile entries: {total_gp}  unique (first|last): {len(index)}")
    return index


# ── Phase 1: per-GP profile fetch ────────────────────────────────────────────
def slugify_name(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def find_halaxy_entry(first: str, last: str, suburb: str, raw_name: str,
                      index: dict) -> tuple[dict | None, str]:
    """
    Return (entry, match_method). Cascade:
      1. exact (first, last)
      2. (first-initial, last) — handles AHPRA multi-initials stored as 'A Bhuiyan'
      3. last-only IF unique in index (one candidate nationally)
    """
    fs, ls = slugify_name(first), slugify_name(last)

    # 1. exact
    key = f"{fs}|{ls}"
    entries = index.get(key)
    if entries:
        return entries[0], "exact"

    # 2. first-initial + last
    if fs:
        initial_key = f"{fs[0]}|{ls}"
        entries = index.get(initial_key)
        if entries:
            return entries[0], "initial"

    # 2b. Try other tokens in raw AHPRA name as potential first names
    # ("Dr A K M Fakhruzzaman Bhuiyan" — try 'a', 'k', 'm', 'fakhruzzaman' against |bhuiyan)
    tokens = re.findall(r"[A-Za-z]+", raw_name.lower())
    honorifics = {"dr", "mr", "mrs", "ms", "miss", "prof", "professor",
                  "assoc", "associate", "clinical", "adj", "adjunct"}
    tokens = [t for t in tokens if t not in honorifics and t != ls]
    for tok in tokens:
        candidate_key = f"{tok}|{ls}"
        if candidate_key in index:
            return index[candidate_key][0], f"token:{tok}"
        if len(tok) == 1:
            continue
        # Also try first-letter of this token
        candidate_key = f"{tok[0]}|{ls}"
        if candidate_key in index:
            return index[candidate_key][0], f"token_initial:{tok[0]}"

    # 3. last-only if unique
    last_matches = [k for k in index if k.endswith(f"|{ls}")]
    if len(last_matches) == 1:
        return index[last_matches[0]][0], "last_unique"

    return None, "none"


def fetch_profile_and_extract(sess: requests.Session, url: str) -> dict:
    """Fetch a Halaxy profile URL, return parsed clinic fields from JSON-LD."""
    out = {"ok": False, "notes": ""}
    try:
        r = sess.get(url, timeout=25)
        if r.status_code != 200:
            out["notes"] = f"http_{r.status_code}"
            return out
        html = r.text
    except Exception as e:
        out["notes"] = f"fetch_err:{type(e).__name__}"
        return out

    jsonld = None
    for m in re.finditer(r'<script type="application/ld\+json">(.+?)</script>', html, re.DOTALL):
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict) and d.get("@type") == "Person":
                jsonld = d
                break
        except Exception:
            continue

    if not jsonld:
        out["notes"] = "no_jsonld"
        return out

    works = jsonld.get("worksFor") or {}
    addr = jsonld.get("address") or works.get("address") or {}
    out["ok"] = True
    out["clinic_name"]     = (works.get("name") or "").strip()
    out["clinic_phone"]    = (jsonld.get("telephone") or works.get("telephone") or "").strip()
    out["clinic_street"]   = (addr.get("streetAddress") or works.get("streetAddress") or "").strip()
    out["clinic_suburb"]   = (addr.get("addressLocality") or works.get("addressLocality") or "").strip()
    out["clinic_postcode"] = (addr.get("postalCode") or works.get("postalCode") or "").strip()
    return out


# ── Resume/IO ────────────────────────────────────────────────────────────────
def load_done(state: str) -> set[str]:
    p = config.gp_practices_csv(state)
    if not p.exists():
        return set()
    with open(p, newline="", encoding="utf-8") as f:
        return {r["practitioner_id"] for r in csv.DictReader(f) if r.get("practitioner_id")}


def append_row(row: dict, state: str) -> None:
    p = config.gp_practices_csv(state)
    exists = p.exists()
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)


def load_index() -> dict:
    if not SITEMAP_INDEX_JSON.exists():
        return {}
    return json.loads(SITEMAP_INDEX_JSON.read_text())


# ── Main ─────────────────────────────────────────────────────────────────────
def run_index_build():
    sess = make_session()
    index = build_sitemap_index(sess)
    SITEMAP_INDEX_JSON.write_text(json.dumps(index, indent=2))
    print(f"[idx] wrote {SITEMAP_INDEX_JSON}  ({SITEMAP_INDEX_JSON.stat().st_size:,} bytes)")


def run_resolve(state: str, limit: int | None):
    index = load_index()
    if not index:
        print("[!] No sitemap index. Run --build-index first.")
        return
    print(f"[gp] state: {state.upper()}")
    print(f"[gp] loaded sitemap index: {len(index)} unique names, "
          f"{sum(len(v) for v in index.values())} total GP URLs")

    done = load_done(state)
    pracs = read_csv(config.practitioners_csv(state))
    candidates = [
        r for r in pracs
        if "general practice" in (r.get("speciality") or "").lower()
        and r["practitioner_id"] not in done
    ]
    print(f"[gp] {state.upper()} GPs to resolve: {len(candidates)} (already done: {len(done)})")
    if limit:
        candidates = candidates[:limit]
    print(f"[gp] processing {len(candidates)} this run")

    sess = make_session()
    stats = {"matched": 0, "with_clinic": 0, "no_match": 0, "fetch_fail": 0, "no_jsonld": 0}

    for i, r in enumerate(candidates, 1):
        first, last = parse_name(r["name"])
        loc = r.get("location", "")
        suburb = loc.split(",")[0].strip() if "," in loc else ""
        postcode = r.get("postcode_searched", "")

        row = {
            "practitioner_id": r["practitioner_id"], "first": first, "last": last,
            "suburb": suburb, "postcode": postcode,
            "halaxy_url": "", "halaxy_specialty": "",
            "clinic_name": "", "clinic_phone": "", "clinic_street": "",
            "clinic_suburb": "", "clinic_postcode": "",
            "method": "", "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "notes": "",
        }

        if not first or not last:
            row["method"] = "skip_no_name"
            append_row(row, state)
            continue

        entry, match_method = find_halaxy_entry(first, last, suburb, r["name"], index)
        if not entry:
            row["method"] = "no_sitemap_match"
            stats["no_match"] += 1
            append_row(row, state)
            if i % 50 == 0:
                print(f"  [{i}/{len(candidates)}] {stats}")
            continue

        row["halaxy_url"] = entry["url"]
        row["halaxy_specialty"] = entry["specialty"]
        row["notes"] = f"match={match_method}"

        # Fetch profile
        parsed = fetch_profile_and_extract(sess, entry["url"])
        polite_sleep()

        if not parsed["ok"]:
            row["method"] = f"halaxy_{parsed.get('notes','fail')}"
            if "fetch_err" in parsed.get("notes", ""):
                stats["fetch_fail"] += 1
            else:
                stats["no_jsonld"] += 1
            append_row(row, state)
            continue

        clinic_postcode = parsed.get("clinic_postcode", "")
        # AU postcode-prefix → state map. Reject fuzzy matches whose Halaxy
        # clinic postcode is in a different state. Trust exact name matches
        # even cross-state (doctor may have relocated).
        STATE_PREFIXES = {
            "nsw": ("1", "2"), "act": ("0", "2"),
            "vic": ("3", "8"), "qld": ("4", "9"),
            "sa": ("5",), "wa": ("6",), "tas": ("7",), "nt": ("0",),
        }
        prefixes = STATE_PREFIXES.get(state, ())
        in_state = (clinic_postcode[:1] in prefixes) if clinic_postcode else True
        if not in_state and match_method != "exact":
            # Wrong doctor — demote to no-match
            row["method"] = "wrong_state_fuzzy_reject"
            row["notes"] += f"|halaxy_postcode={clinic_postcode}"
            stats["no_match"] += 1
            append_row(row, state)
            if i % 25 == 0:
                print(f"  [{i}/{len(candidates)}] {stats}")
            continue

        row["clinic_name"]     = parsed.get("clinic_name", "")
        row["clinic_phone"]    = parsed.get("clinic_phone", "")
        row["clinic_street"]   = parsed.get("clinic_street", "")
        row["clinic_suburb"]   = parsed.get("clinic_suburb", "")
        row["clinic_postcode"] = clinic_postcode
        sub_ok = (not suburb) or (not row["clinic_suburb"]) or (
            suburb.lower() in row["clinic_suburb"].lower()
            or row["clinic_suburb"].lower() in suburb.lower()
        )
        row["method"] = "halaxy_sitemap" if row["clinic_name"] else "halaxy_no_clinic"
        if not sub_ok:
            row["notes"] += f"|suburb_mismatch:ahpra={suburb}/halaxy={row['clinic_suburb']}"

        stats["matched"] += 1
        if row["clinic_name"]:
            stats["with_clinic"] += 1
        append_row(row, state)

        if i % 25 == 0 or i == len(candidates):
            print(f"  [{i}/{len(candidates)}] {stats}")

    print(f"\n[gp] DONE. {stats}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-index", action="store_true",
                    help="Phase 0: download Halaxy sitemaps and build local GP index.")
    ap.add_argument("--sample", type=int, default=10, help="Limit rows (default 10).")
    ap.add_argument("--all", action="store_true", help="Process all remaining GPs.")
    ap.add_argument("--state", default=None, help="vic | nsw | qld | sa | wa | nt")
    args = ap.parse_args()

    if args.build_index:
        run_index_build()
    else:
        state = config.state_lc(args.state)
        run_resolve(state, limit=None if args.all else args.sample)


if __name__ == "__main__":
    main()
