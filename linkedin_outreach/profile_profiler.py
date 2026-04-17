"""Profile profiler — scrapes the signals the influencer classifier needs.

Inputs:  a Playwright page (already authed) + a profile URL
Outputs: a dict with:
  name, headline, location, followers, connections, creator_mode, bio,
  bio_signals[], post_count_90d, last_post_date (ISO or None), has_video_90d,
  avg_likes_per_post, fail_reason

Failure mode: every extraction is wrapped; missing data returns empty/zero
rather than raising. `fail_reason` is set only for nav/auth failures that
invalidate the whole record. A sparse profile (no posts, empty bio) is
NOT a failure — it's just a profile the classifier will reject on hard
filters.
"""
from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

import li_selectors as selectors
from _visit_tracker import mark_visited


def _dump_activity(page: Page, handle: str) -> None:
    """When PROFILER_DEBUG=1, save the current page HTML to dry_run_debug/
    so we can inspect post-card DOM offline."""
    try:
        dump_dir = Path(__file__).resolve().parent.parent / "dry_run_debug"
        dump_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dump = dump_dir / f"{stamp}_{handle}_activity.html"
        dump.write_text(page.content())
    except Exception:
        pass


# ── Spec-locked bio keywords for soft-score ──────────────────────────────────
BIO_KEYWORDS = ["speaker", "author", "educator", "podcast", "media", "researcher"]


# ── Page-scoped JS extractors ────────────────────────────────────────────────

PROFILE_OVERVIEW_JS = r"""
() => {
  const main = document.querySelector("main");
  if (!main) return {};
  const mainText = main.innerText || "";

  // Followers ("1,146 followers" or "500+ followers")
  const fm = mainText.match(/([\d,]+\+?)\s+followers?\b/i);
  // Connections ("500+ connections" / "342 connections")
  const cm = mainText.match(/([\d,]+\+?)\s+connections?\b/i);

  // About / bio section — the <section> whose first <h2> is "About"
  let about = "";
  const aboutH2 = Array.from(main.querySelectorAll("h2"))
    .find(h => ((h.innerText||"").trim().toLowerCase() === "about"));
  if (aboutH2) {
    const section = aboutH2.closest("section") || aboutH2.parentElement;
    if (section) {
      const lines = Array.from(section.querySelectorAll("p, span, div"))
        .map(e => (e.innerText || "").trim())
        .filter(t => t && t.length >= 25 && t.length <= 4000);
      const seen = new Set();
      const uniq = [];
      for (const l of lines) { if (!seen.has(l)) { seen.add(l); uniq.push(l); } }
      about = uniq.join(" ").slice(0, 3000);
    }
  }

  return {
    followers_text:   fm ? fm[1] : "",
    connections_text: cm ? cm[1] : "",
    about:            about,
  };
}
"""

# Posts on /recent-activity/all/. Uses robust structural selectors: any element
# whose data-urn references an activity, and falls back to <article>/feed cards.
ACTIVITY_POSTS_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const candidates = document.querySelectorAll(
    "div[data-urn*='urn:li:activity'], " +
    "div[data-id*='urn:li:activity'], " +
    ".feed-shared-update-v2, " +
    "main article"
  );

  // LinkedIn stores post age in screen-reader-only spans like
  // "7 months ago • Visible to anyone on or off LinkedIn". innerText skips
  // those (CSS-hidden) so we must use textContent AND specifically look inside
  // .visually-hidden spans to reliably parse the date.
  const agoRx  = /(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago\b/i;
  const shortRx = /\b(\d+)\s*(h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks|mo|mos|month|months|y|yr|yrs|year|years)\b/i;
  const unitMap = {
    minute: "h", hour: "h", day: "d", week: "w", month: "mo", year: "y",
  };

  for (const node of candidates) {
    const urn = node.getAttribute("data-urn")
             || node.getAttribute("data-id")
             || "";
    const fullText = (node.textContent || "").trim();
    const visibleText = (node.innerText || "").trim();
    const key = urn || visibleText.slice(0, 60);
    if (!key) continue;
    if (seen.has(key)) continue;
    seen.add(key);

    // 1. Try the rich "N months ago" pattern (most reliable — screen-reader text)
    let relN = 0, relU = "";
    let m = fullText.match(agoRx);
    if (m) {
      relN = parseInt(m[1], 10);
      relU = unitMap[m[2].toLowerCase()] || "";
    } else {
      // Fallback: short form "1d", "5mo" in visible overlay text
      m = visibleText.match(shortRx);
      if (m) {
        relN = parseInt(m[1], 10);
        const u = m[2].toLowerCase();
        if (u.startsWith("h")) relU = "h";
        else if (u.startsWith("day") || u === "d") relU = "d";
        else if (u.startsWith("w")) relU = "w";
        else if (u.startsWith("mo")) relU = "mo";
        else if (u.startsWith("y")) relU = "y";
      }
    }
    if (!relU) continue;   // not a post

    // Minute-granular — treat as "0d" for 90-day windowing
    if (relU === "h" && m && /minute/i.test(m[0])) relU = "h";

    const rel = `${relN}${relU}`;

    // Reshare detection — the repost banner sits in the card header and
    // appears in both textContent and innerText.
    const head = fullText.slice(0, 250).toLowerCase();
    const isReshare = /reposted this|shared this/.test(head);

    // Reactions — LinkedIn shows "N reactions" or just "N" near a reaction icon
    const rxM = fullText.match(/([\d,]+)\s+reactions?\b/i)
              || fullText.match(/([\d,]+)\s+likes?\b/i);
    const reactions = rxM ? parseInt(rxM[1].replace(/,/g, ""), 10) : 0;

    // Video detection — robust to class hashing: <video> tag, data-video-url,
    // or screen-reader text "play video".
    const hasVideo =
      !!node.querySelector("video") ||
      !!node.querySelector("[data-video-url]") ||
      /\bplay video\b/i.test(fullText);

    out.push({
      rel_date: rel,
      reshare: isReshare,
      reactions: reactions,
      has_video: hasVideo,
      preview: visibleText.slice(0, 160).replace(/\s+/g, " "),
    });
  }
  return out;
}
"""


# ── Public API ───────────────────────────────────────────────────────────────

def profile(page: Page, profile_url: str,
            verifier_confidence: str = "") -> dict[str, Any]:
    """Scrape a LinkedIn profile + its recent activity. Graceful on missing data.

    `verifier_confidence` passes through the matcher's confidence tag
    ("high" / "medium" / "") so the classifier downstream can be stricter
    on empty-location-but-strong-name matches.
    """
    result: dict[str, Any] = {
        "url": profile_url,
        "name": "",
        "headline": "",
        "location": "",
        "followers": 0,
        "connections": 0,
        "creator_mode": False,
        "bio": "",
        "bio_signals": [],
        "post_count_90d": 0,
        "last_post_date": None,
        "has_video_90d": False,
        "avg_likes_per_post": 0.0,
        "verifier_confidence": verifier_confidence,
        "fail_reason": "",
    }

    # ── 1. Profile page ──
    mark_visited(profile_url)
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(random.uniform(3, 6))
        page.mouse.wheel(0, 400)
        time.sleep(random.uniform(1.5, 2.5))
        page.mouse.wheel(0, -400)
        time.sleep(random.uniform(1.0, 1.5))
        try:
            page.wait_for_selector("main h2", timeout=10_000)
        except Exception:
            pass
    except Exception as e:
        result["fail_reason"] = f"profile_nav: {type(e).__name__}"
        return result

    # Top-card fields (reuse selector module)
    try:
        card = page.evaluate(selectors.PROFILE_DATA_JS) or {}
        result["name"]     = card.get("name", "") or ""
        result["headline"] = card.get("headline", "") or ""
        result["location"] = card.get("location", "") or ""
    except Exception as e:
        result["fail_reason"] = f"profile_data_js: {type(e).__name__}"

    # Overview: followers, connections, about-text
    followers_text = ""
    try:
        ov = page.evaluate(PROFILE_OVERVIEW_JS) or {}
        followers_text        = ov.get("followers_text", "") or ""
        result["followers"]   = _parse_count(followers_text)
        result["connections"] = _parse_count(ov.get("connections_text", ""))
        result["bio"]         = ov.get("about", "") or ""
    except Exception:
        pass

    # Creator-mode signal: LinkedIn only surfaces "<N> followers" text on
    # profiles that have Creator Mode enabled (validated across 4 live profiles
    # — Jason/Susie/Philip show followers text and creator-mode affordances;
    # Peter does not show followers text). So non-empty followers_text is
    # the single clean indicator. Follow button existence is redundant.
    result["creator_mode"] = bool(followers_text)

    # Bio signals use headline + about so we catch self-descriptors wherever.
    result["bio_signals"] = _bio_signals(
        " ".join([result.get("bio", ""), result.get("headline", "")])
    )

    # ── 2. Recent activity ──
    handle = _extract_handle(profile_url)
    if not handle:
        return result

    activity_url = f"https://www.linkedin.com/in/{handle}/recent-activity/all/"
    mark_visited(activity_url)
    try:
        page.goto(activity_url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(random.uniform(3, 6))
        # Scroll enough to trigger ~2-3 pages of posts, plenty to cover 90 days
        for _ in range(4):
            page.mouse.wheel(0, 1800)
            time.sleep(random.uniform(1.5, 3.0))
        posts = page.evaluate(ACTIVITY_POSTS_JS) or []
        if os.environ.get("PROFILER_DEBUG"):
            _dump_activity(page, handle)
    except Exception as e:
        result.setdefault("_debug", {})["activity_error"] = f"{type(e).__name__}: {e}"
        posts = []
    result.setdefault("_debug", {})["activity_raw_count"] = len(posts)

    now = datetime.now()
    cutoff_90 = now - timedelta(days=90)
    parsed = []
    for p in posts:
        try:
            p["abs_date"] = _parse_rel_date(p.get("rel_date", ""), now)
            parsed.append(p)
        except Exception:
            continue

    originals_90 = [p for p in parsed if p["abs_date"] >= cutoff_90 and not p.get("reshare")]
    result["post_count_90d"] = len(originals_90)
    result["has_video_90d"]  = any(p.get("has_video") for p in originals_90)
    if originals_90:
        total_reactions = sum(int(p.get("reactions", 0) or 0) for p in originals_90)
        result["avg_likes_per_post"] = round(total_reactions / len(originals_90), 2)

    # last_post_date spans all posts we saw (the 60-day hard filter is applied
    # by the classifier, not here — profiler's job is to report ground truth).
    if parsed:
        result["last_post_date"] = max(p["abs_date"] for p in parsed).date().isoformat()

    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_count(text: str) -> int:
    if not text:
        return 0
    cleaned = text.replace(",", "").replace("+", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _parse_rel_date(rel: str, now: datetime) -> datetime:
    """Convert LinkedIn's relative post time ('3d', '5mo', '1y') to a datetime.

    Conservative: '1mo' → 30 days; '1y' → 365 days. Good enough for the 90-day
    cutoff — an off-by-a-few-days reshare near the boundary doesn't change
    the influencer verdict.
    """
    m = re.match(r"(\d+)(h|d|w|mo|y)$", rel)
    if not m:
        return now
    n, unit = int(m.group(1)), m.group(2)
    return {
        "h":  now - timedelta(hours=n),
        "d":  now - timedelta(days=n),
        "w":  now - timedelta(weeks=n),
        "mo": now - timedelta(days=n * 30),
        "y":  now - timedelta(days=n * 365),
    }[unit]


def _bio_signals(text: str) -> list[str]:
    if not text:
        return []
    lc = text.lower()
    return [k for k in BIO_KEYWORDS if k in lc]


def _extract_handle(url: str) -> str:
    m = re.search(r"/in/([^/?#]+)", url)
    return m.group(1) if m else ""
