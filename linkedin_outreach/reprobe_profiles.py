"""Re-probe selectors on a list of known profile URLs or cached HTML dumps.

Default path ("live") opens each URL in a logged-in Chromium — skips the
search step but still hits LinkedIn. Prefer `--cached` whenever the profile
is already dumped under dry_run_debug/ to keep the visit budget low.

Usage:
    # live — only for fresh DOM / URLs we haven't dumped
    python reprobe_profiles.py URL1 URL2 ...

    # offline — feeds saved HTML into a blank page
    python reprobe_profiles.py --cached dry_run_debug/20260417_...html ...
"""
from __future__ import annotations

import os
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import auth  # noqa: E402
import config  # noqa: E402
import li_selectors as selectors  # noqa: E402
from _visit_tracker import is_hot, mark_visited  # noqa: E402


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


def _probe_current_page(page, label: str) -> None:
    """Run the full selector probe battery against whatever is currently
    loaded in `page` (either live URL or injected cached HTML)."""
    print(f"\n=== {label} ===")
    try:
        page.wait_for_selector("main h2", timeout=5_000)
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


def _run_live(urls: list[str]) -> None:
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
            if is_hot(url):
                print(f"\n[SKIP] {url} is HOT (visited <48h ago). Use --cached.")
                continue
            mark_visited(url)
            nav_start = time.time()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            elapsed = time.time() - nav_start
            if elapsed > 30:
                print(f"[STOP] nav took {elapsed:.1f}s — possible throttle. Aborting.")
                break
            time.sleep(random.uniform(3, 6))
            page.mouse.wheel(0, 400)
            time.sleep(random.uniform(1.5, 2.5))
            page.mouse.wheel(0, -400)
            _probe_current_page(page, url)
            time.sleep(random.uniform(3, 6))

        ctx.close()


def _run_cached(html_paths: list[str]) -> None:
    """Offline mode: feed saved HTML into a blank page and run the probes.
    Reuses our Playwright stack so the JS selectors run in a real DOM, just
    without hitting LinkedIn."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": config.BROWSER_WIDTH,
                                             "height": config.BROWSER_HEIGHT})
        page = ctx.new_page()
        for p in html_paths:
            path = Path(p).expanduser().resolve()
            if not path.exists():
                print(f"[SKIP] {path}: not found")
                continue
            html = path.read_text()
            # LinkedIn pages ship a Trusted-Types CSP that blocks Playwright's
            # set_content (it calls document.write internally). Strip ALL meta
            # tags that mention trusted-types — both the name-tagged one and
            # the http-equiv Content-Security-Policy twin. Safe because we
            # don't execute any of the page's scripts.
            html = re.sub(
                r'<meta[^>]*trusted-types[^>]*/?>',
                "", html, flags=re.I,
            )
            page.set_content(html, wait_until="domcontentloaded")
            _probe_current_page(page, f"cached:{path.name}")
        ctx.close()
        browser.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: reprobe_profiles.py [--cached] PATH_OR_URL ...")
    if args[0] == "--cached":
        _run_cached(args[1:])
    else:
        _run_live(args)
