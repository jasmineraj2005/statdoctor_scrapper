"""LinkedIn selectors — semantic (aria-label / role / tag) only.

Background
----------
LinkedIn's 2026 redesign uses opaque hashed classes (e.g. `_8d9617de`) that
change on every deploy. Any class-based selector is guaranteed to rot. The
selectors below rely only on attributes LinkedIn keeps stable for
accessibility: `aria-label`, `role`, semantic tags, and `data-*` hooks.

The audit in `selector_dry_run.py` confirmed the changes:
- Profile pages no longer have `<h1>` (name is the first non-toast `<h2>`).
- Connect button aria-label is `"Invite <Name> to connect"` (not `"Connect"`).
- The "More" overflow menu trigger is `aria-label="More"` (not `"More actions"`).

Search result extraction still lives in `searcher.EXTRACT_RESULTS_JS`; it walks
`a[href*='/in/']` anchors and is DOM-structure-independent.
"""

# ── Search results page (kept for legacy callers of _extract_card_data) ──────
# NOTE: `searcher.search_and_find_profile` uses JS extraction, not these. They
# stay only to keep `_extract_card_data` importable; treat as dead code.
SEARCH_RESULT_CARDS   = "li.reusable-search__result-container"  # DEAD
RESULT_NAME           = "span.entity-result__title-text a span[aria-hidden='true']"  # DEAD
RESULT_LOCATION       = "div.entity-result__secondary-subtitle"  # DEAD
RESULT_HEADLINE       = "div.entity-result__primary-subtitle"  # DEAD
RESULT_PROFILE_LINK   = "span.entity-result__title-text a"  # DEAD

# ── Profile page ──────────────────────────────────────────────────────────────
# The profile owner's name lives in the FIRST non-toast <h2> on the page. The
# unambiguous source is `document.title` → "<Name> | LinkedIn". Use
# `PROFILE_NAME_JS` below to read it without guessing a class.
PROFILE_NAME_JS = r"""
() => {
  const t = document.title || "";
  const m = t.match(/^(.*?)\s*\|\s*LinkedIn/);
  return m ? m[1].trim() : "";
}
"""

# First h2 inside <main> (excludes notification toast h2 which sits in <body>).
PROFILE_NAME_H2 = "main h2"

# Headline: in the 2026 DOM this is a <p> next to the name, NOT an h2. Easiest
# path is the structured evaluator below (`PROFILE_DATA_JS`) which reads it
# from the top-card <a[href*='/in/']> block.
PROFILE_HEADLINE = "main section p"  # fallback probe only, noisy

# Location: same story — <p> near the name, no stable class.
PROFILE_LOCATION = ""  # use PROFILE_DATA_JS

# One-shot structured extractor for the profile top-card. Returns
# {name, headline, location, canonical_url}. Resilient to class changes; only
# relies on `<title>`, `<main>` scoping, and the canonical profile link.
PROFILE_DATA_JS = r"""
() => {
  const out = {name: "", headline: "", location: "", canonical_url: ""};

  // Name from <title> is the most reliable signal.
  const t = document.title || "";
  const nameMatch = t.match(/^(.*?)\s*\|\s*LinkedIn/);
  if (nameMatch) out.name = nameMatch[1].trim();

  const mainEl = document.querySelector("main");
  if (!mainEl) return out;

  // Canonical URL: the FIRST link inside <main> that points at /in/<handle>/
  // WITHOUT an /overlay/ suffix. Walk all matching anchors and pick the cleanest.
  const selfLinks = Array.from(mainEl.querySelectorAll("a[href*='/in/']"));
  for (const a of selfLinks) {
    const href = (a.href || "").split("?")[0].replace(/\/$/, "");
    if (/\/in\/[^\/]+$/.test(href)) { out.canonical_url = href; break; }
  }

  // Degree-badge tokens (· 1st, · 2nd, · 3rd+, 1st degree connection) — filter these out.
  const degreeRx = /^[·•\s]*\d+(?:st|nd|rd|th)(?:\+)?(\s*degree)?\s*$/i;
  // Typical geo string: "Suburb, State, Country" — at least two commas OR ends with a known country.
  const geoRx = /,\s*(Australia|Victoria|New South Wales|Queensland|Western Australia|South Australia|Tasmania|Northern Territory|ACT|NSW|VIC|QLD|WA|SA|TAS|NT)\b/i;

  // Collect candidate lines from the top-card region. Use the first self-link
  // as an anchor and look within its closest <section>.
  const anchorLink = selfLinks.find(a => /\/in\/[^\/]+$/.test((a.href||"").split("?")[0].replace(/\/$/, "")));
  const block = anchorLink ? (anchorLink.closest("section") || mainEl) : mainEl;

  const lines = Array.from(block.querySelectorAll("p, span"))
    .map(el => (el.innerText || "").trim())
    .filter(Boolean)
    .filter(l => l !== out.name)
    .filter(l => !degreeRx.test(l))
    .filter(l => l.length >= 3 && l.length <= 200);

  // Dedupe while preserving order
  const seen = new Set();
  const uniq = [];
  for (const l of lines) { if (!seen.has(l)) { seen.add(l); uniq.push(l); } }

  // Headline = first non-geo line; location = first geo-looking line.
  for (const l of uniq) {
    if (!out.location && geoRx.test(l)) { out.location = l; continue; }
    if (!out.headline && !geoRx.test(l)) { out.headline = l; }
    if (out.headline && out.location) break;
  }

  return out;
}
"""

# ── Top-card action buttons ──────────────────────────────────────────────────
# The profile owner's Connect/Follow/More buttons are identified by the owner's
# name in the aria-label (e.g. "Invite Peter Lange to connect"). Sidebar
# "People also viewed" also have `Invite X to connect` buttons — so always
# match against the PROFILE OWNER's name to disambiguate.
#
# Use these as aria-label templates, substituting the name at call time:
#   CONNECT_BUTTON_FMT.format(name="Peter Lange")
#   FOLLOW_BUTTON_FMT.format(name="Peter Lange")
CONNECT_BUTTON_FMT = 'button[aria-label="Invite {name} to connect"]'
FOLLOW_BUTTON_FMT  = 'button[aria-label="Follow {name}"]'

# "More" overflow — when Connect isn't in the primary top-card area, it lives
# inside the More menu. Scope to <main> to avoid the global nav "More" button.
# Multiple "More" buttons may exist; the top-card one is the FIRST in <main>.
MORE_MENU_BUTTON = 'main button[aria-label="More"]'

# Inside the More-menu dropdown, the Connect option is a <div role="button">
# (not a real button) with aria-label that still embeds the owner's name.
# Empirically LinkedIn also offers "Connect" inside a menu list item.
MORE_MENU_CONNECT_FMT = (
    "div[role='button'][aria-label*='Invite {name}'][aria-label*='connect'],"
    " div[role='menuitem'][aria-label*='Invite {name}'][aria-label*='connect'],"
    " li[role='menuitem'][aria-label*='Invite {name}'][aria-label*='connect']"
)

# ── Connect modal ────────────────────────────────────────────────────────────
# Spec lock: plain connect only, NO note. So we want the "Send without a note"
# button directly; if the modal only offers "Add a note"/"Send" we STOP.
SEND_WITHOUT_NOTE_BUTTON = 'button[aria-label="Send without a note"]'
SEND_NOW_BUTTON          = 'button[aria-label="Send now"]'  # legacy; check first
ADD_NOTE_BUTTON          = 'button[aria-label="Add a note"]'
NOTE_TEXTAREA            = "textarea[name='message']"
MODAL_CLOSE_BUTTON       = "button[aria-label='Dismiss']"

# ── Rate-limit / verification signals ────────────────────────────────────────
# All substring checks done via page.content() in code, so kept as locator
# strings only where clicking a specific element is useful.
INVITATION_LIMIT_BANNER = "text=You've reached the weekly invitation limit"
CAPTCHA_IFRAME          = "iframe[title*='security' i]"
