"""
State-wide AHPRA scraper — no postcode/suburb filter.

Searches by STATE + GENDER + NAME-PREFIX to enumerate every registered
Medical Practitioner in each state, regardless of whether their address
appears in the suburb lookup.

Strategy:
  For each state × gender × name prefix:
    - If reported total ≤ 50   → collect and move on
    - If reported total > 50   → expand to next prefix length recursively
  Prefix depth adapts automatically: 1-char → 2-char → 3-char as needed.

Usage:
  python3 scraper_statewise.py --state QLD
  python3 scraper_statewise.py --state NSW
  python3 scraper_statewise.py --state ALL   (runs every state in sequence)
"""

import argparse, csv, os, random, string, time
from bs4 import BeautifulSoup
from scraper_state import new_session, canary_check, append_csv, parse, SEARCH_URL, CSV_FIELDS, MAX_RESULTS

DB_DIR      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db_ARPHA")
SCRAPER_DIR = os.path.dirname(os.path.abspath(__file__))

STATES = {
    "NSW": {"canary_suburb": "Sydney",    "canary_pc": 2000},
    "VIC": {"canary_suburb": "Melbourne", "canary_pc": 3000},
    "QLD": {"canary_suburb": "Brisbane",  "canary_pc": 4000},
    "SA":  {"canary_suburb": "Adelaide",  "canary_pc": 5000},
    "WA":  {"canary_suburb": "Perth",     "canary_pc": 6000},
    "TAS": {"canary_suburb": "Hobart",    "canary_pc": 7000},
    "NT":  {"canary_suburb": "Darwin",    "canary_pc": 800},
    "ACT": {"canary_suburb": "Canberra",  "canary_pc": 2600},
}

LETTERS = string.ascii_uppercase   # A-Z


def delay(lo=15.0, hi=25.0):
    time.sleep(random.uniform(lo, hi))


def progress_file(state):
    return os.path.join(SCRAPER_DIR, f"{state.lower()}_statewise_progress.txt")


def output_file(state):
    return os.path.join(DB_DIR, f"{state.lower()}_practitioners.csv")


def load_progress(state):
    done = set()
    pf = progress_file(state)
    if os.path.exists(pf):
        with open(pf) as f:
            for line in f:
                done.add(line.strip())
    return done


def save_progress(state, key):
    with open(progress_file(state), "a") as f:
        f.write(key + "\n")


def load_seen(state):
    seen = set()
    of = output_file(state)
    if os.path.exists(of):
        with open(of, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                seen.add(r["practitioner_id"])
    return seen


def query_ahpra(sess, state, gender="", name_prefix=""):
    """POST to AHPRA with state + optional gender + optional name prefix."""
    data = {
        "health-profession": "Medical Practitioner",
        "state": state, "suburb": "", "postcode": "",
        "suburb-postcode": "",
        "phonetic-suggested": "false", "phonetic-direct-match": "",
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
                delay(3, 5)
        except Exception as e:
            if attempt == 2:
                print(f"    Request failed: {e}", flush=True)
            time.sleep(5)
    return None


def wait_for_unblock(sess, state):
    cfg = STATES[state]
    wait = 90
    for attempt in range(10):
        print(f"  [Rate limited — waiting {wait:.0f}s (attempt {attempt+1})]", flush=True)
        time.sleep(wait)
        sess = new_session()
        if canary_check(sess, state, cfg["canary_suburb"], cfg["canary_pc"]):
            print("  [Unblocked — resuming]", flush=True)
            return sess
        wait = min(int(wait * 1.5), 600)
    print("  [Still blocked — continuing anyway]", flush=True)
    return new_session()


def collect_prefix(sess, state, gender, prefix, seen, of, done_set,
                   depth=0, max_depth=3, zero_streak=None):
    """
    Recursively collect practitioners for a given prefix.
    If total > MAX_RESULTS, expands to next prefix depth.
    Returns (added, sess, zero_streak_count).
    """
    if zero_streak is None:
        zero_streak = [0]

    key = f"{state}|{gender}|{prefix}"
    if key in done_set:
        return 0, sess, zero_streak

    html = query_ahpra(sess, state, gender=gender, name_prefix=prefix)
    if not html:
        return 0, sess, zero_streak

    rows, total = parse(html, prefix, state)  # postcode_searched field will hold the prefix

    if total == 0:
        zero_streak[0] += 1
        save_progress(state, key)
        done_set.add(key)
        # Check canary every 5 consecutive zeros
        if zero_streak[0] >= 5:
            if not canary_check(sess, state,
                                 STATES[state]["canary_suburb"],
                                 STATES[state]["canary_pc"]):
                print(f"  [Canary failed — rate limited]", flush=True)
                sess = wait_for_unblock(sess, state)
            zero_streak[0] = 0
        return 0, sess, zero_streak

    zero_streak[0] = 0

    # Collect results from this bucket first
    new_rows = [r for r in rows if r["practitioner_id"] not in seen]
    for r in new_rows:
        seen.add(r["practitioner_id"])
    append_csv(new_rows, of)
    added = len(new_rows)

    if added > 0:
        print(f"    [{gender or 'Any'}] {prefix or '(all)'}: +{added} new "
              f"(reported {total})", flush=True)

    save_progress(state, key)
    done_set.add(key)
    return added, sess, zero_streak


def scrape_state(state):
    cfg = STATES[state]
    of  = output_file(state)
    done_set = load_progress(state)
    seen     = load_seen(state)

    # Get AHPRA total for this state to show progress
    sess = new_session()
    time.sleep(3)

    print(f"\n{'='*60}", flush=True)
    print(f"  {state} — {len(seen):,} practitioners already collected", flush=True)
    print(f"{'='*60}\n", flush=True)

    total_added = 0

    for gender in ["Male", "Female"]:
        print(f"\n--- {state} | {gender} ---", flush=True)
        # Start at 2-char prefixes directly for large states (avoids wasting
        # queries on single-letter buckets that will always overflow >50)
        for letter1 in LETTERS:
            for letter2 in LETTERS:
                # Skip same-letter pairs (AA, BB...) — AHPRA matches them too
                # broadly, causing unbounded recursion with no clean prefix split
                if letter1 == letter2:
                    continue
                prefix = letter1 + letter2
                key = f"{state}|{gender}|{prefix}"
                if key in done_set:
                    continue
                delay()
                added, sess, _ = collect_prefix(
                    sess, state, gender, prefix, seen, of, done_set,
                )
                total_added += added

    # Catch any practitioners not captured by gender split
    # (e.g. "Not Stated" / non-binary gender registrations)
    print(f"\n--- {state} | No gender filter ---", flush=True)
    for letter in LETTERS:
        key = f"{state}||{letter}"
        if key in done_set:
            continue
        delay()
        added, sess, _ = collect_prefix(
            sess, state, "", letter, seen, of, done_set,
        )
        total_added += added

    print(f"\n[{state}] Done. Added {total_added:,} new. "
          f"Total in file: {len(seen):,}\n", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True,
                        help="State code (NSW, QLD, etc.) or ALL")
    args = parser.parse_args()

    if args.state.upper() == "ALL":
        for state in ["QLD", "WA", "SA", "TAS", "NSW", "VIC", "NT", "ACT"]:
            scrape_state(state)
    elif args.state.upper() in STATES:
        scrape_state(args.state.upper())
    else:
        print(f"Unknown state: {args.state}. Choose from: {list(STATES)} or ALL")


if __name__ == "__main__":
    main()
