#!/usr/bin/env python3
"""Test the connector fixes against the 2 profiles we probed earlier.

Expected outcome:
  - Dr Andrew White (top-card Connect, title-prefix bug) → modal opens,
    we dismiss before sending (DRY RUN semantic).
  - Dr Abhilash Balakrishnan (More-menu Connect with empty aria-label) →
    modal opens, we dismiss.

We stop at the modal and press Escape — we do NOT send. This is a selector
test only, not a connect run. The user already approved direct access to
these URLs for diagnosis.
"""
from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeoutError

import auth
import config
import connector
import li_selectors as selectors

TARGETS = [
    ("Dr Andrew White", "https://www.linkedin.com/in/andrew-white-69253041", "Andrew White"),
    ("Dr Abhilash Balakrishnan", "https://www.linkedin.com/in/dr-abhilash-balakrishnan-1aa8b111", "Dr. Abhilash Balakrishnan"),
]


def _load_env_file():
    this_dir = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(this_dir, ".env"),
              os.path.join(os.path.dirname(this_dir), ".env")):
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def run():
    with sync_playwright() as pw:
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "browser_profile")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": config.BROWSER_WIDTH, "height": config.BROWSER_HEIGHT},
            user_agent=config.USER_AGENT,
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page = auth.ensure_logged_in(page,
                                     os.environ.get("LINKEDIN_EMAIL", ""),
                                     os.environ.get("LINKEDIN_PASSWORD", ""))

        for label, url, probe_name in TARGETS:
            print(f"\n===== {label} =====")
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(3.5)

            # Resolve owner name the way connector does
            owner = connector._resolve_owner_name(page) or probe_name
            stripped = connector._strip_title_prefix(owner)
            print(f"  resolved owner_name='{owner}' stripped='{stripped}'")

            # Try top-card fix
            result = connector._try_topcard_connect(page, owner, stripped)
            if result is True:
                print(f"  ✅ TOP-CARD path clicked Connect (fix #1 works)")
                # Dismiss the modal without sending
                time.sleep(1.5)
                _dismiss_any_modal(page)
                continue
            if result is False:
                print(f"  ⚠️  TOP-CARD found but click raised — running manual diagnostic")
                # Show match count for each candidate selector
                from li_selectors import CONNECT_BUTTON_FMT
                for nm in (owner, stripped):
                    sel = CONNECT_BUTTON_FMT.format(name=nm)
                    try:
                        cnt = page.locator(sel).count()
                        print(f"    '{sel}' → {cnt} matches")
                    except Exception as e:
                        print(f"    '{sel}' → error: {e}")
                _dismiss_any_modal(page)
                continue
            print(f"  top-card: no Connect anchor; trying More-menu")

            # More-menu fix
            more = page.locator(selectors.MORE_MENU_BUTTON).first
            try:
                more.wait_for(state="visible", timeout=6_000)
                more.click()
                time.sleep(1.5)
            except PwTimeoutError:
                print(f"  ⚠️  No More menu either — profile has neither Connect nor More")
                continue

            clicked, err = connector._resolve_and_click_more_connect(page, stripped)
            if err:
                print(f"  ⚠️  more-menu error: {err}")
            elif clicked:
                print(f"  ✅ MORE-MENU path clicked Connect (fix #2 works)")
                time.sleep(1.5)
                _dismiss_any_modal(page)
            else:
                print(f"  ❌ more-menu opened but no Connect item matched")

        ctx.close()


def _dismiss_any_modal(page):
    """Press Escape until no modal is visible. Never send."""
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
            time.sleep(0.6)
        except Exception:
            break


if __name__ == "__main__":
    _load_env_file()
    run()
