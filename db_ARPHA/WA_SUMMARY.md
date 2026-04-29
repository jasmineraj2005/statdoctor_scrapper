# Western Australia — Final Practitioner Reach Report

**Date:** 2026-04-29
**Source data:** AHPRA practitioner registry (WA), scraped statewise
**Output file:** `wa_practitioners_enriched.csv` (16,591 rows)

## Headline numbers

- **Total WA practitioners:** 16,591
- **Email-deliverable (catch_all):** **13,724 (82.7%)**
- **Unverified:** 2,864
- **Failed / pending:** 3 / 0

## Email source breakdown

| email_source | Count | % |
|---|---|---|
| hospital_postcode (synthesized via state hospital domain) | 13,525 | 81.5% |
| gp_clinic (verified GP clinic website + DNS) | 221 | 1.3% |
| gp_unresolved (GP not on Halaxy directory) | 2,695 | 16.2% |
| unresolved_postcode | 150 | 0.9% |

## Top catch_all domains

| Domain | Count |
|---|---|
| health.wa.gov.au | 10,174 |
| sjog.org.au (St John of God Health) | 3,351 |
| (small clinic domains) | ~199 |

## Why WA matches NSW/QLD/SA pattern (82.7%)

WA Health centralizes mail at `health.wa.gov.au` — same pattern as the other
state-health systems. All four metropolitan health services (North Metro,
East Metro, South Metro, Child & Adolescent Health) plus WA Country Health
Service resolve to the one domain. Plus `sjog.org.au` covers St John of God
Health Care, which is a major non-government provider with significant WA
presence. **One Disify catch_all on `health.wa.gov.au` promoted 10,174
hospital staff via trust-domain.**

## Methodology

1. **AIHW MyHospitals API** → WA public hospitals with lat/lon + LHN
2. **LHN → domain map** in `resolve_domains.py` (5 WA LHNs → `health.wa.gov.au`)
3. **Postcode → nearest hospital** index (206 WA postcodes)
4. **Halaxy public sitemap** → 2,916 WA GPs evaluated, 643 matched with full clinic data (22% rate, lower than VIC/NSW's ~40% and SA's 30.6% — Halaxy adoption looks lower in WA)
5. **Clinic domain guesser** → 529 unique clinic clusters → 174 dns+content verified, 133 mx_only, 222 unmatched. **221 GPs got real clinic emails**
6. **Trust-domain seeding** — single Disify probe of `health.wa.gov.au` to enable bulk promotion (avoided ~10k redundant Disify calls)
7. **Disify** for 169 small-clinic candidate emails after seed promotion

## Operational note

WA guesser stalled mid-run after the mac slept overnight — a stuck socket
post-resume that the in-process 30s budget couldn't escape (the budget
checks at the top of each candidate iteration, not inside a hung
`requests.get`). Killed and restarted; resume from saved JSON was free.
Worth flagging if TAS/NT runs span an overnight again.

## What's NOT in this deliverable

- Sending pipeline — held for separate scoping
- LinkedIn channel for WA (LinkedIn agent has been VIC-only so far)
- TAS / NT — pipeline ready (hospitals + postcode index + LHN map pre-staged)
- National dedupe on AHPRA registration number
