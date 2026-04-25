"""
STEP 4 — Private GP / clinic practice resolver.

STATUS: PARKED / NEEDS PLAYWRIGHT REWRITE.

During implementation we found that:
  - HotDoc search results are SPA-rendered (JS-only); requests gets an empty
    shell page with zero practice links.
  - HealthEngine search is the same — SPA, no server-side results.
  - DuckDuckGo HTML endpoint rate-limits hard after ~30-50 queries in a session
    (the hospital-domain resolve burned through our quota).

For a working GP resolver we need one of:
  A. Playwright-based SPA rendering (slow but reliable). Reuse
     linkedin_outreach/browser_profile plumbing.
  B. HotDoc/HealthEngine private JSON API endpoints (requires traffic inspection
     to discover, then may need auth tokens).
  C. Bing HTML search as an alternative to DDG (untested).

For the initial pipeline run we bypass this step — GPs fall through to the
hospital-domain synthesis from Step 2. The SMTP probe in Step 5 will return
550 for those cases and we'll mark them `email_confidence=failed`. Post-run
coverage telemetry tells us whether GP resolution is worth revisiting.

The skeleton below is kept as a starting point for the Playwright rewrite.
"""
from __future__ import annotations
import argparse
import csv
import re
import sys
import time
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config
from common import append_csv, delay, extract_emails, read_csv, parse_name

FIELDS = [
    "practitioner_id", "first", "last", "suburb", "postcode",
    "practice_name", "practice_domain", "practice_email",
    "method", "scraped_at", "notes",
]

_SKIP_HOSTS = (
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "instagram.com", "wikipedia.org", "yelp.com", "google.com", "yellowpages.com",
    "ratemds.com", "whitecoat.com.au", "ahpra.gov.au", "betterhealth.vic.gov.au",
    "myhospitals.gov.au", "healthdirect.gov.au", "duckduckgo.com", "bing.com",
    "updoc.com.au", "mable.com.au", "healthshare.com.au", "tripadvisor.com",
)

GENERIC_LOCALPARTS = {
    "info", "admin", "contact", "enquiries", "enquiry", "reception", "hello",
    "support", "help", "office", "noreply", "no-reply", "donotreply", "hr",
    "privacy", "marketing", "careers", "events", "general", "accounts", "billing",
    "bookings", "appointments", "referrals", "mail", "reception@", "team",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9",
    })
    return s


def _fetch(sess: requests.Session, url: str) -> str | None:
    try:
        r = sess.get(url, timeout=config.HTTP_TIMEOUT_S, allow_redirects=True)
        if r.status_code == 200:
            return r.text
    except Exception:
        return None
    return None


def _domain_of(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.").lower()


# ── Provider-specific searches ───────────────────────────────────────────────

def _search_hotdoc(sess: requests.Session, first: str, last: str, suburb: str) -> list[str]:
    """Return candidate practice URLs from HotDoc search results."""
    q = quote_plus(f"{first} {last} {suburb}")
    url = f"https://www.hotdoc.com.au/search?q={q}"
    html = _fetch(sess, url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    candidates = []
    # HotDoc practice profile URLs follow /medical-centres/.../..
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/medical-centres/" in href or "/clinics/" in href:
            if href.startswith("/"):
                href = urljoin("https://www.hotdoc.com.au", href)
            candidates.append(href)
    # Dedupe, preserve order
    seen = set()
    out = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out[:5]


def _search_healthengine(sess: requests.Session, first: str, last: str, suburb: str) -> list[str]:
    q = quote_plus(f"{first} {last} {suburb}")
    url = f"https://healthengine.com.au/search?query={q}"
    html = _fetch(sess, url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href]"):
        href = a.get("href", "") or ""
        if "/practice/" in href or "/gp/" in href or "/clinic/" in href:
            if href.startswith("/"):
                href = urljoin("https://healthengine.com.au", href)
            out.append(href)
    # dedupe
    seen = set()
    res = []
    for c in out:
        if c in seen:
            continue
        seen.add(c)
        res.append(c)
    return res[:5]


def _search_ddg(sess: requests.Session, first: str, last: str, suburb: str) -> list[str]:
    q = f'"{first} {last}" {suburb} VIC GP OR doctor'
    try:
        r = sess.post(
            "https://html.duckduckgo.com/html/",
            data={"q": q},
            timeout=config.HTTP_TIMEOUT_S,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "") or ""
            parsed = urlparse(href)
            if parsed.path == "/l/" and parsed.query:
                from urllib.parse import parse_qs, unquote
                qs = parse_qs(parsed.query)
                if "uddg" in qs:
                    href = unquote(qs["uddg"][0])
            if href.startswith("http"):
                host = urlparse(href).hostname or ""
                if any(s in host for s in _SKIP_HOSTS):
                    continue
                links.append(href)
            if len(links) >= 5:
                break
        return links
    except Exception:
        return []


# ── Email extraction from a practice URL ─────────────────────────────────────

def _practice_page_emails(sess: requests.Session, practice_url: str) -> tuple[str, list[str]]:
    """Return (practice_domain, emails_found) from practice URL + its /contact page."""
    host = _domain_of(practice_url)
    if not host:
        return "", []

    emails = set()
    for path in ("", "/contact", "/contact-us", "/about", "/doctors", "/our-team"):
        target = urljoin(practice_url, path) if path else practice_url
        html = _fetch(sess, target)
        if html:
            emails.update(extract_emails(html))
        delay(0.4, 0.9)

    # Prefer emails at the practice's own domain
    on_domain = sorted(e for e in emails if e.endswith("@" + host))
    # Strip generic localparts, keep only first few
    return host, on_domain[:5]


def _best_email(emails: list[str]) -> str:
    """Pick the best email from a list — prefer reception/contact over noreply, etc."""
    if not emails:
        return ""
    ranked = sorted(
        emails,
        key=lambda e: (
            0 if e.split("@", 1)[0] in ("reception", "contact", "info", "admin", "enquiries") else 1,
            len(e),
        ),
    )
    return ranked[0]


# ── Main entry point ─────────────────────────────────────────────────────────

def resolve_one(sess: requests.Session, practitioner_id: str, name: str, suburb: str, postcode: str) -> dict:
    first, last = parse_name(name)
    result = {
        "practitioner_id": practitioner_id,
        "first": first,
        "last": last,
        "suburb": suburb,
        "postcode": postcode,
        "practice_name": "",
        "practice_domain": "",
        "practice_email": "",
        "method": "",
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": "",
    }
    if not first or not last:
        result["method"] = "skip_no_name"
        return result

    # Try providers in order.
    for method, searcher in [
        ("hotdoc", _search_hotdoc),
        ("healthengine", _search_healthengine),
        ("ddg", _search_ddg),
    ]:
        try:
            urls = searcher(sess, first, last, suburb)
        except Exception as e:
            urls = []
            result["notes"] += f"[{method}-err:{type(e).__name__}]"
        delay(*config.HTTP_JITTER_S)
        if not urls:
            continue
        # Try each candidate URL until we find a practice-domain email
        for u in urls:
            practice_domain, emails = _practice_page_emails(sess, u)
            if not practice_domain:
                continue
            if any(s in practice_domain for s in _SKIP_HOSTS):
                continue
            best = _best_email(emails)
            if best:
                result["practice_domain"] = practice_domain
                result["practice_email"] = best
                result["practice_name"] = ""
                result["method"] = method
                return result
            # No email on this page — still record the domain as a useful fallback
            if practice_domain and not result["practice_domain"]:
                result["practice_domain"] = practice_domain
                result["method"] = method + "_domain_only"
            delay(*config.HTTP_JITTER_S)
        if result["practice_domain"]:
            return result
    if not result["method"]:
        result["method"] = "unresolved"
    return result


def resume_cache() -> set[str]:
    if not config.GP_PRACTICES_CSV.exists():
        return set()
    return {r["practitioner_id"] for r in read_csv(config.GP_PRACTICES_CSV) if r["practitioner_id"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=10, help="Number of GP rows to resolve (dry-test).")
    ap.add_argument("--limit-speciality", default="General practice",
                    help="Only practitioners whose speciality contains this string.")
    ap.add_argument("--all", action="store_true", help="Run all matching rows (ignore --sample).")
    args = ap.parse_args()

    already = resume_cache()
    print(f"[gp] already cached: {len(already)}")

    pracs = read_csv(config.VIC_PRACTITIONERS_CSV)
    candidates = [
        r for r in pracs
        if args.limit_speciality.lower() in (r.get("speciality") or "").lower()
        and r["practitioner_id"] not in already
    ]
    print(f"[gp] candidates matching speciality~{args.limit_speciality!r}: {len(candidates)}")
    if not args.all:
        candidates = candidates[: args.sample]
    print(f"[gp] processing {len(candidates)} rows")

    sess = _session()
    batch = []
    for i, r in enumerate(candidates, 1):
        loc = r.get("location", "")
        # "South Melbourne, VIC, 3205"
        suburb = loc.split(",")[0].strip() if "," in loc else ""
        postcode = r.get("postcode_searched", "")
        print(f"[gp] ({i}/{len(candidates)}) {r['name']}  [{suburb}, {postcode}]")
        res = resolve_one(sess, r["practitioner_id"], r["name"], suburb, postcode)
        batch.append(res)
        print(f"    -> domain={res['practice_domain'] or '-'}  email={res['practice_email'] or '-'}  method={res['method']}")
        # Flush every 5 rows so progress is resumable
        if len(batch) >= 5:
            append_csv(batch, config.GP_PRACTICES_CSV, fieldnames=FIELDS)
            batch = []
    if batch:
        append_csv(batch, config.GP_PRACTICES_CSV, fieldnames=FIELDS)

    # Summary
    rows = read_csv(config.GP_PRACTICES_CSV)
    resolved = sum(1 for r in rows if r["practice_domain"])
    with_email = sum(1 for r in rows if r["practice_email"])
    print()
    print(f"[gp] total cached     : {len(rows)}")
    print(f"[gp] with domain      : {resolved}")
    print(f"[gp] with email       : {with_email}")


if __name__ == "__main__":
    main()
