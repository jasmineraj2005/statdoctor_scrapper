"""connector.py — send a plain (no-note) LinkedIn connection request.

Called only for profiles the classifier tagged `influencer`. The caller
(main.py) must:
  - gate on classification == "influencer" BEFORE calling us (we also
    re-check as a safety net),
  - respect `_visit_tracker.is_hot` BEFORE calling us (the profiler has
    already marked the URL visited; skipping within-session re-connects
    is not our job),
  - honour the daily-cap / weekly-cap accounting.

Spec lock (see ROADMAP.md §"Hard decisions"): connect with NO note. If
the modal only offers an "Add a note" + "Send" split (rare legacy UI)
we STOP — do not fall through to typing a note.

Selector strategy (all semantic, see `li_selectors.py`):
  1. Top-card owner-Connect anchor `a[aria-label="Invite <Name> to connect"]`.
     Probed live 2026-04-20 on Caroline Macindoe: 3 matches (top-card +
     sticky bar + hidden viewport variant). We use `page.locator().first`
     with auto-wait for visibility so we always click the visible one.
  2. More-menu overflow path — click MORE_MENU_BUTTON then resolve the
     Connect item via `MORE_MENU_CONNECT_FMT` (4-way union covering the
     DOM shapes LinkedIn has shipped across 2024-2026).
     NOTE: the More-menu fallback is NOT yet validated live — no
     Follow-primary profile was in the non-HOT set on probe day. Expect
     Day 1 of the staged real run to be the first live exercise; monitor
     the first few Follow-primary attempts closely.
  3. On the resulting modal, click SEND_WITHOUT_NOTE_BUTTON (or legacy
     SEND_NOW_BUTTON). If neither present, dismiss and return
     "send_needs_note" — we do NOT add a note (spec-lock).
"""
from __future__ import annotations

import random
import time

from playwright.sync_api import Page, TimeoutError as PwTimeoutError

import config
import li_selectors as selectors
from searcher import RateLimitError

from sheets_logger import (
    STATUS_SENT,
    STATUS_ALREADY_CONNECTED,
    STATUS_CONNECT_UNAVAIL,
    STATUS_ERROR,
    STATUS_SKIPPED,
)

# Extra statuses the connector can emit that aren't in the base set.
STATUS_DRY_RUN         = "dry_run"
STATUS_SEND_NEEDS_NOTE = "send_needs_note"

# Locator timeouts — keep these short. We don't want to hold a session
# open waiting for a Connect button that's genuinely absent.
FIND_CONNECT_TIMEOUT_MS  = 6_000
FIND_SEND_BTN_TIMEOUT_MS = 6_000
DROPDOWN_SETTLE_SEC      = (1.0, 2.0)


def send_connection_request(page: Page,
                             profile_url: str,
                             practitioner_name: str,
                             classification: str = "",
                             event_logger=None,
                             practitioner: dict | None = None) -> tuple[str, str]:
    """Send a plain connect (no note) for an influencer profile.

    Args:
        page:                authed Playwright page.
        profile_url:         canonical /in/<handle> URL.
        practitioner_name:   AHPRA legal name (fallback for owner resolution
                             if PROFILE_DATA_JS can't read the on-page name).
        classification:      classifier verdict. Anything other than
                             "influencer" short-circuits to `skipped`.
        event_logger:        optional SheetsLogger; if provided, emits a
                             connect_sent / connect_failed event on every
                             return path.

    Returns (status, detail). Statuses map to sheets_logger constants
    plus the two extras above.
    """
    status, detail = _send_connection_request_core(
        page, profile_url, practitioner_name, classification,
    )
    _emit(event_logger, practitioner, profile_url, status, detail)
    return status, detail


def _send_connection_request_core(page: Page,
                                   profile_url: str,
                                   practitioner_name: str,
                                   classification: str) -> tuple[str, str]:
    """All the actual connect logic. Wrapped by send_connection_request so
    every return path emits a single live event."""
    # ── Gate 1 — classification ──────────────────────────────────────────────
    if classification != "influencer":
        return STATUS_SKIPPED, f"classification={classification or 'unknown'}"

    # ── Gate 2 — dry-run early-return (before any nav/click) ─────────────────
    # Per ROADMAP: "Dry-run mode must early-return before any click." We also
    # skip the profile nav so step-8's 50-row dry-run doesn't burn budget.
    if getattr(config, "DRY_RUN", False):
        return STATUS_DRY_RUN, f"dry_run: would connect to {profile_url}"

    # ── Navigate ─────────────────────────────────────────────────────────────
    # Note: _visit_tracker cool-down is checked by the orchestrator (main.py)
    # BEFORE profiling — by the time we get here, the profiler has already
    # stamped this URL, so re-checking is_hot() would always be True.
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        return STATUS_ERROR, f"nav failed: {type(e).__name__}: {str(e)[:80]}"
    time.sleep(random.uniform(2.5, 4.5))

    if _weekly_limit_hit(page):
        raise RateLimitError("Weekly invitation limit reached.")

    # Resolve the owner's name from the live DOM. LinkedIn shows the common
    # name (first + last), which may differ from the AHPRA legal name; the
    # Connect aria-label always matches what's in <title>.
    owner_name = _resolve_owner_name(page) or practitioner_name
    if not owner_name:
        return STATUS_ERROR, "could not read profile owner name"

    # ── Try top-card Connect anchor ──────────────────────────────────────────
    connect_sel = selectors.CONNECT_BUTTON_FMT.format(name=owner_name)
    try:
        connect = page.locator(connect_sel).first
        connect.wait_for(state="visible", timeout=FIND_CONNECT_TIMEOUT_MS)
        connect.click()
    except PwTimeoutError:
        # Top card didn't have Connect — try the More-menu fallback.
        status, detail = _try_more_menu_connect(page, owner_name)
        if status != STATUS_SENT:
            return status, detail
        # More-menu path already completed the modal; we're done.
        _post_connect_pause()
        return status, detail
    except Exception as e:
        return STATUS_ERROR, f"connect click failed: {type(e).__name__}: {str(e)[:80]}"

    # ── Modal → Send without a note ──────────────────────────────────────────
    status, detail = _click_send_without_note(page)
    if status == STATUS_SENT:
        _post_connect_pause()
    return status, detail


# ── Internal helpers ─────────────────────────────────────────────────────────

def _resolve_owner_name(page: Page) -> str:
    try:
        data = page.evaluate(selectors.PROFILE_DATA_JS) or {}
        return (data.get("name") or "").strip()
    except Exception:
        return ""


def _try_more_menu_connect(page: Page, owner_name: str) -> tuple[str, str]:
    """Click More → try the union of known Connect-in-dropdown shapes.

    Returns after either (a) successfully clicking Send without a note, or
    (b) giving up with connect_unavailable / already_connected / error.
    """
    more = page.locator(selectors.MORE_MENU_BUTTON).first
    try:
        more.wait_for(state="visible", timeout=FIND_CONNECT_TIMEOUT_MS)
    except PwTimeoutError:
        # No More menu either. Inspect top-card to see what relationship we
        # already have with this profile (Message / Following / Pending).
        rel = _get_relationship_label(page)
        if rel in ("message", "following", "pending"):
            return STATUS_ALREADY_CONNECTED, f"relationship={rel}"
        return STATUS_CONNECT_UNAVAIL, "no connect button, no more menu"

    try:
        more.click()
    except Exception as e:
        return STATUS_ERROR, f"more click failed: {type(e).__name__}: {str(e)[:80]}"

    time.sleep(random.uniform(*DROPDOWN_SETTLE_SEC))

    dropdown_sel = selectors.MORE_MENU_CONNECT_FMT.format(name=owner_name)
    try:
        item = page.locator(dropdown_sel).first
        item.wait_for(state="visible", timeout=FIND_CONNECT_TIMEOUT_MS)
        item.click()
    except PwTimeoutError:
        # Dropdown opened but none of the 4 known shapes matched. Close the
        # menu politely by pressing Escape so we leave the page clean.
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        rel = _get_relationship_label(page)
        if rel in ("message", "following", "pending"):
            return STATUS_ALREADY_CONNECTED, f"relationship={rel}"
        return STATUS_CONNECT_UNAVAIL, "more menu opened but no Connect match"
    except Exception as e:
        return STATUS_ERROR, f"more→connect click failed: {type(e).__name__}: {str(e)[:80]}"

    return _click_send_without_note(page)


def _click_send_without_note(page: Page) -> tuple[str, str]:
    """On the connect modal, click 'Send without a note' (or legacy 'Send now').

    Spec-lock: we never add a note. If the modal only offers Add-a-note, STOP.
    """
    # Try the primary path first: "Send without a note" (2026 variant) and
    # the legacy "Send now" as a near-synonym. If neither, we bail out.
    for sel in (selectors.SEND_WITHOUT_NOTE_BUTTON, selectors.SEND_NOW_BUTTON):
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=FIND_SEND_BTN_TIMEOUT_MS)
            btn.click()
            return STATUS_SENT, "plain connect sent (no note)"
        except PwTimeoutError:
            continue
        except Exception as e:
            return STATUS_ERROR, f"send click failed: {type(e).__name__}: {str(e)[:80]}"

    # Neither path found. Check if the modal is the legacy Add-note variant,
    # then dismiss — we do NOT add a note (spec-lock).
    add_note = page.locator(selectors.ADD_NOTE_BUTTON)
    if add_note.count() > 0:
        _dismiss_modal(page)
        return STATUS_SEND_NEEDS_NOTE, "modal only offered Add-a-note; spec-lock → stop"

    _dismiss_modal(page)
    return STATUS_ERROR, "connect modal opened but no send button found"


def _dismiss_modal(page: Page) -> None:
    try:
        closer = page.locator(selectors.MODAL_CLOSE_BUTTON).first
        closer.click(timeout=2_000)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass


def _get_relationship_label(page: Page) -> str:
    """Read the profile top-card's primary action to detect an existing
    relationship. Scoped to <main> to exclude the left-nav "Messaging" link,
    which matched page.content() on every profile under the old check and
    caused false-positive `already_connected` verdicts.

    Checks by aria-label:
      - "Pending, click to withdraw invitation..."  → pending
      - "Message <Name>"                            → 1st-degree (message button present)
      - "Follow <Name>" / "Following <Name>"        → following
    Returns "unknown" when none matched.
    """
    try:
        if page.locator('main button[aria-label^="Pending"]').first.is_visible(timeout=800):
            return "pending"
    except Exception:
        pass
    try:
        if page.locator('main button[aria-label^="Message "]').first.is_visible(timeout=800):
            return "message"
    except Exception:
        pass
    try:
        if page.locator('main button[aria-label^="Following "]').first.is_visible(timeout=800):
            return "following"
    except Exception:
        pass
    return "unknown"


def _weekly_limit_hit(page: Page) -> bool:
    try:
        content = page.content().lower()
    except Exception:
        return False
    return (
        "weekly invitation limit" in content
        or "you've reached the weekly invitation limit" in content
    )


def _post_connect_pause() -> None:
    time.sleep(random.uniform(*config.DELAY_BETWEEN_CONNECTIONS_SEC))


def _emit(logger, practitioner, profile_url: str, status: str, detail: str) -> None:
    """Map connector status → (event, outcome) and fire a live event.
    Never raises."""
    if not logger:
        return

    if status == STATUS_SENT:
        event, outcome = "connect_sent", "success"
    elif status == STATUS_DRY_RUN:
        event, outcome = "connect_sent", "pending"    # dry-run intent logged
    elif status == STATUS_SKIPPED:
        event, outcome = "skipped_non_influencer", "skipped"
    elif status == STATUS_ALREADY_CONNECTED:
        event, outcome = "already_connected", "skipped"
    else:
        event, outcome = "connect_failed", "fail"

    try:
        logger.log_live_event(
            practitioner=practitioner,
            linkedin_url=profile_url,
            event=event,
            outcome=outcome,
            detail=f"{status} — {detail}" if detail else status,
        )
    except Exception as e:
        print(f"  [connector] WARNING: event log failed: {e}")
