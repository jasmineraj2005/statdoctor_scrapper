#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# main.py  –  LinkedIn outreach orchestrator
#
# Usage:
#   # First run (saves cookies):
#   python main.py --email you@example.com --password yourpass
#
#   # Subsequent runs (uses saved cookies):
#   python main.py
#
#   # Dry run — search + verify but don't send:
#   python main.py --dry-run
#
#   # Restrict to certain states:
#   python main.py --states NSW VIC
#
#   # Limit connections this session:
#   python main.py --limit 30
# ─────────────────────────────────────────────────────────────────────────────
import argparse, os, random, time
import pandas as pd
from playwright.sync_api import sync_playwright

import config
import auth
import searcher
import connector
from searcher import RateLimitError
from sheets_logger import SheetsLogger, STATUS_SENT, STATUS_NOT_FOUND, STATUS_ERROR


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


# ── No-op logger for dry runs (leaves zero footprint) ────────────────────────

class DryRunLogger:
    def __init__(self):
        print("[main] DRY RUN: using in-memory logger — no CSV, Sheet, or progress writes.")
    def add_pending(self, practitioner): pass
    def update(self, practitioner_id, status, linkedin_url="", notes=""): pass
    def count_sent_today(self): return 0
    def count_sent_this_week(self): return 0
    def already_processed(self, practitioner_id): return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="LinkedIn outreach for medical practitioners")
    p.add_argument("--email",    default="", help="LinkedIn email (first run only)")
    p.add_argument("--password", default="", help="LinkedIn password (first run only)")
    p.add_argument("--states",   nargs="+",  default=[], help="Filter by state codes e.g. NSW VIC")
    p.add_argument("--limit",    type=int,   default=config.MAX_CONNECTIONS_PER_SESSION,
                   help="Max connections to send this session")
    p.add_argument("--dry-run",  action="store_true", help="Search + verify but don't send")
    return p.parse_args()


# ── Data loading ──────────────────────────────────────────────────────────────

def load_queue(states_filter: list, logger: SheetsLogger) -> list[dict]:
    """Load and filter practitioners, skipping already-processed ones."""
    df = pd.read_csv(config.INPUT_CSV, dtype=str).fillna("")

    # Apply state filter
    target_states = states_filter or config.TARGET_STATES
    if target_states:
        df = df[df["state"].isin([s.upper() for s in target_states])]

    # Apply specialty filter
    if config.TARGET_SPECIALTIES:
        mask = df["specialities"].apply(
            lambda s: any(sp.lower() in s.lower() for sp in config.TARGET_SPECIALTIES)
        )
        df = df[mask]

    # Apply registration type exclusions
    if config.EXCLUDE_REG_TYPES:
        for excl in config.EXCLUDE_REG_TYPES:
            df = df[~df["registration_type"].str.contains(excl, case=False, na=False)]

    # Resume from progress file
    start_id = _read_progress()
    if start_id:
        ids = df["practitioner_id"].tolist()
        if start_id in ids:
            idx = ids.index(start_id) + 1
            df  = df.iloc[idx:]
            print(f"[main] Resuming after {start_id} ({len(df)} practitioners remaining)")

    # Skip already-processed
    df = df[~df["practitioner_id"].apply(logger.already_processed)]

    print(f"[main] Queue: {len(df)} practitioners to process")
    return df.to_dict("records")


def _read_progress() -> str:
    if os.path.exists(config.PROGRESS_FILE):
        with open(config.PROGRESS_FILE) as f:
            return f.read().strip()
    return ""


def _save_progress(practitioner_id: str):
    if config.DRY_RUN:
        return
    with open(config.PROGRESS_FILE, "w") as f:
        f.write(practitioner_id)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args):
    if args.dry_run:
        config.DRY_RUN = True
        print("[main] DRY RUN mode — no connection requests will be sent.")

    logger = DryRunLogger() if config.DRY_RUN else SheetsLogger()

    # Daily / weekly cap check
    sent_today = logger.count_sent_today()
    sent_week  = logger.count_sent_this_week()
    print(f"[main] Sent today: {sent_today} / {config.MAX_CONNECTIONS_PER_DAY}")
    print(f"[main] Sent this week: {sent_week} / {config.MAX_CONNECTIONS_PER_WEEK}")

    if sent_today >= config.MAX_CONNECTIONS_PER_DAY:
        print("[main] Daily limit reached. Run again tomorrow.")
        return
    if sent_week >= config.MAX_CONNECTIONS_PER_WEEK:
        print("[main] Weekly limit reached. Run again next week.")
        return

    session_limit = min(
        args.limit,
        config.MAX_CONNECTIONS_PER_DAY - sent_today,
        config.MAX_CONNECTIONS_PER_WEEK - sent_week,
    )
    print(f"[main] Session cap: {session_limit} connections")

    queue = load_queue(args.states, logger)

    with sync_playwright() as pw:
        profile_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "browser_profile"
        )
        os.makedirs(profile_dir, exist_ok=True)
        print(f"[main] Using persistent browser profile: {profile_dir}")

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
        # Persistent context starts with one page already
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
                print("[main] Auth failed: browser window was closed before login completed.")
                print("       Re-run the command and leave the Chromium window alone.")
            else:
                print(f"[main] Unexpected auth error: {type(e).__name__}: {e}")
            context.close()
            return

        sent_this_session = 0

        for practitioner in queue:
            pid  = practitioner["practitioner_id"]
            name = practitioner["name"]
            print(f"\n[main] Processing: {name} ({pid})")

            # Add pending row to sheet before we start
            logger.add_pending(practitioner)

            try:
                profile = searcher.search_and_find_profile(page, practitioner)

                if profile is None:
                    print(f"  → Not found on LinkedIn")
                    logger.update(pid, STATUS_NOT_FOUND, notes="no matching profile found")
                    _save_progress(pid)
                    time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))
                    continue

                print(f"  → Matched: {profile['url']}")

                status, detail = connector.send_connection_request(
                    page, profile["url"], name
                )
                logger.update(pid, status, linkedin_url=profile["url"], notes=detail)
                _save_progress(pid)

                if status == STATUS_SENT:
                    sent_this_session += 1
                    print(f"  → Sent ({sent_this_session}/{session_limit})")

                    if sent_this_session >= session_limit:
                        print("[main] Session limit reached. Stopping.")
                        break

                    # Longer pause after sending a request
                    delay = random.uniform(*config.DELAY_BETWEEN_CONNECTIONS_SEC)
                    print(f"  → Waiting {delay:.0f}s before next search...")
                    time.sleep(delay)

                    # Scheduled session break
                    if sent_this_session % config.SESSION_BREAK_EVERY_N == 0:
                        break_dur = random.uniform(*config.SESSION_BREAK_DURATION_SEC)
                        print(f"[main] Session break — pausing {break_dur:.0f}s...")
                        time.sleep(break_dur)
                else:
                    time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))

            except RateLimitError as e:
                print(f"\n[main] RATE LIMIT detected: {e}")
                print("[main] Stopping session. Wait a few hours before re-running.")
                logger.update(pid, STATUS_ERROR, notes="rate_limit_stop")
                break
            except Exception as e:
                print(f"  → Unexpected error: {e}")
                logger.update(pid, STATUS_ERROR, notes=str(e)[:120])
                _save_progress(pid)
                time.sleep(random.uniform(5, 10))
                continue

        browser.close()

    print(f"\n[main] Session complete. Sent: {sent_this_session} connection requests.")


if __name__ == "__main__":
    _load_env_file()
    args = parse_args()
    if not args.email:
        args.email = os.environ.get("LINKEDIN_EMAIL", "")
    if not args.password:
        args.password = os.environ.get("LINKEDIN_PASSWORD", "")
    run(args)
