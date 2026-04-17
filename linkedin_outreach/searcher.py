# ─────────────────────────────────────────────────────────────────────────────
# searcher.py  –  LinkedIn people search + profile data extraction
# ─────────────────────────────────────────────────────────────────────────────
import os, re, json, time, random, urllib.parse
from playwright.sync_api import Page
import config, verifier
import li_selectors as selectors


def _clean_for_search(full_name: str) -> str:
    """Strip titles and middle names so the search query is just 'First Last'."""
    s = re.sub(r"\b(Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|A/Prof\.?|Assoc\.?|Associate)\b",
               "", full_name, flags=re.IGNORECASE)
    parts = [p for p in s.split() if p]
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1]}"
    return " ".join(parts)


PEOPLE_SEARCH_BASE = "https://www.linkedin.com/search/results/people/?keywords={query}&origin=GLOBAL_SEARCH_HEADER"

EXTRACT_RESULTS_JS = r"""
() => {
  const processedCards = new WeakSet();
  const seenUrls = new Set();
  const results = [];

  // Patterns used to clean / classify card text
  const degreeRx   = /\s*[•·]\s*\d+(?:st|nd|rd|th)(?:\+)?\s*$/i;   // " • 3rd+"
  const stateRx    = /,\s*(Victoria|New South Wales|Queensland|Western Australia|South Australia|Tasmania|Northern Territory|Australian Capital Territory|NSW|VIC|QLD|WA|SA|TAS|NT|ACT)\b[^,]*,?\s*Australia?$/i;
  const locStateRx = /(Victoria|New South Wales|Queensland|Western Australia|South Australia|Tasmania|Northern Territory|Australian Capital Territory|Australia)\b/i;
  const actionRx   = /^(Connect|Message|Follow|Pending|Following)$/i;
  const mutualRx   = /mutual connection/i;
  const emojiRx    = /^[\p{Extended_Pictographic}\u2600-\u27BF\s]+/u;

  function cleanName(raw) {
    if (!raw) return "";
    // first line only, strip degree tag, leading emojis, hyphens, quotes
    let s = raw.split("\n")[0].trim();
    s = s.replace(degreeRx, "").trim();
    s = s.replace(emojiRx, "").trim();
    s = s.replace(/^[\-–—•·]+\s*/, "").trim();
    return s;
  }

  const anchors = document.querySelectorAll("a[href*='/in/']");
  for (const a of anchors) {
    // Normalise href: strip query, hash, /overlay/<anything>, and trailing slash.
    // Without the /overlay/ strip, a single profile card produces multiple
    // "cards" (main link vs background-photo/contact-info overlays).
    let href = (a.href || "").split("?")[0].split("#")[0]
                 .replace(/\/overlay\/.*$/, "").replace(/\/$/, "");
    if (!href.includes("/in/")) continue;
    if (seenUrls.has(href)) continue;

    // Walk up to find the "card" — the nearest ancestor that has
    // multiple lines of content (this is the profile result block).
    let card = a;
    for (let depth = 0; depth < 12 && card.parentElement; depth++) {
      card = card.parentElement;
      const t = (card.innerText || "").trim();
      if (t.split("\n").filter(Boolean).length >= 3 && t.length > 40) break;
    }
    // Only process each card once — skip when a mutual-connection anchor
    // resolves to the same card we already extracted from.
    if (processedCards.has(card)) continue;
    processedCards.add(card);

    const rawText = (card.innerText || "").trim();
    const lines = rawText.split("\n").map(s => s.trim()).filter(Boolean);
    if (lines.length < 2) continue;

    // Find name: first line of the card (that's where the profile name lives,
    // degree badge suffix is stripped). Fall back to anchor text.
    let name = cleanName(lines[0] || a.innerText || "");

    // Find location: a line that looks like an Aussie geo string.
    let location = "";
    for (const ln of lines) {
      if (stateRx.test(ln) || locStateRx.test(ln)) {
        location = ln;
        break;
      }
    }
    // Find headline: longest descriptive line that isn't name/location/button/mutual
    let headline = "";
    for (const ln of lines) {
      if (ln === name || ln === location) continue;
      if (actionRx.test(ln)) continue;
      if (mutualRx.test(ln)) continue;
      if (ln.length > headline.length) headline = ln;
    }

    // Active-account signals
    const hasDegreeBadge = /[•·]\s*\d+(?:st|nd|rd|th)(?:\+)?/i.test(lines[0] || "");
    const hasActionButton = lines.some(l => actionRx.test(l));
    const hasHeadline = headline.length > 0;

    seenUrls.add(href);
    results.push({
      url: href,
      name,
      headline,
      location,
      has_degree_badge: hasDegreeBadge,
      has_action_button: hasActionButton,
      has_headline: hasHeadline,
      raw_preview: lines.slice(0, 5).join(" | ").slice(0, 300),
    });
  }
  return results;
}
"""

DEBUG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dry_run_debug")


def _dump_debug(page: Page, practitioner_id: str, reason: str):
    """Save HTML + screenshot when something goes wrong, for later selector triage."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base  = os.path.join(DEBUG_DIR, f"{stamp}_{practitioner_id}_{reason}")
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(page.content())
        page.screenshot(path=base + ".png", full_page=True)
        print(f"  [debug] Saved {base}.html + .png")
    except Exception as e:
        print(f"  [debug] Could not save dump: {e}")


def _probe_candidate_selectors(page: Page):
    """Count matches for common search-result container selectors — helps us find the right one."""
    candidates = [
        "li.reusable-search__result-container",       # original (probably old)
        "li.search-result__occluded-item",
        "div.search-results-container ul li",
        "ul[role='list'] > li",
        "div[data-view-name='search-entity-result-universal-template']",
        "div.entity-result",
        "div.entity-result__item",
        "li[class*='search-result']",
        "li[class*='entity-result']",
        "[data-chameleon-result-urn]",
    ]
    print("  [debug] Probing candidate selectors:")
    for sel in candidates:
        try:
            n = len(page.query_selector_all(sel))
            if n > 0:
                print(f"    {n:>3}  ← {sel}")
        except Exception:
            pass


def search_and_find_profile(page: Page, practitioner: dict) -> dict | None:
    """
    Search LinkedIn for the practitioner and return the best matching profile,
    or None if no match found within MAX_PROFILES_TO_CHECK results.

    Returns dict with keys: name, location, headline, url
    """
    clean_name = _clean_for_search(practitioner["name"])
    query = config.SEARCH_QUERY_TEMPLATE.format(
        name=practitioner["name"],
        clean_name=clean_name,
        suburb=practitioner["suburb"],
        state=practitioner.get("state", ""),
    )
    encoded = urllib.parse.quote(query)
    url = PEOPLE_SEARCH_BASE.format(query=encoded)

    # Apply geoUrn filter if configured — narrows search server-side
    if config.SEARCH_GEO_URNS:
        geo_param = urllib.parse.quote(json.dumps(config.SEARCH_GEO_URNS), safe="")
        url += f"&geoUrn={geo_param}"

    print(f"  [search] Querying: {query}")
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    # Result cards are rendered async; give the app time to paint them
    time.sleep(random.uniform(3, 5))
    print(f"  [search] Landed on: {page.url}")

    if _is_rate_limited(page):
        raise RateLimitError("LinkedIn rate limit or CAPTCHA detected during search.")

    # Stable extraction via JS — no dependence on hashed class names
    try:
        cards = page.evaluate(EXTRACT_RESULTS_JS)
    except Exception as e:
        print(f"  [search] JS extraction failed: {e}")
        cards = []

    print(f"  [search] Profile cards extracted: {len(cards)}")
    if not cards:
        print("  [search] No cards found — dumping page for inspection.")
        _dump_debug(page, practitioner.get("practitioner_id", "unknown"), "no_cards")
        return None

    for i, profile in enumerate(cards[:config.MAX_PROFILES_TO_CHECK]):
        is_match, reason = verifier.verify_profile(practitioner, profile)
        print(f"  [search] Result {i+1}: '{profile['name']}' | '{profile['location']}' | '{profile['headline'][:80]}' — {reason}")
        if is_match:
            return profile
        time.sleep(random.uniform(*config.DELAY_BETWEEN_PROFILES_SEC))

    return None


def get_profile_page_data(page: Page, profile_url: str) -> dict:
    """
    Navigate to a full profile page and return richer data.
    Used to verify connect button availability before clicking.
    """
    page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
    time.sleep(random.uniform(2, 4))

    name     = _safe_text(page, selectors.PROFILE_NAME)
    headline = _safe_text(page, selectors.PROFILE_HEADLINE)
    location = _safe_text(page, selectors.PROFILE_LOCATION)

    return {"name": name, "headline": headline, "location": location, "url": profile_url}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _extract_card_data(card) -> dict | None:
    try:
        name_el     = card.query_selector(selectors.RESULT_NAME)
        location_el = card.query_selector(selectors.RESULT_LOCATION)
        headline_el = card.query_selector(selectors.RESULT_HEADLINE)
        link_el     = card.query_selector(selectors.RESULT_PROFILE_LINK)

        name     = name_el.inner_text().strip()     if name_el     else ""
        location = location_el.inner_text().strip() if location_el else ""
        headline = headline_el.inner_text().strip() if headline_el else ""
        url      = link_el.get_attribute("href")    if link_el     else ""

        if not name:
            return None

        # Strip query params from profile URL
        url = url.split("?")[0] if url else ""
        return {"name": name, "location": location, "headline": headline, "url": url}
    except Exception:
        return None


def _safe_text(page: Page, selector: str) -> str:
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else ""
    except Exception:
        return ""


def _is_rate_limited(page: Page) -> bool:
    try:
        if page.query_selector(selectors.CAPTCHA_IFRAME):
            return True
        content = page.content()
        if "security verification" in content.lower():
            return True
        if "you've reached the weekly invitation limit" in content.lower():
            return True
    except Exception:
        pass
    return False


class RateLimitError(Exception):
    pass
