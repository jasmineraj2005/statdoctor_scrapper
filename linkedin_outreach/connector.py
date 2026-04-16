# ─────────────────────────────────────────────────────────────────────────────
# connector.py  –  Sends LinkedIn connection requests
# ─────────────────────────────────────────────────────────────────────────────
import time, random, re
from playwright.sync_api import Page
import config
import li_selectors as selectors
from searcher import RateLimitError


def get_first_name(full_name: str) -> str:
    """Extract first name, stripping titles like Dr, Prof, etc."""
    name = re.sub(r"\b(Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|A/Prof\.?)\b", "", full_name, flags=re.IGNORECASE)
    parts = name.split()
    return parts[0].capitalize() if parts else "there"


def send_connection_request(page: Page, profile_url: str, practitioner_name: str) -> tuple[str, str]:
    """
    Navigate to profile and send a connection request with a personalised note.

    Returns (status, detail):
        status  — one of: "sent", "already_connected", "connect_unavailable", "rate_limited", "error"
        detail  — short description for the notes column
    """
    if config.DRY_RUN:
        print(f"  [connect] DRY RUN — would send to {profile_url}")
        return "sent", "dry_run"

    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(random.uniform(2, 3))

        # ── Check for weekly limit banner ──────────────────────────────────
        if _weekly_limit_hit(page):
            raise RateLimitError("Weekly invitation limit reached.")

        # ── Try primary Connect button ─────────────────────────────────────
        connect_btn = page.query_selector(selectors.CONNECT_BUTTON_PRIMARY)

        # ── If not found, try the "More" overflow menu ─────────────────────
        if not connect_btn:
            more_btn = page.query_selector(selectors.CONNECT_BUTTON_MORE_MENU)
            if more_btn:
                more_btn.click()
                time.sleep(random.uniform(1, 2))
                connect_btn = page.query_selector(selectors.CONNECT_IN_MORE_DROPDOWN)

        if not connect_btn:
            label = _get_relationship_label(page)
            if label in ("message", "following", "unfollow"):
                return "already_connected", f"relationship={label}"
            return "connect_unavailable", "no connect button found"

        connect_btn.click()
        time.sleep(random.uniform(1.5, 2.5))

        # ── Add personalised note ──────────────────────────────────────────
        add_note_btn = page.query_selector(selectors.ADD_NOTE_BUTTON)
        if add_note_btn:
            add_note_btn.click()
            time.sleep(random.uniform(0.8, 1.5))

            note = config.CONNECTION_NOTE.format(first_name=get_first_name(practitioner_name))
            textarea = page.query_selector(selectors.NOTE_TEXTAREA)
            if textarea:
                textarea.click()
                _type_human(page, note)

        # ── Send ───────────────────────────────────────────────────────────
        send_btn = page.query_selector(selectors.SEND_BUTTON)
        if not send_btn:
            # Dismiss modal and report
            dismiss = page.query_selector(selectors.MODAL_CLOSE_BUTTON)
            if dismiss:
                dismiss.click()
            return "error", "send button not found after opening modal"

        send_btn.click()
        time.sleep(random.uniform(1, 2))

        print(f"  [connect] Request sent to {profile_url}")
        return "sent", "connection request sent with note"

    except RateLimitError:
        raise
    except Exception as e:
        return "error", str(e)[:120]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _type_human(page: Page, text: str):
    lo, hi = config.DELAY_AFTER_TYPING_SEC
    # Use keyboard type for the focused textarea
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(lo, hi))


def _get_relationship_label(page: Page) -> str:
    """Try to read what button is showing instead of Connect (Message, Follow, etc.)."""
    try:
        content = page.content().lower()
        for label in ("message", "following", "unfollow", "pending"):
            if label in content:
                return label
    except Exception:
        pass
    return "unknown"


def _weekly_limit_hit(page: Page) -> bool:
    try:
        content = page.content().lower()
        return "weekly invitation limit" in content or "invitation limit" in content
    except Exception:
        return False
