"""Profile-visit tracker with 48h cool-down, persisted to JSON.

Purpose
-------
LinkedIn tolerates normal browsing but penalises pattern repetition. Every
iteration that revisits the same profile for selector debugging bakes that
profile into a scraper-looking cadence. This module keeps a per-url last-
visit timestamp so we can:

  1. Skip profiles visited in the last 48h during sample-based audits.
  2. Force selector-iteration work onto cached HTML (dry_run_debug/) when
     a URL is still hot.
  3. Record every live visit automatically at the point of navigation.

Storage: a plain JSON file at linkedin_outreach/visited_profiles.json,
  { "https://www.linkedin.com/in/<handle>": "<iso-timestamp>", ... }

Not a security boundary — this is ops hygiene, not auth.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_STORE_PATH = Path(__file__).resolve().parent / "visited_profiles.json"
_COOLDOWN_HOURS = 48


def _canonical(url: str) -> str:
    """Strip query/hash/overlay and trailing slash so different URL forms
    for the same profile share a single cool-down record."""
    if not url:
        return ""
    u = url.split("?")[0].split("#")[0]
    u = re.sub(r"/overlay/.*$", "", u)
    u = re.sub(r"/recent-activity.*$", "", u)
    return u.rstrip("/")


def _load() -> dict[str, str]:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text() or "{}")
    except Exception:
        return {}


def _save(data: dict[str, str]) -> None:
    _STORE_PATH.write_text(json.dumps(data, sort_keys=True, indent=2))


def mark_visited(url: str, when: datetime | None = None) -> None:
    """Record a live profile visit. Idempotent — overwrites any prior stamp."""
    key = _canonical(url)
    if not key:
        return
    data = _load()
    ts = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    data[key] = ts
    _save(data)


def is_hot(url: str, hours: int = _COOLDOWN_HOURS) -> bool:
    """True iff `url` was last visited within the cool-down window."""
    key = _canonical(url)
    if not key:
        return False
    data = _load()
    stamp = data.get(key)
    if not stamp:
        return False
    try:
        last = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(hours=hours)


def hot_set(hours: int = _COOLDOWN_HOURS) -> set[str]:
    data = _load()
    now = datetime.now(timezone.utc)
    out = set()
    for key, stamp in data.items():
        try:
            last = datetime.fromisoformat(stamp)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(hours=hours):
                out.add(key)
        except ValueError:
            continue
    return out
