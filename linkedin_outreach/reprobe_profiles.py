"""Re-probe selectors on a list of known profile URLs — skips the search
step to validate selector changes without burning rate-limit budget.

Usage:
    python reprobe_profiles.py URL1 URL2 URL3 ...
"""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import auth  # noqa: E402
import config  # noqa: E402
import li_selectors as selectors  # noqa: E402


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

        for url in urls:
            print(f"\n=== {url} ===")
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(random.uniform(3, 6))
            page.mouse.wheel(0, 400)
            time.sleep(random.uniform(1.5, 2.5))
            page.mouse.wheel(0, -400)
            try:
                page.wait_for_selector("main h2", timeout=10_000)
            except Exception:
                pass

            data = page.evaluate(selectors.PROFILE_DATA_JS)
            print(f"name     = {data.get('name','')}")
            print(f"headline = {data.get('headline','')[:120]}")
            print(f"location = {data.get('location','')[:80]}")
            print(f"url      = {data.get('canonical_url','')}")

            name = data.get("name", "")
            if name:
                for lbl, sel in [
                    ("CONNECT (a, owner-scoped)", selectors.CONNECT_BUTTON_FMT.format(name=name)),
                    ("FOLLOW (button, owner-scoped)", selectors.FOLLOW_BUTTON_FMT.format(name=name)),
                    ("MORE_MENU_BUTTON", selectors.MORE_MENU_BUTTON),
                ]:
                    n = len(page.query_selector_all(sel))
                    print(f"  {lbl}: count={n}")
            time.sleep(random.uniform(3, 6))

        ctx.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: reprobe_profiles.py URL1 URL2 ...")
    main(sys.argv[1:])
