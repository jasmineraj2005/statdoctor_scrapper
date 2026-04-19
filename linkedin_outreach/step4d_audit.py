"""Step 4d — profile-profiler dry-test on 5 NEW matches.

Rotates through provided seeds (default 7, 13, 17) sampling 10 rows at a
time. For each row: search → verify. If a matched URL is HOT (48h cool-down)
we record the match but SKIP the profile visit. We stop as soon as we have
`target` new profiler records. Writes JSON to step4d_profiler_output.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import auth  # noqa: E402
import config  # noqa: E402
import searcher  # noqa: E402
from searcher import RateLimitError  # noqa: E402
from profile_profiler import profile  # noqa: E402
from _visit_tracker import is_hot  # noqa: E402

SUBSET_CSV = THIS_DIR / "data" / "vic_high_yield_subset.csv"

POSTCODE_TO_STATE = {"3": "VIC", "2": "NSW", "4": "QLD", "5": "SA",
                     "6": "WA", "7": "TAS", "0": "NT"}
SUBURB_RX = re.compile(r"^\s*([^,]+),")


def adapt_row(row: dict) -> dict:
    loc = row.get("location", "") or ""
    m = SUBURB_RX.match(loc)
    suburb = m.group(1).strip() if m else ""
    pc = (row.get("postcode_searched", "") or "").strip()
    return {
        "practitioner_id": row["practitioner_id"],
        "name": row["name"],
        "suburb": suburb,
        "state": POSTCODE_TO_STATE.get(pc[:1], "VIC"),
        "postcode": pc,
        "specialities": row.get("speciality", ""),
        "registration_type": "Specialist",
    }


def load_dotenv() -> None:
    for env_path in (THIS_DIR / ".env", THIS_DIR.parent / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run(seeds: list[int], target: int) -> None:
    df = pd.read_csv(SUBSET_CSV, dtype=str).fillna("")
    load_dotenv()
    email = os.environ.get("LINKEDIN_EMAIL", "")
    password = os.environ.get("LINKEDIN_PASSWORD", "")

    profiled_rows: list[dict] = []
    hot_skipped:   list[dict] = []
    seen_ids = set()

    def done() -> bool:
        return len(profiled_rows) >= target

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(THIS_DIR / "browser_profile"),
            headless=config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": config.BROWSER_WIDTH,
                      "height": config.BROWSER_HEIGHT},
            user_agent=config.USER_AGENT,
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page = auth.ensure_logged_in(page, email, password)

        for seed in seeds:
            if done():
                break
            sample = df.sample(n=10, random_state=seed).to_dict("records")
            for row in sample:
                if done():
                    break
                if row["practitioner_id"] in seen_ids:
                    continue
                seen_ids.add(row["practitioner_id"])

                pr = adapt_row(row)
                print(f"\n[seed={seed}] {pr['name']} ({pr['practitioner_id']})")
                try:
                    matched = searcher.search_and_find_profile(page, pr)
                except RateLimitError as e:
                    print(f"[STOP] rate-limit: {e}")
                    break
                except Exception as e:
                    print(f"  search error: {type(e).__name__}: {e}")
                    continue

                if not matched:
                    print("  no match")
                    time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))
                    continue

                url = matched["url"]
                conf = matched.get("verifier_confidence", "")
                if is_hot(url):
                    print(f"  matched ({conf}) — HOT, skipping profiler visit: {url}")
                    hot_skipped.append({
                        "practitioner_id": pr["practitioner_id"],
                        "practitioner_name": pr["name"],
                        "linkedin_url": url,
                        "verifier_confidence": conf,
                    })
                    time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))
                    continue

                print(f"  matched ({conf}): {url} → profiling…")
                prof = profile(page, url,
                               verifier_confidence=conf,
                               ahpra_specialities=pr.get("specialities", ""))
                prof["practitioner_id"] = pr["practitioner_id"]
                prof["practitioner_name"] = pr["name"]
                profiled_rows.append(prof)
                time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))

        ctx.close()

    out = THIS_DIR / "step4d_profiler_output.json"
    out.write_text(json.dumps(
        {"profiled": profiled_rows, "hot_skipped": hot_skipped},
        default=str, indent=2,
    ))
    print(f"\nwrote {out}")
    print(f"profiled: {len(profiled_rows)}  hot-skipped: {len(hot_skipped)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="7,13,17")
    ap.add_argument("--target", type=int, default=5)
    args = ap.parse_args()
    run([int(s) for s in args.seeds.split(",") if s.strip()], args.target)
