#!/usr/bin/env python3
"""Drive test: scan run logs for `name_mismatch` and `name_low_confidence`
rejections that, under v2.1.1 (Fix A), would now be RESCUED into a 'medium'
match because the search-card carries a medical signal.

These are real doctors we missed and should reconsider.

Output: a list of {practitioner_id, ahpra_name, linkedin_url (if extractable),
linkedin_name, headline, why_rejected, would_rescue_reason}.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from collections import defaultdict

import config
import verifier

LOG_FILES = [
    "/tmp/day2_run.log",
    "/tmp/day3_run.log",
    "/tmp/day3b_run.log",
    "/tmp/day3v3_run.log",
    "/tmp/day3v3b_run.log",
]

PROC_LINE = re.compile(r"^\[main\] ── (?P<name>.+?) \((?P<pid>MED\d+)\) ──")
RESULT_LINE = re.compile(
    r"\[search\] Result \d+: '(?P<lname>[^']*)' \| '(?P<loc>[^']*)' \| "
    r"'(?P<headline>[^']*)' — (?P<reason>.+)$"
)
ARROW_MATCH  = re.compile(r"^\s*→ matched")
ARROW_NOMATCH = re.compile(r"^\s*→ no LinkedIn match")


def parse_practitioner_meta() -> dict[str, dict]:
    out = {}
    import csv
    with open(config.INPUT_SUBSET_CSV) as f:
        for r in csv.DictReader(f):
            out[r["practitioner_id"]] = r
    return out


def walk_logs():
    """Yield (pid, ahpra_name, ahpra_speciality, all_results) per
    practitioner block. all_results is a list of (lname, loc, headline,
    reason_string) for every search result line in the block."""
    meta = parse_practitioner_meta()
    for path in LOG_FILES:
        p = Path(path)
        if not p.exists():
            continue
        cur = None
        results: list[tuple] = []
        with open(p) as f:
            for line in f:
                pm = PROC_LINE.search(line)
                if pm:
                    if cur is not None:
                        yield cur, results, meta.get(cur, {})
                    cur = pm.group("pid")
                    results = []
                    continue
                if cur is None:
                    continue
                rm = RESULT_LINE.search(line)
                if rm:
                    results.append((rm.group("lname"), rm.group("loc"),
                                    rm.group("headline"), rm.group("reason")))
                    continue
            if cur is not None:
                yield cur, results, meta.get(cur, {})


def main():
    rescued: list[dict] = []
    seen_pids: set[str] = set()  # don't double-count across multiple log files

    # Mock practitioner lookup to call _card_has_medical_rescue without
    # needing the full row.
    def mk_pract(meta_row):
        return {
            "name": meta_row.get("name", ""),
            "specialities": meta_row.get("speciality", ""),
        }

    for pid, results, meta in walk_logs():
        if not meta or pid in seen_pids:
            continue
        # Only consider blocks that did NOT match any result.
        rejected_reasons = {r[3].split(" ")[0] for r in results}
        # If at least one result matched, the verifier already accepted —
        # nothing to rescue.
        if any(r[3].startswith("matched") for r in results):
            continue

        # Walk results; for each rejection that's name_mismatch or
        # name_low_confidence, check if Fix A's rescue would have flipped it
        # AND the rejection score still meets the rescue gate (sort>=85,
        # set>=85, Δtok≤2 for delta-near-miss; sort in [85,95) for low-band).
        for lname, loc, headline, reason in results:
            if not (reason.startswith("name_mismatch")
                    or reason.startswith("name_low_confidence")):
                continue

            # Parse "(sort=N, set=N, Δtok=N)" from reason
            m = re.search(r"sort=(\d+).*set=(\d+).*Δtok=(\d+)", reason)
            if not m:
                continue
            sort_s = int(m.group(1)); set_s = int(m.group(2)); delta = int(m.group(3))

            # Apply the actual verifier gate (sort≥NAME_SORT_THRESHOLD,
            # set≥NAME_SET_THRESHOLD, delta≤MAX+1 for delta rescue)
            sort_thresh = config.NAME_SORT_THRESHOLD
            set_thresh = config.NAME_SET_THRESHOLD
            high_thresh = config.NAME_HIGH_CONF_SCORE
            delta_max = config.NAME_TOKEN_DELTA_MAX

            delta_rescue_ok = (sort_s >= sort_thresh and set_s >= set_thresh
                               and delta == delta_max + 1)
            low_band_rescue_ok = (sort_s >= sort_thresh
                                  and sort_s < high_thresh
                                  and set_s >= set_thresh
                                  and delta <= delta_max)
            if not (delta_rescue_ok or low_band_rescue_ok):
                continue

            pract = mk_pract(meta)
            profile_card = {"name": lname, "headline": headline}
            if not verifier._card_has_medical_rescue(pract, profile_card):
                continue

            rescued.append({
                "pid": pid,
                "ahpra_name": meta.get("name", ""),
                "speciality": meta.get("speciality", ""),
                "linkedin_name": lname,
                "loc": loc,
                "headline": headline,
                "rejected_as": reason.strip(),
                "scores": f"sort={sort_s} set={set_s} Δtok={delta}",
                "rescue_path": "delta" if delta_rescue_ok else "low_band",
            })
            seen_pids.add(pid)
            break  # one rescue per practitioner is enough

    print(f"Rescued near-miss real doctors: {len(rescued)}")
    print()
    by_speciality: dict[str, int] = defaultdict(int)
    for r in rescued:
        by_speciality[r["speciality"]] += 1
        print(f"  {r['ahpra_name']} ({r['speciality']})")
        print(f"    LinkedIn: '{r['linkedin_name']}' loc={r['loc'][:50]}")
        print(f"    headline: {r['headline'][:90]}")
        print(f"    rejected: {r['rejected_as'][:80]}")
        print(f"    rescue:   {r['rescue_path']} ({r['scores']})")
        print()
    print("=" * 60)
    print("By speciality:")
    for s, c in sorted(by_speciality.items(), key=lambda x: -x[1]):
        print(f"  {c:>3}  {s}")


if __name__ == "__main__":
    main()
