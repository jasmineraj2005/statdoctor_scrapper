#!/usr/bin/env python3
"""_bulk_reinvestigate.py — re-investigate the *_no_medical_signal+followers≥300
rejection pool under the new verifier rank-by-medical-signal logic.

Why this exists: 2026-04-29 audit showed 6/8 inspected high-conf rejections
were wrong-person namesake matches (Belinda Drew→QLD govt, Helen Hsu→
Whitehorse Council, etc.). The verifier fix (commit 8d27f7b) now ranks
same-name candidates by medical-signal-in-headline, so re-running the
full search→profile→classify→connect pipeline against these doctors should
land on the actual doctor instead of the namesake — IF a different
LinkedIn profile exists with medical signal in the top-5 search results.

Strategy:
  - Pool = all rows in vic_linkedin_classifications.csv where:
      classification == 'non_influencer'
      fail_reason contains 'no_medical_signal'
      follower_count >= 300
    (50 candidates as of 2026-04-29; sorted by follower_count desc here)
  - For each: build practitioner dict from vic_high_yield_subset.csv,
    run full pipeline reusing main.py's _profile_and_classify (watchdog
    included) + main.py's _connect_pending for phase 2.
  - Skip if new search lands on same URL we already classified
    (no progress possible there).

Spec lock unchanged: plain connect, no note. 30k lifetime cap respected.
User-authorised re-attempt of terminal-stage practitioners (bypasses the
load_queue _keep filter).

Usage:
  python _bulk_reinvestigate.py                   # all 50, real connects
  python _bulk_reinvestigate.py --limit 25        # first 25 by follower count
  python _bulk_reinvestigate.py --limit 25 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import signal
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

import config
import auth
from searcher import RateLimitError
from sheets_logger import SheetsLogger, STAGE_ERROR, STAGE_SKIPPED

# Reuse main.py's full pipeline + watchdog
from main import (
    WATCHDOG_PER_ROW_SEC, WATCHDOG_CONSEC_BAIL,
    RowTimeoutError, _row_timeout_handler,
    adapt_row, _profile_and_classify, _connect_pending,
    _load_env_file,
)


THIS_DIR = Path(__file__).resolve().parent
SUBSET_CSV          = THIS_DIR / "data" / "vic_high_yield_subset.csv"
CLASSIFICATIONS_CSV = THIS_DIR / "data" / "vic_linkedin_classifications.csv"


def _build_pool() -> list[dict]:
    """Load the recovery pool: non_influencer + *_no_medical_signal +
    followers>=300. Returns rows from classifications.csv enriched with
    the prior URL we'd already matched (so we can detect no-progress repeats).
    """
    out = []
    with open(CLASSIFICATIONS_CSV) as f:
        for r in csv.DictReader(f):
            if r["classification"] != "non_influencer":
                continue
            if "no_medical_signal" not in r["fail_reason"]:
                continue
            try:
                fc = int(r["follower_count"]) if r["follower_count"] else 0
            except ValueError:
                fc = 0
            if fc < 300:
                continue
            out.append({
                "practitioner_id": r["practitioner_id"],
                "prior_url":       r["linkedin_url"],
                "prior_followers": fc,
                "prior_fail":      r["fail_reason"],
            })
    out.sort(key=lambda x: -x["prior_followers"])
    return out


def _load_subset_meta() -> dict[str, dict]:
    out = {}
    with open(SUBSET_CSV) as f:
        for r in csv.DictReader(f):
            out[r["practitioner_id"]] = r
    return out


def run(limit: int | None, dry_run: bool):
    if dry_run:
        config.DRY_RUN = True
        print("[bulk-reinvestigate] DRY RUN — connector will early-return.")

    pool = _build_pool()
    if limit:
        pool = pool[:limit]
    if not pool:
        print("[bulk-reinvestigate] Pool empty. Nothing to do.")
        return
    print(f"[bulk-reinvestigate] Pool size: {len(pool)} candidates "
          f"(top {min(5,len(pool))} by followers: "
          f"{', '.join(p['practitioner_id']+'='+str(p['prior_followers']) for p in pool[:5])})")

    subset = _load_subset_meta()
    logger = SheetsLogger()

    with sync_playwright() as pw:
        profile_dir = str(THIS_DIR / "browser_profile")
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": config.BROWSER_WIDTH, "height": config.BROWSER_HEIGHT},
            user_agent=config.USER_AGENT,
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            page = auth.ensure_logged_in(page,
                                         os.environ.get("LINKEDIN_EMAIL", ""),
                                         os.environ.get("LINKEDIN_PASSWORD", ""))
        except Exception as e:
            print(f"[bulk-reinvestigate] Auth failed: {type(e).__name__}: {e}")
            context.close()
            return

        signal.signal(signal.SIGALRM, _row_timeout_handler)
        consec_timeouts = 0
        pending: list[dict] = []
        new_url_count = 0
        same_url_count = 0
        no_match_count = 0

        for idx, p in enumerate(pool):
            pid = p["practitioner_id"]
            row = subset.get(pid)
            if not row:
                print(f"[bulk-reinvestigate] {pid} not in subset CSV — skip")
                continue
            pr = adapt_row(row)
            print(f"\n[bulk-reinvestigate] [{idx+1}/{len(pool)}] {pr['name']} ({pid}) "
                  f"prior_url={p['prior_url']} prior_followers={p['prior_followers']}")

            signal.alarm(WATCHDOG_PER_ROW_SEC)
            try:
                try:
                    res = _profile_and_classify(page, pr, logger)
                    consec_timeouts = 0
                except RateLimitError as e:
                    print(f"\n[bulk-reinvestigate] RATE LIMIT: {e}. Stopping.")
                    logger.set_stage(pr, STAGE_ERROR)
                    break
                except RowTimeoutError as e:
                    consec_timeouts += 1
                    print(f"\n[bulk-reinvestigate] WATCHDOG: {pid} {pr.get('name','?')} "
                          f"hung — {e}. Skipping (consec={consec_timeouts}).")
                    logger.set_stage(pr, STAGE_SKIPPED,
                                     detail="watchdog: per-row timeout (bulk-reinvestigate)")
                    if consec_timeouts >= WATCHDOG_CONSEC_BAIL:
                        print(f"\n[bulk-reinvestigate] WATCHDOG: {WATCHDOG_CONSEC_BAIL} "
                              f"consecutive timeouts — stopping.")
                        break
                    try:
                        page.goto("https://www.linkedin.com/feed/",
                                  wait_until="domcontentloaded", timeout=15_000)
                    except Exception:
                        pass
                    continue
            finally:
                signal.alarm(0)

            if res["pending"]:
                new_url = res["pending"]["url"]
                if new_url == p["prior_url"]:
                    print(f"  → same URL as before ({new_url}) — no rescue possible from new ranking")
                    same_url_count += 1
                else:
                    print(f"  → NEW URL via medical-signal ranking: {new_url} "
                          f"(was {p['prior_url']})")
                    new_url_count += 1
                    res["pending"]["_idx"] = idx
                    pending.append(res["pending"])
            else:
                no_match_count += 1

            time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))

        print(f"\n[bulk-reinvestigate] Phase 1 summary:")
        print(f"  new-URL rescues queued for connect: {new_url_count}")
        print(f"  same-URL (no rescue possible):      {same_url_count}")
        print(f"  no-match / skipped / errored:       {no_match_count}")

        # Phase 2: connect to the rescued candidates
        sent = 0
        if pending:
            try:
                sent = _connect_pending(page, pending, logger, send_cap=len(pending))
            except RateLimitError as e:
                print(f"\n[bulk-reinvestigate] RATE LIMIT during connects: {e}.")

        context.close()

    print(f"\n[bulk-reinvestigate] DONE. Connects sent: {sent}/{len(pending) if pending else 0} "
          f"(of {len(pool)} candidates re-investigated).")


if __name__ == "__main__":
    _load_env_file()
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Re-investigate only the first N candidates (sorted by follower count desc).")
    p.add_argument("--dry-run", action="store_true",
                   help="Profile + classify only; connector early-returns.")
    args = p.parse_args()
    run(args.limit, args.dry_run)
