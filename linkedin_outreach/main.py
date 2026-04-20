#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# main.py — VIC LinkedIn outreach orchestrator (step-6b pipeline)
#
#   subset CSV row
#     → search (searcher.search_and_find_profile)
#     → is_hot gate (_visit_tracker.is_hot)
#     → profile (profile_profiler.profile)
#     → classify (influencer_classifier.classify)
#     → connect iff classification == "influencer"
#         (connector.send_connection_request)
#     → CSV + sheets (sheets_logger.log_classification / set_stage /
#       update_connect_status)
#
# Usage:
#   # First run (saves cookies):
#   python main.py --email you@example.com --password yourpass
#
#   # Subsequent runs:
#   python main.py
#
#   # Dry run — search + verify + profile + classify, NO connect clicks:
#   python main.py --dry-run
#
#   # Limit connections this session (independent of the daily cap):
#   python main.py --limit 10
# ─────────────────────────────────────────────────────────────────────────────
import argparse
import os
import random
import re
import time

import pandas as pd
from playwright.sync_api import sync_playwright

import config
import auth
import searcher
import connector
import influencer_classifier
from profile_profiler import profile as profiler_profile
from searcher import RateLimitError
from _visit_tracker import is_hot
import sheets_logger as sl
from sheets_logger import (
    SheetsLogger,
    STAGE_PENDING, STAGE_SEARCHED, STAGE_PROFILED, STAGE_CLASSIFIED,
    STAGE_CONNECTED, STAGE_SKIPPED, STAGE_NOT_FOUND, STAGE_ERROR,
    TERMINAL_STAGES,
    STATUS_SENT,
)


# ── .env loader (no external dep) ────────────────────────────────────────────

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
        print(f"[main] Loaded credentials from {env_path}")
        return


# ── Row adapter (subset CSV → searcher's expected dict) ──────────────────────

POSTCODE_TO_STATE = {
    "3": "VIC", "2": "NSW", "4": "QLD", "5": "SA",
    "6": "WA", "7": "TAS", "0": "NT",
}
SUBURB_RX = re.compile(r"^\s*([^,]+),")


def adapt_row(row: dict) -> dict:
    """Map vic_high_yield_subset.csv row → searcher-compatible practitioner dict.

    Identical semantics to step4d_audit.adapt_row / selector_dry_run.adapt_row —
    kept local so main.py has no inbound coupling on those scripts.
    """
    loc = row.get("location", "") or ""
    m = SUBURB_RX.match(loc)
    suburb = m.group(1).strip() if m else ""
    postcode = (row.get("postcode_searched", "") or "").strip()
    return {
        "practitioner_id":   row["practitioner_id"],
        "name":              row["name"],
        "suburb":            suburb,
        "state":             POSTCODE_TO_STATE.get(postcode[:1], "VIC"),
        "postcode":          postcode,
        "postcode_searched": postcode,
        "location":          loc,
        "speciality":        row.get("speciality", ""),
        "specialities":      row.get("speciality", ""),  # legacy alias
        "registration_type": "Specialist",
    }


# ── Dry-run logger (in-memory; no CSV/sheet writes) ──────────────────────────

class DryRunLogger:
    """No-op stand-in for SheetsLogger. Logs intent to stdout only.

    NOTE: ROADMAP step-8 gate requires the classifications + processing-status
    CSV *and* sheet tabs to populate during dry-run. So the 50-row dry-run
    uses the REAL SheetsLogger with config.DRY_RUN=True — connector early-
    returns before any click, but writes still happen. This class is kept
    only for the special case of --no-logging runs; it is NOT wired into the
    step-8 path.
    """
    def __init__(self):
        print("[main] DryRunLogger: no CSV / sheet writes will occur.")
    def add_pending(self, practitioner): pass
    def set_stage(self, practitioner, stage): pass
    def log_classification(self, practitioner, profile, classification): pass
    def update_connect_status(self, pid, status, detail=""): pass
    def update(self, pid, status, linkedin_url="", notes=""): pass
    def already_classified(self, practitioner_id): return False
    def already_processed(self, practitioner_id): return False
    def get_stage(self, pid): return ""
    def count_sent_today(self): return 0
    def count_sent_this_week(self): return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="VIC LinkedIn outreach (influencer-gated)")
    p.add_argument("--email",    default="", help="LinkedIn email (first run only)")
    p.add_argument("--password", default="", help="LinkedIn password (first run only)")
    p.add_argument("--limit",    type=int, default=config.MAX_CONNECTIONS_PER_SESSION,
                   help="Max practitioner rows to walk this session. "
                        "Real-mode sends are independently capped at "
                        "MAX_CONNECTIONS_PER_DAY/WEEK regardless of --limit.")
    p.add_argument("--dry-run",  action="store_true",
                   help="Run the full pipeline but connector early-returns (no clicks).")
    p.add_argument("--no-logging", action="store_true",
                   help="Use DryRunLogger — skip all CSV + sheet writes. "
                        "Use with --dry-run for an isolated test pass.")
    return p.parse_args()


# ── Queue loading ─────────────────────────────────────────────────────────────

def load_queue(logger) -> list[dict]:
    """Read vic_high_yield_subset.csv, adapt rows, and skip practitioners whose
    Processing Status row is already in a terminal stage.

    The subset CSV is pre-filtered (VIC specialists, top-50 postcodes, exclude
    Non-Practising / Limited / Provisional) — no further filters applied here.
    """
    path = config.INPUT_SUBSET_CSV
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Subset CSV missing: {path}. Run build_subset.py first."
        )
    df = pd.read_csv(path, dtype=str).fillna("")
    rows = [adapt_row(r) for r in df.to_dict("records")]

    # Dedup on terminal Processing Status. (already_classified is a subset —
    # it misses practitioners who bailed at search / is_hot / profile.)
    def _keep(pr: dict) -> bool:
        stage = logger.get_stage(pr["practitioner_id"])
        return stage not in TERMINAL_STAGES

    before = len(rows)
    rows = [r for r in rows if _keep(r)]
    print(f"[main] Queue: {len(rows)}/{before} practitioners "
          f"({before - len(rows)} already in terminal stage)")
    return rows


# ── Per-practitioner pipeline ────────────────────────────────────────────────

def _process_practitioner(page, pr: dict, logger, session_sent: int,
                          send_cap: int) -> tuple[str, int]:
    """Run one practitioner end-to-end. Returns (terminal_stage, sent_increment).

    terminal_stage is one of TERMINAL_STAGES. sent_increment is 1 iff we
    successfully clicked Connect in this call, else 0.
    """
    pid  = pr["practitioner_id"]
    name = pr["name"]
    print(f"\n[main] ── {name} ({pid}) ──")

    logger.set_stage(pr, STAGE_PENDING)

    # 1. Search
    try:
        matched = searcher.search_and_find_profile(page, pr)
    except RateLimitError:
        raise  # bubble up to main loop so session stops
    except Exception as e:
        print(f"  search error: {type(e).__name__}: {e}")
        classification = _error_classification(pr, "", f"search_error:{type(e).__name__}")
        logger.log_classification(pr, {}, classification)
        logger.set_stage(pr, STAGE_ERROR)
        return STAGE_ERROR, 0

    logger.set_stage(pr, STAGE_SEARCHED)

    if not matched:
        print("  → no LinkedIn match")
        classification = _error_classification(pr, "", "not_found", verdict="not_found")
        logger.log_classification(pr, {}, classification)
        logger.set_stage(pr, STAGE_NOT_FOUND)
        return STAGE_NOT_FOUND, 0

    url  = matched["url"]
    conf = matched.get("verifier_confidence", "")
    print(f"  → matched ({conf}): {url}")

    # 2. is_hot gate — skip if visited in last 48h (resume semantics).
    if is_hot(url):
        print(f"  → HOT (48h cool-down). Skipping profile + classify for this pass.")
        # Deliberately DON'T set_stage to terminal — we want to retry on a
        # future run once the cool-down expires.
        return "", 0

    # 3. Profile
    try:
        profile_dict = profiler_profile(
            page, url,
            verifier_confidence=conf,
            ahpra_specialities=pr.get("speciality", "") or pr.get("specialities", ""),
        )
    except RateLimitError:
        raise
    except Exception as e:
        print(f"  profiler error: {type(e).__name__}: {e}")
        classification = _error_classification(pr, url, f"profiler_error:{type(e).__name__}")
        logger.log_classification(pr, {}, classification)
        logger.set_stage(pr, STAGE_ERROR)
        return STAGE_ERROR, 0
    logger.set_stage(pr, STAGE_PROFILED)

    # 4. Classify
    classification = influencer_classifier.classify(
        profile_dict,
        practitioner_id=pid,
        ahpra_specialty=pr.get("speciality", "") or pr.get("specialities", ""),
    )
    verdict = classification.get("classification", "")
    soft    = classification.get("soft_score", 0)
    src     = classification.get("classifier_source", "")
    print(f"  → classify: {verdict} (soft={soft}, source={src})")
    logger.log_classification(pr, profile_dict, classification)
    logger.set_stage(pr, STAGE_CLASSIFIED)

    # 5. Connect gate — only influencers proceed.
    if verdict != "influencer":
        logger.set_stage(pr, STAGE_SKIPPED)
        return STAGE_SKIPPED, 0

    if session_sent >= send_cap:
        print(f"  → send cap reached ({session_sent}/{send_cap}); skipping connect")
        logger.set_stage(pr, STAGE_SKIPPED)
        return STAGE_SKIPPED, 0

    # 6. Connect
    try:
        status, detail = connector.send_connection_request(
            page, url, name, classification=verdict,
        )
    except RateLimitError:
        raise
    except Exception as e:
        print(f"  connector error: {type(e).__name__}: {e}")
        logger.update_connect_status(pid, STAGE_ERROR, detail=str(e)[:100])
        logger.set_stage(pr, STAGE_ERROR)
        return STAGE_ERROR, 0

    print(f"  → connect: {status} — {detail}")
    logger.update_connect_status(pid, status, detail=detail)
    if status == STATUS_SENT:
        logger.set_stage(pr, STAGE_CONNECTED)
        return STAGE_CONNECTED, 1

    # connect failed cleanly (unavailable / needs-note / etc) — terminal skip.
    logger.set_stage(pr, STAGE_SKIPPED)
    return STAGE_SKIPPED, 0


def _error_classification(pr: dict, url: str, reason: str,
                          verdict: str = "error") -> dict:
    """Build a minimal classification row for failed-before-classify paths.

    Keeps classifications.csv schema-stable even when we never ran the classifier.
    """
    from datetime import datetime
    return {
        "practitioner_id":       pr.get("practitioner_id", ""),
        "linkedin_url":          url,
        "classification":        verdict,
        "soft_score":            0,
        "hard_filters_passed":   False,
        "follower_count":        0,
        "post_count_90d":        0,
        "last_post_date":        "",
        "has_video_90d":         False,
        "creator_mode":          False,
        "bio_signals":           [],
        "classifier_source":     "",
        "classifier_confidence": None,
        "classified_at":         datetime.now().isoformat(timespec="seconds"),
        "fail_reason":           reason,
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args):
    if args.dry_run:
        config.DRY_RUN = True
        print("[main] DRY RUN — connector.send_connection_request will early-return.")

    logger = DryRunLogger() if args.no_logging else SheetsLogger()

    # Daily / weekly cap still tracked via legacy outreach_log.csv; it's the
    # only source that records `sent` timestamps. Processing Status doesn't
    # track when a connect was sent vs just attempted.
    sent_today = logger.count_sent_today()
    sent_week  = logger.count_sent_this_week()
    print(f"[main] Sent today: {sent_today} / {config.MAX_CONNECTIONS_PER_DAY}")
    print(f"[main] Sent this week: {sent_week} / {config.MAX_CONNECTIONS_PER_WEEK}")

    if sent_today >= config.MAX_CONNECTIONS_PER_DAY:
        print("[main] Daily limit reached.")
        return
    if sent_week >= config.MAX_CONNECTIONS_PER_WEEK:
        print("[main] Weekly limit reached.")
        return

    # Row cap = --limit. Send cap = remaining daily/weekly quota.
    # Real-mode sends stop as soon as either cap is hit; dry-run never sends.
    send_cap = min(
        config.MAX_CONNECTIONS_PER_DAY - sent_today,
        config.MAX_CONNECTIONS_PER_WEEK - sent_week,
    )
    print(f"[main] Row cap: {args.limit} practitioners | Send cap: {send_cap}")

    queue = load_queue(logger)[: args.limit]
    print(f"[main] Walking {len(queue)} practitioners this session")

    sent_this_session = 0

    with sync_playwright() as pw:
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "browser_profile")
        os.makedirs(profile_dir, exist_ok=True)
        print(f"[main] Persistent browser profile: {profile_dir}")

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
            page = auth.ensure_logged_in(page, args.email, args.password)
        except RuntimeError as e:
            print(f"[main] Auth failed: {e}")
            context.close()
            return
        except Exception as e:
            msg = str(e)
            if "Target page" in msg or "browser has been closed" in msg:
                print("[main] Auth failed: browser window was closed before login.")
            else:
                print(f"[main] Unexpected auth error: {type(e).__name__}: {e}")
            context.close()
            return

        for pr in queue:
            try:
                terminal_stage, sent_inc = _process_practitioner(
                    page, pr, logger, sent_this_session, send_cap,
                )
            except RateLimitError as e:
                print(f"\n[main] RATE LIMIT: {e}. Stopping session.")
                logger.set_stage(pr, STAGE_ERROR)
                break

            sent_this_session += sent_inc

            if sent_this_session >= send_cap and sent_inc:
                print(f"[main] Send cap reached ({sent_this_session}/{send_cap}).")
                break

            if sent_inc:
                # Post-send break when hitting a multiple of SESSION_BREAK_EVERY_N
                if sent_this_session % config.SESSION_BREAK_EVERY_N == 0:
                    break_dur = random.uniform(*config.SESSION_BREAK_DURATION_SEC)
                    print(f"[main] Session break {break_dur:.0f}s")
                    time.sleep(break_dur)
            else:
                # Pacing between non-sending iterations. Profiler already pauses
                # while extracting; this is just the inter-search jitter.
                time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))

        context.close()

    print(f"\n[main] Session complete. Sent: {sent_this_session} / cap {session_limit}.")


if __name__ == "__main__":
    _load_env_file()
    args = parse_args()
    if not args.email:
        args.email = os.environ.get("LINKEDIN_EMAIL", "")
    if not args.password:
        args.password = os.environ.get("LINKEDIN_PASSWORD", "")
    run(args)
