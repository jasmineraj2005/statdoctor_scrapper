#!/usr/bin/env python3
"""Probe: open More menu on a live profile and dump every interactive element
in the dropdown. Diagnostic for the Day-2 connect_unavailable failures where
MORE_MENU_CONNECT_FMT's 4-way union didn't match.

Bypasses is_hot (user-authorised). Walks 2 profiles from the Day-2 pending
list so we can compare and find a universal selector.
"""
from __future__ import annotations

import json
import os
import time

from playwright.sync_api import sync_playwright

import auth
import config
import li_selectors as selectors

TARGETS = [
    ("Dr Andrew Anthony White",   "https://www.linkedin.com/in/andrew-white-69253041"),
    ("Dr Abhilash Balakrishnan",  "https://www.linkedin.com/in/dr-abhilash-balakrishnan-1aa8b111"),
]


DUMP_JS = r"""
() => {
  // Find the visible dropdown that just opened. LinkedIn renders it as
  // a <div role="menu"> or sometimes an artdeco-dropdown__content block.
  const menus = Array.from(document.querySelectorAll(
    "div[role='menu'], div.artdeco-dropdown__content, ul[role='menu']"
  )).filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });

  const out = {menus_found: menus.length, items: []};
  for (const menu of menus) {
    const candidates = menu.querySelectorAll(
      "a, button, div[role='button'], div[role='menuitem'], li[role='menuitem'], span[role='menuitem']"
    );
    for (const el of candidates) {
      const rect = el.getBoundingClientRect();
      out.items.push({
        tag:          el.tagName.toLowerCase(),
        role:         el.getAttribute("role") || "",
        aria_label:   el.getAttribute("aria-label") || "",
        text:         (el.innerText || "").trim().slice(0, 120),
        href:         el.getAttribute("href") || "",
        classes:      (el.getAttribute("class") || "").slice(0, 80),
        visible:      rect.width > 0 && rect.height > 0,
      });
    }
  }
  return out;
}
"""


def _load_env_file():
    this_dir = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(this_dir, ".env"),
              os.path.join(os.path.dirname(this_dir), ".env")):
        if not os.path.exists(p):
            continue
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
        page = auth.ensure_logged_in(page,
                                     os.environ.get("LINKEDIN_EMAIL", ""),
                                     os.environ.get("LINKEDIN_PASSWORD", ""))

        for name, url in TARGETS:
            print(f"\n\n===== {name} =====")
            print(f"URL: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                print(f"nav failed: {e}")
                continue
            time.sleep(3.5)

            # Check top-card action buttons first (Connect? Follow? Message?)
            try:
                top_actions = page.evaluate(r"""() => {
                    const main = document.querySelector("main");
                    if (!main) return [];
                    const btns = main.querySelectorAll("button, a");
                    const out = [];
                    for (const b of btns) {
                        const txt = (b.innerText || "").trim();
                        const rect = b.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 20 && rect.top < 600 && txt && txt.length < 40) {
                            out.push({
                                tag: b.tagName.toLowerCase(),
                                text: txt,
                                aria_label: b.getAttribute("aria-label") || "",
                            });
                        }
                    }
                    return out.slice(0, 15);
                }""")
                print("\nTOP-CARD ACTIONS:")
                for a in top_actions:
                    print(f"  {a['tag']:6} text='{a['text']}' aria='{a['aria_label']}'")
            except Exception as e:
                print(f"top-card probe failed: {e}")

            # Open More menu
            try:
                more = page.locator(selectors.MORE_MENU_BUTTON).first
                more.wait_for(state="visible", timeout=6_000)
                more.click()
                time.sleep(1.8)
            except Exception as e:
                print(f"More button click failed: {e}")
                continue

            # Dump dropdown contents
            try:
                dump = page.evaluate(DUMP_JS)
            except Exception as e:
                print(f"dump failed: {e}")
                continue

            print(f"\nDROPDOWN MENUS FOUND: {dump.get('menus_found', 0)}")
            items = dump.get("items", [])
            print(f"DROPDOWN ITEMS: {len(items)}")
            for i, it in enumerate(items):
                print(f"  [{i}] <{it['tag']}> role='{it['role']}' "
                      f"aria='{it['aria_label']}' text='{it['text']}'")

            # Save a per-profile JSON dump in case we need to diff later
            out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    f"_dropdown_dump_{url.split('/')[-1]}.json")
            with open(out_path, "w") as f:
                json.dump({"name": name, "url": url, "top_actions": top_actions,
                           "dropdown": dump}, f, indent=2)
            print(f"dump saved → {out_path}")

            # Close dropdown before next profile
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception:
                pass

        context.close()


if __name__ == "__main__":
    _load_env_file()
    run()
