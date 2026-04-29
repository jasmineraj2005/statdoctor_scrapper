"""Probe the invite modal DOM after clicking Connect.

Day-4 batch #3 hit a new failure mode: connector clicked Connect, the
modal opened, but neither SEND_WITHOUT_NOTE_BUTTON nor SEND_NOW_BUTTON
matched and we returned STATUS_ERROR "no send button found." Two real
sends (Harris, Naidoo) errored this way; both turned out to be 1st-degree
on follow-up, suggesting LinkedIn shipped a new modal shape that may have
auto-fired the invite or just renamed the Send button.

This probe:
  1. Picks ONE non-HOT profile from the high-yield subset.
  2. Navigates, finds the top-card Connect anchor, clicks it.
  3. Waits for [role="dialog"] to be visible.
  4. Dumps every button / role / aria-label / text inside the dialog to
     stdout AND to _modal_dump_<slug>.json.
  5. **Dismisses the modal without clicking Send** (Escape + close-X).

Spec-lock observed: we do NOT click any Send button — purely diagnostic.

Run:
    python3 _modal_send_probe.py             # default seed 23
    python3 _modal_send_probe.py 29          # alternate seed
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeoutError

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import auth                                             # noqa: E402
import config                                           # noqa: E402
import li_selectors as selectors                        # noqa: E402
import searcher                                         # noqa: E402
from _visit_tracker import is_hot, mark_visited        # noqa: E402
from searcher import RateLimitError                     # noqa: E402
from step4d_audit import adapt_row, load_dotenv         # noqa: E402

SUBSET_CSV   = THIS_DIR / "data" / "vic_high_yield_subset.csv"
MAX_ATTEMPTS = 5
DIALOG_SEL   = '[role="dialog"]'


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/in/")[-1].split("?")[0] or "unknown"


def _click_top_card_connect(page, owner_name: str) -> bool:
    """Mirror connector._click_connect's top-card path. Returns True on click."""
    sel = selectors.CONNECT_BUTTON_FMT.format(name=owner_name)
    try:
        btn = page.locator(sel).first
        btn.wait_for(state="visible", timeout=6_000)
        btn.scroll_into_view_if_needed(timeout=2_000)
        btn.click()
        return True
    except PwTimeoutError:
        return False
    except Exception as e:
        print(f"  top-card connect click error: {type(e).__name__}: {e}")
        return False


def _click_more_menu_connect(page, owner_name: str) -> bool:
    """Open More menu, then click Connect (aria union, then text fallback)."""
    more = page.locator(selectors.MORE_MENU_BUTTON).first
    try:
        more.wait_for(state="visible", timeout=4_000)
        more.click()
    except PwTimeoutError:
        return False
    except Exception as e:
        print(f"  more-menu open error: {type(e).__name__}: {e}")
        return False
    time.sleep(random.uniform(1.0, 1.6))
    try:
        sel = selectors.MORE_MENU_CONNECT_FMT.format(name=owner_name)
        item = page.locator(sel).first
        item.wait_for(state="visible", timeout=4_000)
        item.click()
        return True
    except PwTimeoutError:
        pass
    except Exception:
        pass
    try:
        item = (page.locator("div[role='menu'], ul[role='menu'], "
                             "div.artdeco-dropdown__content")
                .locator("a[role='menuitem'], div[role='menuitem'], "
                         "li[role='menuitem'], button[role='menuitem']")
                .filter(has_text="Connect")
                .first)
        item.wait_for(state="visible", timeout=4_000)
        item.click()
        return True
    except PwTimeoutError:
        return False
    except Exception:
        return False


def _open_invite_modal(page, owner_name: str) -> bool:
    if _click_top_card_connect(page, owner_name):
        return True
    print("  no top-card Connect; trying More-menu fallback")
    return _click_more_menu_connect(page, owner_name)


def _dump_dialog(page) -> dict:
    """Walk every interactive descendant of the dialog and report shape."""
    js = """() => {
      const dlg = document.querySelector('[role="dialog"]');
      if (!dlg) return {found: false};
      const elements = dlg.querySelectorAll(
        'button, a, input, [role="button"], [role="dialog"], [role="heading"]'
      );
      const items = [];
      for (const el of elements) {
        items.push({
          tag:        el.tagName.toLowerCase(),
          role:       el.getAttribute('role') || '',
          aria_label: el.getAttribute('aria-label') || '',
          name:       el.getAttribute('name') || '',
          type:       el.getAttribute('type') || '',
          data_test:  el.getAttribute('data-test-id') || el.getAttribute('data-testid') || '',
          text:       (el.innerText || '').trim().slice(0, 120),
          disabled:   el.disabled || el.getAttribute('aria-disabled') === 'true',
          visible:    el.offsetParent !== null,
        });
      }
      const heading = dlg.querySelector('[role="heading"], h2, h3');
      return {
        found: true,
        heading_text: heading ? (heading.innerText || '').trim() : '',
        outer_aria_label: dlg.getAttribute('aria-label') || '',
        outer_aria_labelledby: dlg.getAttribute('aria-labelledby') || '',
        item_count: items.length,
        items,
      };
    }"""
    return page.evaluate(js)


def _dismiss(page) -> None:
    try:
        page.locator(selectors.MODAL_CLOSE_BUTTON).first.click(timeout=2_000)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass


def probe(page, url: str, owner_name: str) -> dict | None:
    print(f"\n--- modal probe: {owner_name} @ {url} ---")
    if not _open_invite_modal(page, owner_name):
        print("  could not open invite modal (no top-card Connect, no More-menu Connect)")
        return None

    # Wait for the dialog. Short timeout — if the modal didn't open we
    # bail and try another seed.
    try:
        page.locator(DIALOG_SEL).first.wait_for(state="visible", timeout=6_000)
    except PwTimeoutError:
        print("  Connect clicked but no [role=dialog] appeared in 6s — bailing")
        return None

    time.sleep(random.uniform(0.8, 1.4))  # let any animation settle
    dump = _dump_dialog(page)
    print(f"  dialog heading: {dump.get('heading_text','')}")
    print(f"  dialog aria-label: {dump.get('outer_aria_label','')}")
    print(f"  items: {dump.get('item_count',0)}")
    for it in dump.get("items", []):
        if not it["visible"]:
            continue
        print(
            f"    [{it['tag']}] role={it['role'] or '-'} "
            f"aria='{it['aria_label'][:70]}' "
            f"text='{it['text'][:60]}' "
            f"data-test='{it['data_test']}'"
        )
    _dismiss(page)
    return dump


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
                print(f"  HOT — skip: {url}")
                time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))
                continue

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

            dump = probe(page, url, owner_name)
            if dump and dump.get("found"):
                out_path = THIS_DIR / f"_modal_dump_{_slug_from_url(url)}.json"
                out_path.write_text(json.dumps(dump, indent=2))
                print(f"\n>>> dump saved: {out_path.name}")
                break
        else:
            print(f"\nNo suitable non-HOT profile in {MAX_ATTEMPTS} attempts. "
                  f"Try another seed.")

        ctx.close()


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 23
    run(seed)
