# ─────────────────────────────────────────────────────────────────────────────
# auth.py  –  LinkedIn login + cookie persistence
# ─────────────────────────────────────────────────────────────────────────────
import json, os, time, random
from playwright.sync_api import Page
import config

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL  = "https://www.linkedin.com/feed/"


def save_cookies(page: Page):
    cookies = page.context.cookies()
    with open(config.COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    print(f"[auth] Cookies saved to {config.COOKIES_FILE}")


def load_cookies(page: Page) -> bool:
    if not os.path.exists(config.COOKIES_FILE):
        return False
    with open(config.COOKIES_FILE, "r") as f:
        cookies = json.load(f)
    page.context.add_cookies(cookies)
    print(f"[auth] Loaded {len(cookies)} cookies from {config.COOKIES_FILE}")
    return True


def is_logged_in(page: Page) -> bool:
    page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded", timeout=30_000)
    time.sleep(random.uniform(2, 4))
    return "feed" in page.url and "login" not in page.url


def _safe_url(p: Page) -> str:
    try:
        return p.url
    except Exception:
        return ""


def _find_logged_in_page(context) -> Page | None:
    """Return any page in the context that's on /feed/ (covers the case
    where LinkedIn redirects login success into a new tab)."""
    for p in context.pages:
        if "feed" in _safe_url(p):
            return p
    return None


def _wait_for_feed(page: Page, seconds: int) -> Page | None:
    """Poll every second across all context pages until one reaches /feed/.
    Returns the feed-page if found, else None. Logs URLs every 15s."""
    last_dump = 0
    for i in range(seconds):
        time.sleep(1)
        try:
            winner = _find_logged_in_page(page.context)
            if winner:
                return winner
            # Diagnostic dump every 15s so we can see what URLs exist
            if i - last_dump >= 15:
                urls = [_safe_url(p) for p in page.context.pages]
                print(f"[auth] Still waiting ({i}s). Tabs: {urls}")
                last_dump = i
        except Exception as e:
            print(f"[auth] Error inspecting pages at {i}s: {type(e).__name__}: {e}")
    return None


def login_with_credentials(page: Page, email: str, password: str) -> Page:
    """
    Interactive login — types credentials and waits for manual 2FA if needed.
    Saves cookies on success. Returns the page that landed on /feed/ (may
    differ from the input page if LinkedIn redirected into a new tab).
    """
    print("[auth] Navigating to LinkedIn login page...")
    page.goto(LINKEDIN_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    time.sleep(random.uniform(1, 2))
    print(f"[auth] Landed on: {page.url}")

    # If LinkedIn redirected us straight to /feed/ (persistent profile is already logged in),
    # don't try to type into a non-existent login form.
    if "feed" in page.url:
        print("[auth] Already authenticated via persistent profile — skipping login form.")
        save_cookies(page)
        return page

    try:
        page.wait_for_selector("input#username", state="visible", timeout=20_000)
    except Exception:
        # Dump what's actually on the page so we can see why the form isn't there
        debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dry_run_debug")
        os.makedirs(debug_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base  = os.path.join(debug_dir, f"{stamp}_login_form_missing")
        try:
            with open(base + ".html", "w", encoding="utf-8") as f:
                f.write(page.content())
            page.screenshot(path=base + ".png", full_page=True)
            print(f"[auth] Saved debug dump: {base}.html + .png")
        except Exception as dump_err:
            print(f"[auth] Debug dump failed: {dump_err}")

        # Also probe alternate selectors
        candidates = ["input[name='session_key']", "input[autocomplete='username']",
                      "input[type='email']", "#email-or-phone",
                      "form[data-id='sign-in-form']", "button[type='submit']"]
        matches = {c: len(page.query_selector_all(c)) for c in candidates}
        print(f"[auth] Alternate selector match counts: {matches}")

        # Page title hint
        try:
            print(f"[auth] Page title: {page.title()!r}")
        except Exception:
            pass

        raise RuntimeError(
            f"[auth] Login form not found after 20s. URL: {page.url}. See debug dump."
        )

    print(f"[auth] Typing email ({email[:3]}***{email[-10:] if len(email) > 13 else ''})...")
    page.fill("input#username", "")
    _type_human(page, "input#username", email)
    time.sleep(random.uniform(0.5, 1.2))

    print("[auth] Typing password...")
    page.fill("input#password", "")
    _type_human(page, "input#password", password)
    time.sleep(random.uniform(0.5, 1.0))

    print("[auth] Submitting login form...")
    page.click("button[type='submit']")
    time.sleep(random.uniform(2, 3))
    print(f"[auth] Submitted. URL now: {page.url}")

    # Short wait — maybe no 2FA (trusted device or already verified)
    winner = _wait_for_feed(page, 20)
    if winner:
        print(f"[auth] Login successful (no 2FA prompt). Active URL: {winner.url}")
        save_cookies(winner)
        return winner

    # Otherwise, 2FA expected — inform user and wait longer
    cur = _safe_url(page)
    print(f"[auth] 2FA / security check likely required. Current URL: {cur}")
    print("[auth] Complete the challenge in the Chromium window the script opened.")
    print("[auth] If you tap 'Yes' in the LinkedIn mobile app, this will advance automatically.")
    print("[auth] Waiting up to 300 seconds (5 min) for any tab to reach /feed/...")

    winner = _wait_for_feed(page, 300)
    if winner:
        print(f"[auth] 2FA complete. Logged in. Active URL: {winner.url}")
        save_cookies(winner)
        return winner

    # Final diagnostic
    urls = [_safe_url(p) for p in page.context.pages]
    raise RuntimeError(
        f"[auth] Timed out waiting for /feed/. Open tabs: {urls}"
    )


def ensure_logged_in(page: Page, email: str = "", password: str = "") -> Page:
    """
    Try cookies first. If expired, fall back to credential login.
    Returns the (possibly new) page that is logged in.
    """
    if load_cookies(page) and is_logged_in(page):
        print("[auth] Session restored from cookies.")
        return page

    if not email or not password:
        raise RuntimeError(
            "[auth] No valid cookies and no credentials provided. "
            "Add LINKEDIN_EMAIL and LINKEDIN_PASSWORD to .env."
        )
    return login_with_credentials(page, email, password)


def _type_human(page: Page, selector: str, text: str):
    lo, hi = config.DELAY_AFTER_TYPING_SEC
    for char in text:
        page.type(selector, char)
        time.sleep(random.uniform(lo, hi))
