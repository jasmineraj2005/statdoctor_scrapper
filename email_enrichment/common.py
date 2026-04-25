"""Shared utilities: env loader, CSV append, jitter, name parsing, email format tools."""
import csv
import os
import random
import re
import time
from pathlib import Path
from typing import Iterable


# ── .env loader (adapted from linkedin_outreach/main.py:35-49) ──────────────

def load_env():
    """Load .env from email_enrichment/, then repo root. No overwrites."""
    here = Path(__file__).resolve().parent
    for env_path in (here / ".env", here.parent / ".env"):
        if not env_path.exists():
            continue
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        return str(env_path)
    return None


# ── Jitter (pattern from scraper/scraper_state.py:26-27) ─────────────────────

def delay(lo: float = 0.8, hi: float = 1.5):
    time.sleep(random.uniform(lo, hi))


# ── CSV append (pattern from scraper/scraper_state.py:171-179) ──────────────

def append_csv(rows: Iterable[dict], path: Path, fieldnames: list[str] | None = None):
    """Append dict rows to CSV. Writes header only if the file doesn't exist."""
    rows = list(rows)
    if not rows:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header_needed = not path.exists() or path.stat().st_size == 0
    fn = fieldnames or list(rows[0].keys())
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fn, extrasaction="ignore")
        if header_needed:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def read_csv(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Practitioner name parsing ────────────────────────────────────────────────

_TITLE_TOKENS = {
    "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "miss", "prof",
    "prof.", "professor", "assoc", "a/prof", "clinical", "clin", "associate", "adj", "adjunct",
    "sir", "dame", "the",
}


def parse_name(full: str) -> tuple[str, str]:
    """
    Split a practitioner name into (first, last).
    Strips titles (Dr, Prof, Assoc Prof, Clinical Prof, etc.).
    Handles multi-token first names — takes the FIRST non-title token as `first`
    and the LAST token as `last`. Middle names dropped.

    Returns empty strings if name can't be parsed.
    """
    if not full:
        return "", ""
    # Replace punctuation except apostrophe and hyphen
    cleaned = re.sub(r"[^\w\s\-']", " ", full).strip()
    tokens = [t for t in cleaned.split() if t.lower() not in _TITLE_TOKENS]
    if len(tokens) < 2:
        return (tokens[0].lower() if tokens else ""), ""
    # Skip single-letter initials at the start (e.g. "A K M Fakhruzzaman Bhuiyan" → first=Fakhruzzaman)
    first_idx = 0
    while first_idx < len(tokens) - 1 and len(tokens[first_idx]) == 1:
        first_idx += 1
    return tokens[first_idx].lower(), tokens[-1].lower()


# ── Email format synthesis ───────────────────────────────────────────────────

# Known format templates. Keys used in domain_formats.json.
FORMAT_TEMPLATES = {
    "firstname.lastname": "{first}.{last}",
    "firstnamelastname": "{first}{last}",
    "flastname": "{first[0]}{last}",
    "firstnamel": "{first}{last[0]}",
    "firstname_lastname": "{first}_{last}",
    "lastname.firstname": "{last}.{first}",
    "f.lastname": "{first[0]}.{last}",
    "lastnamef": "{last}{first[0]}",
}


def synth_email(first: str, last: str, domain: str, fmt: str) -> str | None:
    """Build an email address for (first, last, domain) under the given format key."""
    if not first or not last or not domain:
        return None
    template = FORMAT_TEMPLATES.get(fmt)
    if not template:
        return None
    try:
        # Render manually to support {first[0]} style
        local = template
        local = local.replace("{first[0]}", first[0])
        local = local.replace("{last[0]}", last[0])
        local = local.replace("{first}", first)
        local = local.replace("{last}", last)
        # Keep only safe chars
        local = re.sub(r"[^\w.\-_]", "", local).lower()
        if not local:
            return None
        return f"{local}@{domain}"
    except (IndexError, KeyError):
        return None


def infer_format(example_email: str, first: str, last: str) -> str | None:
    """
    Given a known-real email and the owner's first/last, guess which format
    template generated it. Returns a format key or None.
    """
    if "@" not in example_email:
        return None
    local = example_email.split("@", 1)[0].lower()
    first = first.lower()
    last = last.lower()
    if not first or not last:
        return None
    checks = [
        ("firstname.lastname", f"{first}.{last}"),
        ("firstname_lastname", f"{first}_{last}"),
        ("firstnamelastname", f"{first}{last}"),
        ("flastname", f"{first[0]}{last}"),
        ("firstnamel", f"{first}{last[0]}"),
        ("lastname.firstname", f"{last}.{first}"),
        ("f.lastname", f"{first[0]}.{last}"),
        ("lastnamef", f"{last}{first[0]}"),
    ]
    for key, expected in checks:
        if local == expected:
            return key
    return None


EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", re.I)


def extract_emails(text: str) -> list[str]:
    """Extract plausible email addresses from arbitrary text (HTML, page body)."""
    return list({m.group(0).lower() for m in EMAIL_RE.finditer(text or "")})
