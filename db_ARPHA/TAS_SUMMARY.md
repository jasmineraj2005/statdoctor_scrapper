# Tasmania — Final Practitioner Reach Report

**Date:** 2026-04-29
**Source data:** AHPRA practitioner registry (TAS), scraped statewise
**Output file:** `tas_practitioners_enriched.csv` (4,193 rows)

## Headline numbers

- **Total TAS practitioners:** 4,193
- **Email-deliverable (catch_all):** **3,418 (81.5%)**
- **Unverified:** 774
- **Failed / pending:** 1 / 0

## Email source breakdown

| email_source | Count | % |
|---|---|---|
| hospital_postcode (synthesized via state hospital domain) | 3,358 | 80.1% |
| gp_clinic (verified GP clinic website + DNS) | 62 | 1.5% |
| gp_unresolved (GP not on Halaxy directory) | 721 | 17.2% |
| unresolved_postcode | 52 | 1.2% |

## Top catch_all domains

| Domain | Count |
|---|---|
| health.tas.gov.au | 3,358 |
| (small clinic domains) | ~60 |

## Why TAS matches the pattern (81.5%)

TAS has a single Tasmanian Health Service. `health.tas.gov.au` (Department
of Health) handles all hospital staff mail. **One Disify catch_all on
`health.tas.gov.au` promoted 3,358 hospital staff via trust-domain.**
Slight dip vs WA (82.7%) — TAS has fewer non-government provider hits,
since the local clinic ecosystem is small.

## Methodology

1. **AIHW MyHospitals API** → TAS public hospitals + LHN
2. **LHN → domain map** in `resolve_domains.py` (single TAS LHN → `health.tas.gov.au`)
3. **Postcode → nearest hospital** index for TAS postcodes
4. **Halaxy public sitemap** → 783 TAS GPs evaluated, 230 matched with full clinic data (29% rate, similar to SA)
5. **Clinic domain guesser** → 157 unique clinic clusters → 40 dns+content verified, 47 mx_only, 70 unmatched. **62 GPs got real clinic emails**
6. **Trust-domain seeding** — single Disify probe of `health.tas.gov.au` to enable bulk promotion
7. **Disify** for 41 small-clinic candidate emails after seed promotion

## What's NOT in this deliverable

- Sending pipeline — held for separate scoping
- LinkedIn channel for TAS (LinkedIn agent has been VIC-only so far)
- NT — pipeline ready (hospitals + postcode index + LHN map pre-staged); single state remaining
- National dedupe on AHPRA registration number
