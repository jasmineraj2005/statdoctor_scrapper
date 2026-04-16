"""
Verification script — spot-checks dense suburbs against live AHPRA counts.
For any suburb where we have >= 50 practitioners, query AHPRA and compare
the reported total vs what we scraped. Reports gaps.
"""

import csv, os, time, random, sys
from collections import defaultdict
import requests
from bs4 import BeautifulSoup
from scraper_state import new_session, SEARCH_URL, BASE_URL

BASE = os.path.dirname(os.path.abspath(__file__))

STATE_FILES = {
    "NSW": "nsw_practitioners.csv",
    "QLD": "qld_practitioners.csv",
    "SA":  "sa_practitioners.csv",
    "WA":  "wa_practitioners.csv",
    "TAS": "tas_practitioners.csv",
}

# Also check some zero-result postcodes to confirm they're truly empty
ZERO_SAMPLE_SIZE = 30


def delay():
    time.sleep(random.uniform(2.0, 3.5))


def get_ahpra_count(sess, suburb, postcode, state):
    """Return (total_reported, actual_rows_on_page) from AHPRA for a suburb."""
    data = {
        "health-profession": "Medical Practitioner",
        "state": state, "suburb": suburb, "postcode": str(postcode),
        "suburb-postcode": f"{suburb}, {state}, {postcode}",
        "phonetic-suggested": "true", "phonetic-direct-match": "",
        "event-category": "Practitioner Search",
        "event-action": "On Search Results page",
        "name-reg": "", "name-reg-detail": "",
        "sex-filters": "", "language-filters": "",
        "load-more-page-num": "", "search-results-count": "",
        "practitioner-row-id": "",
    }
    try:
        r = sess.post(SEARCH_URL, data=data, timeout=30)
        if r.status_code != 200:
            return None, None
        soup = BeautifulSoup(r.text, "lxml")
        c = soup.find("input", {"name": "search-results-count"})
        total = int(c["value"]) if c and c.get("value", "").isdigit() else 0
        rows_on_page = len(soup.find_all("div", attrs={"data-practitioner-row-id": True}))
        return total, rows_on_page
    except Exception as e:
        print(f"    ERROR querying {suburb}: {e}")
        return None, None


def parse_location(location):
    parts = [p.strip() for p in location.rsplit(",", 2)]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return location, "", ""


def main():
    # Build suburb -> scraped count map
    suburb_scraped = {}  # (suburb, state, postcode) -> set of practitioner_ids
    state_zero_pcs = {}  # state -> list of zero-result postcodes

    for state, fname in STATE_FILES.items():
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            continue
        pc_counts = defaultdict(set)
        loc_ids = defaultdict(set)
        with open(path) as f:
            for r in csv.DictReader(f):
                pc_counts[int(r["postcode_searched"])].add(r["practitioner_id"])
                suburb, st, pc = parse_location(r["location"])
                loc_ids[(suburb, st, pc)].add(r["practitioner_id"])

        for key, ids in loc_ids.items():
            suburb_scraped[key] = ids

        # Zero-result postcodes (done but no rows in CSV)
        prog_file = os.path.join(BASE, f"{state.lower()}_scrape_progress.txt")
        with open(prog_file) as f:
            done = set(int(l.strip()) for l in f if l.strip().isdigit())
        zeros = [pc for pc in done if pc not in pc_counts]
        state_zero_pcs[state] = zeros

    # ---- Phase 1: Check dense suburbs ----
    dense = [(k, v) for k, v in suburb_scraped.items() if len(v) >= 50]
    dense.sort(key=lambda x: -len(x[1]))

    print(f"=== Phase 1: Checking {len(dense)} dense suburbs against live AHPRA ===\n")
    sess = new_session()
    gaps = []

    for i, ((suburb, state, postcode), scraped_ids) in enumerate(dense):
        delay()
        total_reported, _ = get_ahpra_count(sess, suburb, postcode, state)
        if total_reported is None:
            print(f"  [{i+1}/{len(dense)}] {suburb}, {state} {postcode} — QUERY FAILED")
            continue

        scraped = len(scraped_ids)
        gap = total_reported - scraped
        flag = " *** MISSING DATA" if gap > 5 else (" (ok)" if gap <= 0 else f" (minor diff: {gap})")
        print(f"  [{i+1}/{len(dense)}] {suburb}, {state} {postcode}: AHPRA={total_reported}, scraped={scraped}, diff={gap}{flag}")

        if gap > 5:
            gaps.append({
                "suburb": suburb, "state": state, "postcode": postcode,
                "ahpra_total": total_reported, "scraped": scraped, "gap": gap,
            })

    # ---- Phase 2: Spot-check zero-result postcodes ----
    print(f"\n=== Phase 2: Spot-checking zero-result postcodes ===\n")

    from scraper_state import get_suburbs
    false_zeros = []

    for state, zeros in state_zero_pcs.items():
        sample = zeros[:ZERO_SAMPLE_SIZE]
        print(f"{state}: checking {len(sample)} of {len(zeros)} zero-result postcodes...")
        for pc in sample:
            delay()
            suburbs_found = get_suburbs(sess, pc, state)
            if suburbs_found:
                # Check if any have results
                for loc in suburbs_found[:2]:  # spot check first 2 suburbs
                    delay()
                    total, _ = get_ahpra_count(sess, loc["suburb"], pc, state)
                    if total and total > 0:
                        print(f"  *** FALSE ZERO: {pc} -> {loc['suburb']}: {total} practitioners on AHPRA!")
                        false_zeros.append({"state": state, "postcode": pc, "suburb": loc["suburb"], "ahpra_total": total})
                        break

    # ---- Summary ----
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    if gaps:
        print(f"\n{len(gaps)} suburbs with significant missing data (gap > 5):")
        for g in sorted(gaps, key=lambda x: -x["gap"]):
            print(f"  {g['suburb']}, {g['state']} {g['postcode']}: missing ~{g['gap']} practitioners")
        # Save gaps report
        with open(os.path.join(BASE, "coverage_gaps.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["suburb", "state", "postcode", "ahpra_total", "scraped", "gap"])
            w.writeheader()
            w.writerows(sorted(gaps, key=lambda x: -x["gap"]))
        print(f"\n  → Saved to coverage_gaps.csv")
    else:
        print("\nNo significant gaps found in dense suburbs.")

    if false_zeros:
        print(f"\n{len(false_zeros)} postcodes incorrectly recorded as empty:")
        for z in false_zeros:
            print(f"  {z['state']} {z['postcode']} ({z['suburb']}): {z['ahpra_total']} practitioners")
    else:
        print("No false-zero postcodes found.")


if __name__ == "__main__":
    main()
