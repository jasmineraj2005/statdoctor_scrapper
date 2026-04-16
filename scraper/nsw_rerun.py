"""
NSW Re-run — re-checks all zero-data postcodes, stops cleanly on rate limit.
Targets  : nsw_rerun_targets.txt
Output   : nsw_practitioners.csv (appends)
Progress : nsw_rerun_progress.txt

Run: python3 nsw_rerun.py
"""

import csv, os, random, time
import requests
from bs4 import BeautifulSoup
from scraper_state import (
    new_session, canary_check, get_suburbs, collect,
    load_seen, load_done, mark_done, CSV_FIELDS
)

TARGETS_FILE  = "nsw_rerun_targets.txt"
PROGRESS_FILE = "nsw_rerun_progress.txt"
OUTPUT_FILE   = "nsw_practitioners.csv"
STATE         = "NSW"
CANARY_SUBURB = "Sydney"
CANARY_PC     = 2000
ZERO_STREAK_THRESHOLD = 3


def delay(lo=1.5, hi=3.5):
    time.sleep(random.uniform(lo, hi))


def main():
    with open(TARGETS_FILE) as f:
        targets = [int(l.strip()) for l in f if l.strip().isdigit()]

    done      = load_done(PROGRESS_FILE)
    remaining = [p for p in targets if p not in done]
    seen      = load_seen(OUTPUT_FILE)

    print(f"NSW Re-run")
    print(f"Targets  : {len(targets)} postcodes")
    print(f"Remaining: {len(remaining)}")
    print(f"Existing practitioners: {len(seen):,}\n", flush=True)

    sess       = new_session()
    zero_streak = 0

    for i, pc in enumerate(remaining):
        suburbs = get_suburbs(sess, pc, STATE)
        if not suburbs:
            mark_done(pc, PROGRESS_FILE)
            continue

        names = [s["suburb"] for s in suburbs]
        print(f"[{i+1}/{len(remaining)}] {pc} -> {names}", flush=True)

        postcode_total = 0
        for loc in suburbs:
            delay()
            try:
                added, total = collect(sess, loc["suburb"], pc, STATE, seen, OUTPUT_FILE)
                postcode_total += total
            except Exception as e:
                print(f"  ERROR {loc['suburb']}: {e}", flush=True)

        mark_done(pc, PROGRESS_FILE)

        if postcode_total == 0:
            zero_streak += 1
            if zero_streak >= ZERO_STREAK_THRESHOLD:
                if not canary_check(sess, STATE, CANARY_SUBURB, CANARY_PC):
                    print(f"\n  [IP RATE LIMITED — stopping cleanly]", flush=True)
                    print(f"  Completed {i+1}/{len(remaining)} postcodes this run.", flush=True)
                    print(f"  Restart with a fresh IP: python3 nsw_rerun.py", flush=True)
                    raise SystemExit(0)
                zero_streak = 0
        else:
            zero_streak = 0

        delay(20.0, 35.0)

    print(f"\nNSW Re-run complete! {len(seen):,} unique practitioners -> {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
