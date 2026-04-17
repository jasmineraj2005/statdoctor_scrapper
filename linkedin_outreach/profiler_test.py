"""Dry-test the profiler on a list of LinkedIn profile URLs.

Usage:
    python profiler_test.py URL1 URL2 URL3
"""
from __future__ import annotations

import json
import os
import sys
import time
import random
from pathlib import Path

from playwright.sync_api import sync_playwright

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import auth  # noqa: E402
import config  # noqa: E402
from profile_profiler import profile  # noqa: E402


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


def main(urls: list[str]) -> None:
    load_dotenv()
    email = os.environ.get("LINKEDIN_EMAIL", "")
    password = os.environ.get("LINKEDIN_PASSWORD", "")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(THIS_DIR / "browser_profile"),
            headless=config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": config.BROWSER_WIDTH, "height": config.BROWSER_HEIGHT},
            user_agent=config.USER_AGENT,
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page = auth.ensure_logged_in(page, email, password)

        all_results = []
        for url in urls:
            print(f"\n=== profiling {url} ===")
            data = profile(page, url)
            all_results.append(data)
            # Pretty-print the interesting fields
            print(json.dumps(
                {k: v for k, v in data.items() if k not in ("bio",)},
                default=str, indent=2,
            ))
            if data.get("bio"):
                print(f"bio (first 300 chars): {data['bio'][:300]}")
            time.sleep(random.uniform(4, 8))

        out = THIS_DIR / "profiler_test_output.json"
        out.write_text(json.dumps(all_results, default=str, indent=2))
        print(f"\nwrote {out}")
        ctx.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: profiler_test.py URL1 URL2 ...")
    main(sys.argv[1:])
