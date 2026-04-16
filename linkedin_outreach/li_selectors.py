# ─────────────────────────────────────────────────────────────────────────────
# selectors.py  –  LinkedIn CSS/XPath selectors, isolated so UI changes are
#                  easy to fix without touching business logic.
# ─────────────────────────────────────────────────────────────────────────────

# ── Search results page ───────────────────────────────────────────────────────
SEARCH_RESULT_CARDS   = "li.reusable-search__result-container"
RESULT_NAME           = "span.entity-result__title-text a span[aria-hidden='true']"
RESULT_LOCATION       = "div.entity-result__secondary-subtitle"
RESULT_HEADLINE       = "div.entity-result__primary-subtitle"
RESULT_PROFILE_LINK   = "span.entity-result__title-text a"

# ── Profile page ──────────────────────────────────────────────────────────────
PROFILE_NAME          = "h1.text-heading-xlarge"
PROFILE_HEADLINE      = "div.text-body-medium.break-words"
PROFILE_LOCATION      = "span.text-body-small.inline.t-black--light.break-words"

# Connect button — LinkedIn renders different variants depending on context
CONNECT_BUTTON_PRIMARY   = "button[aria-label*='Connect']"
CONNECT_BUTTON_MORE_MENU = "button[aria-label='More actions']"
CONNECT_IN_MORE_DROPDOWN = "div[aria-label*='connect' i]"

# ── Connection modal ──────────────────────────────────────────────────────────
ADD_NOTE_BUTTON       = "button[aria-label='Add a note']"
NOTE_TEXTAREA         = "textarea[name='message']"
SEND_BUTTON           = "button[aria-label='Send now']"
MODAL_CLOSE_BUTTON    = "button[aria-label='Dismiss']"

# ── Rate-limit / warning signals ──────────────────────────────────────────────
INVITATION_LIMIT_BANNER = "text=You've reached the weekly invitation limit"
CAPTCHA_IFRAME          = "iframe[title*='security' i]"
