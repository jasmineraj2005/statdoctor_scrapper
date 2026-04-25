"""
STEP 1b — Scrape health.vic.gov.au's public-hospitals directory.

Output: data/healthvic_directory.csv
  health_service_name, website_url, domain, segment (metro/rural)

This complements AIHW: AIHW gives us hospital-level rows with LHN/PHN tags but
no official website. health.vic gives us the managing health-service
organisation and its external website. We join on name similarity in STEP 1c.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config
from common import append_csv

SOURCE_URL = "https://www.health.vic.gov.au/hospitals-and-health-services/public-hospitals-in-victoria"
FIELDS = ["health_service_name", "website_url", "domain", "segment"]

_EXCLUDE_HOSTS = (
    "health.vic.gov.au", "vic.gov.au", "twitter.com", "facebook.com",
    "linkedin.com", "youtube.com", "instagram.com", "mailto:",
)


def clean_name(raw: str) -> str:
    """Strip 'External Link' suffix and fix missing-space issues like 'MonashHealth'."""
    s = re.sub(r"External\s*Link\s*$", "", raw).strip()
    # Insert space between consecutive word boundaries: 'MonashHealth' → 'Monash Health'
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    # Normalise whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def domain_of(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.").lower()


def fetch() -> list[dict]:
    r = requests.get(SOURCE_URL, headers={"User-Agent": config.USER_AGENT}, timeout=config.HTTP_TIMEOUT_S)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    # The page has two segments: metro (first table/list) and rural (second). We detect
    # via section headings. Pragmatic approach: find all headings, take text between
    # "Melbourne metropolitan" heading and "Regional and rural" heading, then rest.
    full_text = soup.get_text("\n", strip=True).lower()
    split_marker = "regional and rural"
    metro_cutoff = full_text.find(split_marker)

    # Instead of complex text-position tracking, use sibling walks:
    # Find every external link and classify by nearest preceding heading.
    rows = []
    seen = set()
    current_segment = "metro"  # default
    for el in soup.find_all(["h1", "h2", "h3", "h4", "a"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            text = el.get_text(" ", strip=True).lower()
            if "regional" in text or "rural" in text:
                current_segment = "rural"
            elif "metropolitan" in text or "melbourne metro" in text:
                current_segment = "metro"
            continue
        href = el.get("href", "") or ""
        if not href.startswith("http"):
            continue
        host = urlparse(href).hostname or ""
        if any(x in host for x in _EXCLUDE_HOSTS):
            continue
        if "mailto:" in href:
            continue
        name = clean_name(el.get_text(" ", strip=True))
        if not name or name.lower() in ("external link",):
            continue
        dom = domain_of(href)
        if not dom:
            continue
        key = (name.lower(), dom)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "health_service_name": name,
            "website_url": href,
            "domain": dom,
            "segment": current_segment,
        })
    return rows


def main():
    print(f"[healthvic] fetching {SOURCE_URL}")
    rows = fetch()
    print(f"[healthvic] extracted {len(rows)} health service → website pairs")

    out_path = config.DATA_DIR / "healthvic_directory.csv"
    if out_path.exists():
        out_path.unlink()
    append_csv(rows, out_path, fieldnames=FIELDS)
    print(f"[healthvic] wrote {out_path}")

    # Sanity: known metros must be present
    domains = {r["domain"] for r in rows}
    for probe in ["alfredhealth.org.au", "rch.org.au", "thermh.org.au", "svhm.org.au", "monashhealth.org"]:
        print(f"[healthvic] sanity: {probe} present? {'YES' if probe in domains else 'no'}")

    # Segment breakdown
    from collections import Counter
    seg = Counter(r["segment"] for r in rows)
    print(f"[healthvic] segments: {dict(seg)}")


if __name__ == "__main__":
    main()
