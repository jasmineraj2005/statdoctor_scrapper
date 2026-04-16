"""
Fix two classes of gaps found by verify_coverage.py:

1. Dense suburb overflow — suburbs where even the Male/Female gender split
   capped at 50. Fix: iterate A-Z name prefixes within each gender; recurse
   to 2-char prefixes for any bucket still >50.

2. False-zero postcodes — postcodes incorrectly recorded as empty due to
   rate-limiting during the original run. Fix: re-scrape them normally.

Reads:  coverage_gaps.csv  (dense suburbs to fix)
Writes: appends to existing state CSVs  (e.g. nsw_practitioners.csv)
        fix_gaps_progress.txt           (resume support)
"""

import csv, os, random, string, time
from collections import defaultdict

from bs4 import BeautifulSoup
from scraper_state import (
    new_session, canary_check, get_suburbs, collect,
    load_seen, mark_done, append_csv, parse, CSV_FIELDS,
    SEARCH_URL, MAX_RESULTS,
)

SCRAPER_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(SCRAPER_DIR, "..", "db_ARPHA")  # CSVs live here
PROGRESS_FILE = os.path.join(SCRAPER_DIR, "fix_gaps_progress.txt")

# False-zero postcodes found by verify_coverage.py
FALSE_ZEROS = [
    ("NSW", "nsw_practitioners.csv", 2190, "Chullora",  "Sydney", 2000),
    ("QLD", "qld_practitioners.csv", 4132, "Marsden",   "Brisbane", 4000),
    ("QLD", "qld_practitioners.csv", 4133, "Waterford", "Brisbane", 4000),
    ("QLD", "qld_practitioners.csv", 4151, "Coorparoo", "Brisbane", 4000),
    ("QLD", "qld_practitioners.csv", 4152, "Carina",    "Brisbane", 4000),
    ("QLD", "qld_practitioners.csv", 4153, "Belmont",   "Brisbane", 4000),
    ("QLD", "qld_practitioners.csv", 4154, "Gumdale",   "Brisbane", 4000),
    ("QLD", "qld_practitioners.csv", 4155, "Chandler",  "Brisbane", 4000),
    ("SA",  "sa_practitioners.csv",  5007, "Bowden",    "Adelaide", 5000),
    ("TAS", "tas_practitioners.csv", 7004, "South Hobart", "Hobart", 7000),
    ("TAS", "tas_practitioners.csv", 7007, "Mount Nelson",  "Hobart", 7000),
]


def delay(lo=4.0, hi=7.0):
    time.sleep(random.uniform(lo, hi))


def load_progress():
    done = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            for line in f:
                done.add(line.strip())
    return done


def save_progress(key):
    with open(PROGRESS_FILE, "a") as f:
        f.write(key + "\n")


def query_ahpra(sess, suburb, postcode, state, gender="", name_prefix=""):
    data = {
        "health-profession": "Medical Practitioner",
        "state": state, "suburb": suburb, "postcode": str(postcode),
        "suburb-postcode": f"{suburb}, {state}, {postcode}",
        "phonetic-suggested": "true", "phonetic-direct-match": "",
        "event-category": "Practitioner Search",
        "event-action": "On Search Results page",
        "name-reg": name_prefix, "name-reg-detail": "",
        "sex-filters": "", "language-filters": "",
        "load-more-page-num": "", "search-results-count": "",
        "practitioner-row-id": "",
    }
    if gender:
        data["gender-select"] = gender
    for attempt in range(3):
        try:
            r = sess.post(SEARCH_URL, data=data, timeout=30)
            if r.status_code == 200:
                return r.text
            if r.status_code == 403:
                sess.cookies.clear()
                sess.get(SEARCH_URL, timeout=20)
                delay()
        except Exception as e:
            if attempt == 2:
                print(f"    Request failed: {e}")
            time.sleep(5)
    return None


def scrape_dense_suburb(sess, suburb, postcode, state, output_file, seen, progress_done):
    """
    Exhaustively scrape a suburb using gender × name-prefix splits.
    Recursively uses 2-char prefixes if a single-letter bucket is still >50.
    """
    added_total = 0

    for gender in ["Male", "Female"]:
        for letter in string.ascii_uppercase:
            key = f"dense|{state}|{postcode}|{gender}|{letter}"
            if key in progress_done:
                continue

            delay()
            html = query_ahpra(sess, suburb, postcode, state,
                                gender=gender, name_prefix=letter)
            if not html:
                continue

            rows, total = parse(html, postcode, state)

            if total > MAX_RESULTS:
                # Go to 2-char prefixes
                print(f"    {gender}+{letter}: {total} reported — expanding to 2-char prefixes", flush=True)
                for letter2 in string.ascii_uppercase:
                    prefix2 = letter + letter2
                    key2 = f"dense|{state}|{postcode}|{gender}|{prefix2}"
                    if key2 in progress_done:
                        continue
                    delay()
                    html2 = query_ahpra(sess, suburb, postcode, state,
                                        gender=gender, name_prefix=prefix2)
                    if not html2:
                        continue
                    rows2, total2 = parse(html2, postcode, state)
                    new2 = [r for r in rows2 if r["practitioner_id"] not in seen]
                    for r in new2:
                        seen.add(r["practitioner_id"])
                    append_csv(new2, output_file)
                    added_total += len(new2)
                    save_progress(key2)
                    progress_done.add(key2)
                    if new2:
                        print(f"      {gender}+{prefix2}: +{len(new2)} new (total reported {total2})", flush=True)
            else:
                new = [r for r in rows if r["practitioner_id"] not in seen]
                for r in new:
                    seen.add(r["practitioner_id"])
                append_csv(new, output_file)
                added_total += len(new)
                if new:
                    print(f"    {gender}+{letter}: +{len(new)} new (of {total})", flush=True)

            save_progress(key)
            progress_done.add(key)

    return added_total


def fix_dense_suburbs(sess, progress_done):
    gaps_file = os.path.join(BASE, "coverage_gaps.csv")
    if not os.path.exists(gaps_file):
        print("No coverage_gaps.csv found — skipping dense suburb fix.")
        return

    with open(gaps_file) as f:
        gaps = list(csv.DictReader(f))

    print(f"\n{'='*60}")
    print(f"Phase 1: Fixing {len(gaps)} dense suburbs")
    print(f"{'='*60}\n")

    for g in gaps:
        suburb  = g["suburb"]
        state   = g["state"]
        postcode = int(g["postcode"])
        ahpra_total = int(g["ahpra_total"])
        already_scraped = int(g["scraped"])
        output_file = os.path.join(BASE, f"{state.lower()}_practitioners.csv")

        print(f"\n[{suburb}, {state} {postcode}] AHPRA={ahpra_total}, have={already_scraped}, gap=~{ahpra_total - already_scraped}")

        seen = load_seen_from_file(output_file)
        before = len(seen)

        added = scrape_dense_suburb(sess, suburb, postcode, state,
                                     output_file, seen, progress_done)

        print(f"  → Added {added} new practitioners (total in file: {len(seen)})", flush=True)

        # Brief canary check between suburbs
        delay(8, 14)
        if not canary_check(sess, state, suburb, postcode):
            print("  [Rate limited — waiting]", flush=True)
            sess = wait_for_unblock_simple(sess, state, suburb, postcode)


def load_seen_from_file(output_file):
    seen = set()
    if os.path.exists(output_file):
        with open(output_file, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                seen.add(r["practitioner_id"])
    return seen


def wait_for_unblock_simple(sess, state, suburb, postcode, max_wait=600):
    wait = 90
    for _ in range(8):
        print(f"  Waiting {wait}s...", flush=True)
        time.sleep(wait)
        sess = new_session()
        if canary_check(sess, state, suburb, postcode):
            print("  Unblocked.", flush=True)
            return sess
        wait = min(int(wait * 1.5), max_wait)
    return new_session()


def fix_false_zeros(sess, progress_done):
    print(f"\n{'='*60}")
    print(f"Phase 2: Re-scraping {len(FALSE_ZEROS)} false-zero postcodes")
    print(f"{'='*60}\n")

    for state, csv_fname, postcode, suburb_hint, canary_suburb, canary_pc in FALSE_ZEROS:
        key = f"falzero|{state}|{postcode}"
        if key in progress_done:
            print(f"  {state} {postcode}: already done — skip")
            continue

        output_file = os.path.join(BASE, csv_fname)
        seen = load_seen_from_file(output_file)

        suburbs = get_suburbs(sess, postcode, state)
        if not suburbs:
            print(f"  {state} {postcode}: no suburbs found", flush=True)
            save_progress(key)
            progress_done.add(key)
            continue

        names = [s["suburb"] for s in suburbs]
        print(f"\n  {state} {postcode} -> {names}", flush=True)

        postcode_total = 0
        for loc in suburbs:
            delay()
            try:
                added, total = collect(sess, loc["suburb"], postcode, state, seen, output_file)
                postcode_total += total
                if added:
                    print(f"    {loc['suburb']}: +{added} new", flush=True)
            except Exception as e:
                print(f"    ERROR {loc['suburb']}: {e}", flush=True)

        save_progress(key)
        progress_done.add(key)
        delay(8, 12)


def main():
    progress_done = load_progress()
    sess = new_session()

    fix_dense_suburbs(sess, progress_done)
    fix_false_zeros(sess, progress_done)

    print(f"\n{'='*60}")
    print("All gap fixes complete.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
