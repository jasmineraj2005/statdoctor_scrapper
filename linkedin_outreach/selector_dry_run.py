"""Selector audit run — drives the existing searcher against N subset practitioners,
navigates to each matched profile, and probes every selector in li_selectors.py.

Writes a report to linkedin_outreach/selector_audit.md. No clicks. No connects.
Stops immediately on any captcha/checkpoint.

Usage:
    python selector_dry_run.py --limit 2
    python selector_dry_run.py --limit 10
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

import config  # noqa: E402
import auth  # noqa: E402
import searcher  # noqa: E402
import li_selectors as selectors  # noqa: E402
from searcher import RateLimitError  # noqa: E402

SUBSET_CSV = THIS_DIR / "data" / "vic_high_yield_subset.csv"
AUDIT_MD = THIS_DIR / "selector_audit.md"

# Old class-based selectors are documented as DEAD in li_selectors.py.
# This script now only validates the rewritten semantic selectors.

POSTCODE_TO_STATE = {
    "3": "VIC", "2": "NSW", "4": "QLD", "5": "SA", "6": "WA", "7": "TAS", "0": "NT",
}


def postcode_to_state(postcode: str) -> str:
    if not postcode:
        return "VIC"
    return POSTCODE_TO_STATE.get(postcode[0], "VIC")


SUBURB_RX = re.compile(r"^\s*([^,]+),")


def adapt_row(row: dict) -> dict:
    """Map subset CSV row → searcher's expected practitioner dict."""
    loc = row.get("location", "") or ""
    m = SUBURB_RX.match(loc)
    suburb = m.group(1).strip() if m else ""
    return {
        "practitioner_id": row["practitioner_id"],
        "name": row["name"],
        "suburb": suburb,
        "state": postcode_to_state(row.get("postcode_searched", "")),
        "specialities": row.get("speciality", ""),
        "registration_type": "Specialist",
    }


def probe(page, name: str, selector_str: str) -> dict:
    try:
        els = page.query_selector_all(selector_str)
        sample = ""
        if els:
            try:
                sample = (els[0].inner_text() or "").strip()[:120]
            except Exception:
                sample = "<could not read text>"
            if not sample:
                try:
                    sample = (els[0].get_attribute("aria-label") or "")[:120]
                except Exception:
                    pass
        return {"name": name, "selector": selector_str, "count": len(els), "sample": sample}
    except Exception as e:
        return {"name": name, "selector": selector_str, "count": -1, "sample": f"ERR: {e}"}


def probe_group(page, label: str, candidates: list[str]) -> list[dict]:
    rows = []
    for sel in candidates:
        rows.append(probe(page, label, sel))
    return rows


def run(limit: int) -> None:
    if not SUBSET_CSV.exists():
        sys.exit(f"subset CSV not found: {SUBSET_CSV}")
    df = pd.read_csv(SUBSET_CSV, dtype=str).fillna("")
    rows = df.head(limit).to_dict("records")

    report_lines: list[str] = []
    report_lines.append(f"# Selector audit — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"Limit: {limit}")
    report_lines.append("")

    load_dotenv()

    email = os.environ.get("LINKEDIN_EMAIL", "")
    password = os.environ.get("LINKEDIN_PASSWORD", "")

    with sync_playwright() as pw:
        profile_dir = str(THIS_DIR / "browser_profile")
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
            page = auth.ensure_logged_in(page, email, password)
        except Exception as e:
            sys.exit(f"auth failed: {e}")

        for i, raw in enumerate(rows, 1):
            pr = adapt_row(raw)
            report_lines.append(f"## {i}. {pr['name']} ({pr['practitioner_id']})")
            report_lines.append(f"- speciality: {pr['specialities']}")
            report_lines.append(f"- suburb/state: {pr['suburb']} / {pr['state']}")
            try:
                profile = searcher.search_and_find_profile(page, pr)
            except RateLimitError as e:
                report_lines.append(f"- **STOP** rate-limit/CAPTCHA: {e}")
                break
            except Exception as e:
                report_lines.append(f"- search error: {type(e).__name__}: {e}")
                profile = None

            if not profile:
                report_lines.append("- search result: **no match**")
                time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))
                continue

            report_lines.append(
                f"- matched: [{profile['name']}]({profile['url']}) — {profile.get('headline', '')[:80]}"
            )

            # Navigate to the profile page and probe selectors
            try:
                page.goto(profile["url"], wait_until="domcontentloaded", timeout=30_000)
                time.sleep(random.uniform(3, 6))
                # Scroll down a bit to trigger lazy-render of top-card
                page.mouse.wheel(0, 400)
                time.sleep(random.uniform(1.5, 2.5))
                page.mouse.wheel(0, -400)
                time.sleep(random.uniform(1.0, 1.5))
                # Wait for h1 to actually exist (up to 10s)
                try:
                    page.wait_for_selector("main h1, main section h1", timeout=10_000)
                except Exception:
                    pass
            except Exception as e:
                report_lines.append(f"- profile nav error: {e}")
                continue

            # check for captcha on profile page too
            if searcher._is_rate_limited(page):
                report_lines.append("- **STOP** rate-limit/CAPTCHA on profile page")
                break

            # Dump HTML + screenshot so we can inspect structure
            dump_dir = THIS_DIR.parent / "dry_run_debug"
            dump_dir.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            dump_base = dump_dir / f"{stamp}_{pr['practitioner_id']}_profile"
            try:
                (dump_base.with_suffix(".html")).write_text(page.content())
                page.screenshot(path=str(dump_base.with_suffix(".png")), full_page=True)
                report_lines.append(f"- dumped: `{dump_base.name}.html` / `.png`")
            except Exception as e:
                report_lines.append(f"- dump error: {e}")

            # Aggregate page-structure counts
            try:
                agg = page.evaluate("""() => ({
                    h1: document.querySelectorAll('h1').length,
                    main_h1: document.querySelectorAll('main h1').length,
                    buttons: document.querySelectorAll('button').length,
                    button_labels: Array.from(document.querySelectorAll('button[aria-label]'))
                        .slice(0, 30).map(b => b.getAttribute('aria-label')),
                    modal_present: !!document.querySelector('[role="dialog"]'),
                    url: location.href,
                })""")
                report_lines.append(f"- url: {agg['url']}")
                report_lines.append(
                    f"- page counts: h1={agg['h1']} main_h1={agg['main_h1']} "
                    f"buttons={agg['buttons']} modal={agg['modal_present']}"
                )
                report_lines.append(
                    "- sample button aria-labels: "
                    + ", ".join(f"`{b}`" for b in agg["button_labels"] if b)[:800]
                )
            except Exception as e:
                report_lines.append(f"- aggregate probe error: {e}")

            # NEW — validate the semantic selectors we just rewrote
            report_lines.append("")
            report_lines.append("### NEW semantic-selector probes")
            try:
                data = page.evaluate(selectors.PROFILE_DATA_JS)
                report_lines.append(f"- PROFILE_DATA_JS.name:     `{data.get('name','')}`")
                report_lines.append(f"- PROFILE_DATA_JS.headline: `{data.get('headline','')[:100]}`")
                report_lines.append(f"- PROFILE_DATA_JS.location: `{data.get('location','')[:80]}`")
                report_lines.append(f"- PROFILE_DATA_JS.url:      `{data.get('canonical_url','')}`")
            except Exception as e:
                report_lines.append(f"- PROFILE_DATA_JS error: {e}")

            name = (data or {}).get("name") or profile.get("name") or ""
            if name:
                connect_sel = selectors.CONNECT_BUTTON_FMT.format(name=name)
                follow_sel  = selectors.FOLLOW_BUTTON_FMT.format(name=name)
                more_connect_sel = selectors.MORE_MENU_CONNECT_FMT.format(name=name)
                for lbl, s in [
                    ("CONNECT (top-card, owner-scoped)", connect_sel),
                    ("FOLLOW (top-card, owner-scoped)", follow_sel),
                    ("MORE_MENU_BUTTON", selectors.MORE_MENU_BUTTON),
                    ("CONNECT in More menu (pre-open)", more_connect_sel),
                ]:
                    r = probe(page, lbl, s)
                    report_lines.append(
                        f"- {lbl}: count={r['count']}"
                        f"{' sample=`'+r['sample']+'`' if r['sample'] else ''}"
                    )

            report_lines.append("")
            time.sleep(random.uniform(*config.DELAY_BETWEEN_SEARCHES_SEC))

        context.close()

    AUDIT_MD.write_text("\n".join(report_lines))
    print(f"\nwrote audit to {AUDIT_MD}")


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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2)
    args = ap.parse_args()
    run(args.limit)
