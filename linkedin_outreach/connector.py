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
    # name (first + last), which may differ from the AHPRA legal name AND
    # from the aria-label (which uses the plain name, no title prefix).
    # Day-2 failure mode: document.title often has "Dr Andrew White | LinkedIn"
    # but the aria-label is "Invite Andrew White to connect" — so we also
    # try a stripped-prefix variant.
    owner_name = _resolve_owner_name(page) or practitioner_name
    if not owner_name:
        return STATUS_ERROR, "could not read profile owner name"
    owner_name_stripped = _strip_title_prefix(owner_name)

    # ── Try top-card Connect anchor ──────────────────────────────────────────
    # Sequence: exact match on resolved name → exact on stripped-prefix →
    # permissive fallback "Invite …to connect" scoped to the top-card section.
    clicked = _try_topcard_connect(page, owner_name, owner_name_stripped)
    if clicked is None:
        # Top card didn't have Connect — try the More-menu fallback.
        status, detail = _try_more_menu_connect(page, owner_name_stripped or owner_name)
        if status != STATUS_SENT:
            return status, detail
        _post_connect_pause()
        return status, detail
    if clicked is False:
        return STATUS_ERROR, "connect click failed"

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


_TITLE_PREFIXES = ("dr ", "dr. ", "prof ", "prof. ", "a/prof ", "a/prof. ")


def _strip_title_prefix(name: str) -> str:
    """Strip common medical/academic title prefixes. LinkedIn's
    aria-label uses the plain name, so "Dr Andrew White" in document.title
    needs to become "Andrew White" for the selector to match."""
    low = name.lower()
    for p in _TITLE_PREFIXES:
        if low.startswith(p):
            return name[len(p):].strip()
    return name


def _try_topcard_connect(page: Page, owner_name: str,
                          owner_name_stripped: str) -> bool | None:
    """Try to click the top-card Connect anchor. Returns:
      True  — clicked successfully
      False — located but click raised (caller treats as error)
      None  — not present; caller should fall through to More-menu

    Tries three selectors in order:
      1. exact aria-label match on resolved name
      2. exact aria-label match on stripped-prefix name (Day-2 fix)
      3. permissive substring "Invite …to connect" scoped to top-card section
         (guards against sidebar "People you may know" Connect chips)
    """
    candidates: list[str] = [selectors.CONNECT_BUTTON_FMT.format(name=owner_name)]
    if owner_name_stripped and owner_name_stripped != owner_name:
        candidates.append(selectors.CONNECT_BUTTON_FMT.format(name=owner_name_stripped))
    # Permissive fallback: first section inside <main> is the top card.
    # :visible guards against the hidden viewport-variant anchors.
    candidates.append(
        "main section:first-of-type "
        "a[aria-label*='Invite '][aria-label*=' to connect']:visible"
    )

    for sel in candidates:
        try:
            matches = page.locator(sel)
            n = matches.count()
        except Exception:
            continue
        if n == 0:
            continue
        # LinkedIn often renders 2+ identical Connect anchors (top-card +
        # sticky header bar). Iterate through them — if the first is
        # pointer-intercepted or off-screen, the second usually isn't.
        for i in range(n):
            try:
                loc = matches.nth(i)
                loc.wait_for(state="visible", timeout=2_000)
                loc.scroll_into_view_if_needed(timeout=2_000)
                loc.click(timeout=3_000)
                return True
            except PwTimeoutError:
                continue
            except Exception:
                continue
    return None


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

    # Try the aria-label union first (covers the Dawid-Naude shape). If that
    # misses, fall back to matching menu items by text — Day-2 probe on Dr
    # Abhilash found a Connect item rendered as <a role="menuitem"> with
    # EMPTY aria-label and visible text "Connect", which the aria-only union
    # couldn't catch.
    # Resolve the Connect item. Two strategies in order:
    #   1. aria-label union (covers the Dawid-Naude shape with a proper
    #      "Invite <Name> to connect" aria-label)
    #   2. text-based fallback scoped to the visible dropdown menu — catches
    #      the Day-2 Abhilash shape where <a role="menuitem"> has an EMPTY
    #      aria-label and visible text "Connect".
    clicked, err = _resolve_and_click_more_connect(page, owner_name)
    if err:
        return STATUS_ERROR, err
    if not clicked:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        rel = _get_relationship_label(page)
        if rel in ("message", "following", "pending"):
            return STATUS_ALREADY_CONNECTED, f"relationship={rel}"
        return STATUS_CONNECT_UNAVAIL, "more menu opened but no Connect match"

    return _click_send_without_note(page)


def _resolve_and_click_more_connect(page: Page, owner_name: str) -> tuple[bool, str]:
    """Click the Connect item inside an already-open More dropdown.

    Returns (clicked, error_message). clicked=True on success; clicked=False
    with empty error means "no Connect item found" (caller emits
    connect_unavailable); non-empty error means an unexpected exception.
    """
    dropdown_sel = selectors.MORE_MENU_CONNECT_FMT.format(name=owner_name)
    try:
        item = page.locator(dropdown_sel).first
        item.wait_for(state="visible", timeout=FIND_CONNECT_TIMEOUT_MS)
        item.click()
        return True, ""
    except PwTimeoutError:
        pass
    except Exception as e:
        return False, f"more→connect click failed: {type(e).__name__}: {str(e)[:80]}"

    # Text-based fallback. Playwright's has_text is substring + case-insensitive;
    # narrow to menuitem-role items so we don't catch e.g. a "Connections" link.
    try:
        item = (page.locator("div[role='menu'], ul[role='menu'], "
                             "div.artdeco-dropdown__content")
                .locator("a[role='menuitem'], div[role='menuitem'], "
                         "li[role='menuitem'], button[role='menuitem']")
                .filter(has_text="Connect")
                .first)
        item.wait_for(state="visible", timeout=FIND_CONNECT_TIMEOUT_MS)
        item.click()
        return True, ""
    except PwTimeoutError:
        return False, ""
    except Exception as e:
        return False, f"more→connect text click failed: {type(e).__name__}: {str(e)[:80]}"


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
