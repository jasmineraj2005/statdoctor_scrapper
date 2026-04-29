#!/usr/bin/env python3
"""_reprofile_hot_locked.py — one-off: re-profile + classify + connect the 3
candidates locked out of Day-4 batch #4 by HOT cool-down from hung runs.

Background: batch #4 hit 3 mid-run hangs across 4 attempts. Each hang
marked the in-flight URL as visited via _visit_tracker before crashing
mid-search/profile, never completing classification or connect. The
visit_tracker entry then blocks the URL from being re-attempted for 48h.

These 3 doctors were high-confidence search matches that never got past
the search step in any successful run:

  - Hany Georgeos (Skin Cancer College Mentor & Fellow, GP, Flora Hill)
  - Graham Leslie Barrett (already classified influencer soft=7 in run #2,
    but never made it to phase 2 connect because run #2 hung mid-phase-1)
  - Glenn Richard Hocking (Anatomical Pathology, Gardenvale)

Bypasses _visit_tracker.is_hot deliberately. User-authorised. Spec lock
still applies (plain connect, no note). 30k lifetime cap respected.

Usage:
  python _reprofile_hot_locked.py              # real connects
  python _reprofile_hot_locked.py --dry-run    # profile + classify only
"""
from __future__ import annotations

import argparse
import os
import random
import time

from playwright.sync_api import sync_playwright

import config
import auth
import connector
import influencer_classifier
from profile_profiler import profile as profiler_profile
from searcher import RateLimitError
from sheets_logger import (
    SheetsLogger,
    STAGE_PROFILED, STAGE_CLASSIFIED, STAGE_CONNECTED,
    STAGE_SKIPPED, STAGE_ERROR, STATUS_SENT,
)


APPROVED = [
    {
        "practitioner_id":   "MED0001219184",
        "name":              "Dr Hany Georgeos",
        "speciality":        "General practice",
        "postcode_searched": "3550",
        "location":          "Flora Hill, VIC, 3550",
        "suburb":            "Flora Hill",
        "state":             "VIC",
        "postcode":          "3550",
        "specialities":      "General practice",
        "registration_type": "Specialist",
        "url":               "https://www.linkedin.com/in/dr-hany-georgeos",
    },
    {
        "practitioner_id":   "MED0001122716",
        "name":              "Dr Graham Leslie Barrett",
        "speciality":        "General practice",
        "postcode_searched": "3068",
        "location":          "Fitzroy North, VIC, 3068",
        "suburb":            "Fitzroy North",
        "state":             "VIC",
        "postcode":          "3068",
        "specialities":      "General practice",
        "registration_type": "Specialist",
        "url":               "https://www.linkedin.com/in/grahambarrettaus",
    },
    {
        "practitioner_id":   "MED0001139840",
        "name":              "Dr Glenn Richard Hocking",
        "speciality":        "Pathology",
        "postcode_searched": "3185",
        "location":          "Gardenvale, VIC, 3185",
        "suburb":            "Gardenvale",
        "state":             "VIC",
        "postcode":          "3185",
        "specialities":      "Pathology, Anatomical pathology (including cytopathology)",
        "registration_type": "Specialist",
        "url":               "https://www.linkedin.com/in/glen-hocking-90226a346",
    },
]


def _load_env_file():
    this_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(this_dir)
    for env_path in (os.path.join(this_dir, ".env"), os.path.join(repo_root, ".env")):
        if not os.path.exists(env_path):
            continue
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        return


def run(dry_run: bool):
    if dry_run:
        config.DRY_RUN = True
        print("[reprofile-hot] DRY RUN — connector will early-return, no clicks.")

    logger = SheetsLogger()
    logger.set_send_cap(len(APPROVED))

    with sync_playwright() as pw:
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "browser_profile")
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
            print(f"[reprofile-hot] Auth failed: {type(e).__name__}: {e}")
            context.close()
            return

        sent = 0
        for pr in APPROVED:
            url = pr.pop("url")
            pid, name = pr["practitioner_id"], pr["name"]
            print(f"\n[reprofile-hot] ── {name} ({pid}) ──")
            print(f"  url: {url}")

            try:
                profile_dict = profiler_profile(
                    page, url,
                    verifier_confidence="high",
                    ahpra_specialities=pr["speciality"],
                    event_logger=logger,
                    practitioner=pr,
                )
            except RateLimitError:
                print("[reprofile-hot] RATE LIMIT during profile — stopping.")
                break
            except Exception as e:
                print(f"  profiler error: {type(e).__name__}: {e}")
                logger.set_stage(pr, STAGE_ERROR)
                continue

            logger.set_stage(pr, STAGE_PROFILED)
            print(f"  profiled: followers={profile_dict.get('followers', 0)} "
                  f"posts_90d={profile_dict.get('post_count_90d', 0)} "
                  f"creator_mode={profile_dict.get('creator_mode', False)}")

            classification = influencer_classifier.classify(
                profile_dict,
                practitioner_id=pid,
                ahpra_specialty=pr["speciality"],
                event_logger=logger,
                practitioner=pr,
            )
            verdict = classification.get("classification", "")
            soft    = classification.get("soft_score", 0)
            src     = classification.get("classifier_source", "")
            print(f"  classify: {verdict} (soft={soft}, source={src})")
            logger.log_classification(pr, profile_dict, classification)
            logger.set_stage(pr, STAGE_CLASSIFIED)

            if verdict != "influencer":
                fail = classification.get("fail_reason", "") or verdict
                print(f"  → not an influencer under v2.1.2: {fail}")
                logger.set_stage(pr, STAGE_SKIPPED, detail=f"{verdict}: {fail}")
                continue

            try:
                status, detail = connector.send_connection_request(
                    page, url, name, classification=verdict,
                    event_logger=logger, practitioner=pr,
                )
            except RateLimitError:
                print("[reprofile-hot] RATE LIMIT during connect — stopping.")
                break
            except Exception as e:
                print(f"  connector error: {type(e).__name__}: {e}")
                logger.update_connect_status(pid, STAGE_ERROR, detail=str(e)[:100])
                logger.set_stage(pr, STAGE_ERROR)
                continue

            print(f"  [connect] {status} — {detail}")
            logger.update_connect_status(pid, status, detail=detail)
            if status == STATUS_SENT:
                sent += 1
                logger.set_stage(pr, STAGE_CONNECTED,
                                 detail=f"connect sent — soft={soft}")
            else:
                logger.set_stage(pr, STAGE_SKIPPED, detail=f"connect {status}: {detail}")

            time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))

        context.close()

    print(f"\n[reprofile-hot] Done. Connects sent: {sent}/{len(APPROVED)}")


if __name__ == "__main__":
    _load_env_file()
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Profile + classify only; connector early-returns.")
    args = p.parse_args()
    run(args.dry_run)
