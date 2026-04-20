"""Step-6 prerequisite: live dry-run of the More-menu → Connect nav path.

Picks ONE non-HOT profile, navigates to it, and probes the selectors that
connector.py will rely on. Does NOT click Connect, does NOT send. If the
top-card Connect anchor is present we report Connect-primary UI; if not we
click the "More" overflow button and probe for Connect inside the dropdown.

Why this script exists: `li_selectors.MORE_MENU_CONNECT_FMT` is a union of
four DOM shapes LinkedIn has shipped across 2024-2026; we need one live
confirmation before the connector writes real clicks.

Run:
    python3 _more_menu_probe.py             # default seed 23
    python3 _more_menu_probe.py 29          # alternate seed

No connect is sent. Daily profile-visit budget: 1-3 visits max (one per
sampled practitioner until we find a non-HOT match).
"""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import auth                                             # noqa: E402
import config                                           # noqa: E402
import li_selectors as selectors                        # noqa: E402
import searcher                                         # noqa: E402
from _visit_tracker import is_hot, mark_visited        # noqa: E402
from searcher import RateLimitError                     # noqa: E402
from step4d_audit import adapt_row, load_dotenv         # noqa: E402

SUBSET_CSV = THIS_DIR / "data" / "vic_high_yield_subset.csv"

# Hard cap on attempts — we only need ONE probe. Walking more practitioners
# than this without finding a non-HOT match suggests the HOT set is too
# saturated; better to stop and inspect than keep burning search budget.
MAX_ATTEMPTS = 4


def probe_profile(page, url: str, owner_name: str) -> None:
    """Probe selectors on the authenticated profile page. No clicks on Connect."""
    print(f"\n--- probing: {owner_name} @ {url} ---")

    connect_sel = selectors.CONNECT_BUTTON_FMT.format(name=owner_name)
    follow_sel  = selectors.FOLLOW_BUTTON_FMT.format(name=owner_name)

    top_connect = page.query_selector_all(connect_sel)
    top_follow  = page.query_selector_all(follow_sel)
    more_btn    = page.query_selector(selectors.MORE_MENU_BUTTON)

    print(f"  CONNECT_BUTTON_FMT count: {len(top_connect)}")
    print(f"  FOLLOW_BUTTON_FMT count:  {len(top_follow)}")
    print(f"  MORE_MENU_BUTTON found:   {bool(more_btn)}")

    if top_connect:
        print("  >>> Connect-primary UI. CONNECT_BUTTON_FMT matches top card.")
        print("  >>> No More-menu fallback needed on this profile.")
        return

    if not more_btn:
        print("  >>> NEITHER Connect nor More menu. Likely Message/Pending state.")
        return

    print("  clicking More menu…")
    more_btn.click()
    time.sleep(random.uniform(1.0, 2.0))

    more_connect_sel = selectors.MORE_MENU_CONNECT_FMT.format(name=owner_name)
    in_menu = page.query_selector_all(more_connect_sel)
    print(f"  MORE_MENU_CONNECT_FMT count after open: {len(in_menu)}")

    if in_menu:
        label = (in_menu[0].get_attribute("aria-label") or "")[:120]
        print(f"  >>> More-menu fallback WORKS. First match aria-label='{label}'")
        return

    # Fallback: dump all candidate menu-item aria-labels so we can refine
    # MORE_MENU_CONNECT_FMT if the union doesn't cover the 2026 shape.
    print("  >>> More menu opened but NO Connect match. Menu aria-labels:")
    candidates = page.query_selector_all(
        "div[role='button'][aria-label],"
        " div[role='menuitem'][aria-label],"
        " li[role='menuitem'][aria-label],"
        " a[aria-label]"
    )
    labels = []
    for el in candidates:
        label = (el.get_attribute("aria-label") or "").strip()
        if label and label not in labels:
            labels.append(label)
    for l in labels[:40]:
        print(f"    - {l[:120]}")


def run(seed: int) -> None:
    if not SUBSET_CSV.exists():
        sys.exit(f"subset CSV not found: {SUBSET_CSV}")

    df = pd.read_csv(SUBSET_CSV, dtype=str).fillna("")
    load_dotenv()
    email    = os.environ.get("LINKEDIN_EMAIL", "")
    password = os.environ.get("LINKEDIN_PASSWORD", "")

    sample = df.sample(n=MAX_ATTEMPTS, random_state=seed).to_dict("records")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(THIS_DIR / "browser_profile"),
            headless=config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": config.BROWSER_WIDTH, "height": config.BROWSER_HEIGHT},
            user_agent=config.USER_AGENT,
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page = auth.ensure_logged_in(page, email, password)

        for idx, raw in enumerate(sample, 1):
            pr = adapt_row(raw)
            print(f"\n[{idx}/{MAX_ATTEMPTS}] {pr['name']} ({pr['practitioner_id']})")
            try:
                matched = searcher.search_and_find_profile(page, pr)
            except RateLimitError as e:
                print(f"STOP — rate-limit: {e}")
                break
            except Exception as e:
                print(f"  search error: {type(e).__name__}: {e}")
                continue

            if not matched:
                print("  no match")
                time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))
                continue

            url = matched["url"]
            if is_hot(url):
                print(f"  HOT (48h cool-down) — skip: {url}")
                time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))
                continue

            print(f"  matched → {url}")
            mark_visited(url)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                time.sleep(random.uniform(3, 6))
                page.mouse.wheel(0, 400); time.sleep(random.uniform(1.5, 2.5))
                page.mouse.wheel(0, -400); time.sleep(random.uniform(1.0, 1.5))
                try:
                    page.wait_for_selector("main h2", timeout=10_000)
                except Exception:
                    pass
            except Exception as e:
                print(f"  nav error: {e}")
                continue

            if searcher._is_rate_limited(page):
                print("STOP — rate-limit on profile page")
                break

            data = page.evaluate(selectors.PROFILE_DATA_JS) or {}
            owner_name = (data.get("name") or matched.get("name") or "").strip()
            if not owner_name:
                print("  could not read owner name — skip")
                continue

            probe_profile(page, url, owner_name)
            break  # exactly one probe per user spec
        else:
            print(f"\nNo suitable non-HOT profile found in {MAX_ATTEMPTS} attempts. "
                  f"Try another seed.")

        ctx.close()


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 23
    run(seed)
