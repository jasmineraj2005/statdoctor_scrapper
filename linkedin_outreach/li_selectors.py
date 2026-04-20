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

  // Scope to the top-card section — the <section> containing the <h2> that
  // matches the profile owner's name. Profile pages have many <h2>s (Activity,
  // Experience, etc.); the owner's name is one of them.
  const nameH2 = Array.from(mainEl.querySelectorAll("h2"))
    .find(h => ((h.innerText || "").trim() === out.name));
  const topCard = nameH2
    ? (nameH2.closest("section") || nameH2.parentElement || mainEl)
    : mainEl;

  // Regexes for filters
  const degreeRx = /^[·•\s]*\d+(?:st|nd|rd|th)(?:\+)?(\s*degree)?\s*$/i;
  const geoRx    = /,\s*(Australia|Victoria|New South Wales|Queensland|Western Australia|South Australia|Tasmania|Northern Territory|ACT|NSW|VIC|QLD|WA|SA|TAS|NT)\b/i;
  const pronounRx = /^(he\/him|she\/her|they\/them|[a-z]{2,4}\/[a-z]{2,4})$/i;
  const uiChipRx  = /^(contact info|message|connect|follow|more|see more|show more|send|edit|about|activity|experience|education|skills|interests|languages|recommendations|highlights)$/i;
  // Action-button row like "Message Connect Jason Ha He/Him" — contains the
  // word "connect" AND another action verb (message/follow/more).
  const actionComboRx = /\b(message|follow|more)\b[^.]*\bconnect\b|\bconnect\b[^.]*\b(message|follow|more)\b/i;
  // LinkedIn top nav line: "Home ... My Network ... Jobs ..."
  const navRx = /\b(my network|messaging|notifications|for business)\b/i;

  const isValid = (l) => {
    if (!l) return false;
    if (l === out.name) return false;
    if (degreeRx.test(l)) return false;
    if (geoRx.test(l)) return false;
    if (pronounRx.test(l)) return false;
    if (uiChipRx.test(l)) return false;
    if (actionComboRx.test(l)) return false;
    if (navRx.test(l)) return false;
    if (l.length < 10 || l.length > 220) return false;
    return true;
  };

  const paraTexts = Array.from(topCard.querySelectorAll("p"))
    .map(p => (p.innerText || "").trim().replace(/\s+/g, " "))
    .filter(Boolean);

  // Headline: first valid <p> inside the top-card section.
  for (const txt of paraTexts) {
    if (isValid(txt)) { out.headline = txt; break; }
  }
  // Location: first geo-matching <p> (≤ 100 chars to exclude recruiter ads).
  for (const txt of paraTexts) {
    if (geoRx.test(txt) && txt.length <= 100) { out.location = txt; break; }
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
#
# IMPORTANT: owner's Connect is rendered as an <a> anchor (href points to
# /preload/custom-invite/?vanityName=...). Sidebar "Invite X to connect"
# recommendations are <button>s. Tagging Connect as `a[...]` auto-excludes
# sidebar. Follow remains a <button>.
CONNECT_BUTTON_FMT = 'a[aria-label="Invite {name} to connect"]'
FOLLOW_BUTTON_FMT  = 'button[aria-label="Follow {name}"]'

# "More" overflow — when Connect isn't in the primary top-card area, it lives
# inside the More menu. Scope to <main> to avoid the global nav "More" button.
# `:visible` rejects the 0×0 hidden-ghost More button that LinkedIn also
# renders inside <main> (observed live on graham-mccorkill + dawidnaude
# probes, 2026-04-21). Playwright's :visible pseudo works in page.locator()
# chains; connector.py uses those, not page.query_selector, so the filter
# actually runs.
MORE_MENU_BUTTON = 'main button[aria-label="More"]:visible'

# Inside the More-menu dropdown, Connect is typically an <a> anchor OR a
# role=button element with aria-label embedding the owner's name. Use a union
# that covers both shapes.
MORE_MENU_CONNECT_FMT = (
    "a[aria-label='Invite {name} to connect'],"
    " div[role='button'][aria-label*='Invite {name}'][aria-label*='connect'],"
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
