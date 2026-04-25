#!/usr/bin/env python3
"""Audit existing 18 influencers under v2.1.1 medical-signal rule.

For each influencer in classifications.csv, locate the matched search-result
entry in the run logs (day*_run.log) and re-run the medical-signal check
against the headline. We can't re-check bio/experience without re-visiting
the profile (HOT), but headline is sufficient signal for most legit doctors.

Output: a flagged list (would_pass / would_fail / unknown_no_log) so the
user can decide whether to disconnect any of the 17 connect requests already
sent.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

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
    r"'(?P<headline>[^']*)' — matched"
)
MATCHED_URL = re.compile(r"→ matched \([^)]+\): (?P<url>https?://\S+)")


def load_logs() -> dict[str, dict]:
    """Walk each run log; for each practitioner block, capture the matched
    search result (name/loc/headline) and the URL it landed on. Returns
    {practitioner_id: {pid, name, lname, loc, headline, url}}.
    """
    out: dict[str, dict] = {}
    for path in LOG_FILES:
        p = Path(path)
        if not p.exists():
            continue
        cur_pid = None
        cur_name = None
        cur_block: dict | None = None
        with open(p) as f:
            for line in f:
                m = PROC_LINE.search(line)
                if m:
                    if cur_block and cur_block.get("url"):
                        out[cur_block["pid"]] = cur_block  # last-write-wins
                    cur_pid = m.group("pid")
                    cur_name = m.group("name")
                    cur_block = {"pid": cur_pid, "name": cur_name,
                                 "lname": "", "loc": "", "headline": "", "url": ""}
                    continue
                if cur_block is None:
                    continue
                rm = RESULT_LINE.search(line)
                if rm:
                    cur_block["lname"]    = rm.group("lname")
                    cur_block["loc"]      = rm.group("loc")
                    cur_block["headline"] = rm.group("headline")
                    continue
                um = MATCHED_URL.search(line)
                if um:
                    cur_block["url"] = um.group("url").rstrip("/")
            if cur_block and cur_block.get("url"):
                out[cur_block["pid"]] = cur_block
    return out


def load_subset_meta() -> dict[str, dict]:
    out = {}
    with open(config.INPUT_SUBSET_CSV) as f:
        for r in csv.DictReader(f):
            out[r["practitioner_id"]] = r
    return out


def run():
    logs = load_logs()
    subset = load_subset_meta()

    influencers = []
    with open(config.CLASSIFICATIONS_CSV) as f:
        for r in csv.DictReader(f):
            if r["classification"] == "influencer":
                influencers.append(r)

    print(f"Auditing {len(influencers)} influencers in classifications.csv\n")

    pass_rows = []
    fail_rows = []
    unknown_rows = []

    for r in influencers:
        pid = r["practitioner_id"]
        url = r["linkedin_url"]
        meta = subset.get(pid, {})
        speciality = meta.get("speciality", "")
        full_name = meta.get("name", "?")

        log = logs.get(pid)
        if not log:
            unknown_rows.append({
                "pid": pid, "name": full_name, "url": url,
                "reason": "no log entry — was profiled in an earlier run not in /tmp",
            })
            continue

        headline = log.get("headline", "")

        # Apply the new medical-signal check against the headline only.
        # (medical_signal_in_text is fed bio + experience too in production;
        #  here we only have the headline. Headline alone is a tight check —
        #  some legit doctors put their credentials in bio not headline. So
        #  unknown != automatic fail; we list them separately.)
        ok, reason = verifier.medical_signal_in_text(headline, speciality)

        # Also run the on-card rescue check (mirrors the verifier's new logic).
        rescue_card = verifier._card_has_medical_rescue(
            {"name": full_name, "specialities": speciality},
            {"name": log.get("lname", ""), "headline": headline},
        )

        row = {
            "pid": pid, "name": full_name, "speciality": speciality,
            "lname": log.get("lname", ""), "loc": log.get("loc", ""),
            "headline": headline, "url": url,
            "soft": r["soft_score"], "src": r["classifier_source"],
            "fol": r["follower_count"], "posts": r["post_count_90d"],
            "signal": reason if ok else "—",
            "rescue": rescue_card,
        }
        if ok or rescue_card:
            pass_rows.append(row)
        else:
            fail_rows.append(row)

    print("=" * 80)
    print(f"WOULD PASS (medical signal in headline): {len(pass_rows)}")
    print("=" * 80)
    for r in pass_rows:
        print(f"  ✅ {r['name']} ({r['speciality']})  soft={r['soft']}/{r['src']}")
        print(f"     headline: {r['headline'][:90]}")
        print(f"     signal:   {r['signal']}  rescue:{r['rescue']}")
        print(f"     {r['url']}")
        print()

    print("=" * 80)
    print(f"WOULD FAIL (no medical signal in headline): {len(fail_rows)}")
    print("=" * 80)
    for r in fail_rows:
        print(f"  ❌ {r['name']} ({r['speciality']})  soft={r['soft']}/{r['src']}")
        print(f"     LinkedIn name: {r['lname']}  loc: {r['loc']}")
        print(f"     headline: {r['headline'][:90]}")
        print(f"     fol={r['fol']} posts/90d={r['posts']}")
        print(f"     {r['url']}")
        print()

    print("=" * 80)
    print(f"UNKNOWN (no log for this run): {len(unknown_rows)}")
    print("=" * 80)
    for r in unknown_rows:
        print(f"  ? {r['name']} — {r['url']}")
        print(f"    reason: {r['reason']}")
        print()

    print("=" * 80)
    print(f"SUMMARY")
    print("=" * 80)
    print(f"  Total influencers:    {len(influencers)}")
    print(f"  Would pass v2.1.1:    {len(pass_rows)}")
    print(f"  Would FAIL v2.1.1:    {len(fail_rows)}  ← false positives")
    print(f"  Unknown (no log):     {len(unknown_rows)}  ← need re-profile")


if __name__ == "__main__":
    run()
