# Northern Territory — Final Practitioner Reach Report

**Date:** 2026-04-29
**Source data:** AHPRA practitioner registry (NT), scraped statewise
**Output file:** `nt_practitioners_enriched.csv` (1,897 rows)

## Headline numbers

- **Total NT practitioners:** 1,897
- **Email-deliverable (catch_all):** **1,569 (82.7%)**
- **Unverified:** 328
- **Failed / pending:** 0 / 0

## Email source breakdown

| email_source | Count | % |
|---|---|---|
| hospital_postcode (synthesized via state hospital domain) | 1,552 | 81.8% |
| gp_clinic (verified GP clinic website + DNS) | 17 | 0.9% |
| gp_unresolved (GP not on Halaxy directory) | 299 | 15.8% |
| unresolved_postcode | 29 | 1.5% |

## Top catch_all domains

| Domain | Count |
|---|---|
| nt.gov.au | 1,552 |
| palmerstongpsuperclinic.com.au | 4 |
| (very small clinic domains) | ~13 |

## Why NT uses `nt.gov.au` not `health.nt.gov.au`

Unlike other states where mail centralizes at `health.{state}.gov.au`, NT
Health uses the whole-of-government `nt.gov.au` domain. We confirmed this
during LHN map seeding: `health.nt.gov.au` has no MX record; `nt.gov.au`
does (3 MX hosts: `mi11/mi21/mi31.nt.gov.au`). NT Health staff use
`@nt.gov.au` addresses in practice. **One Disify catch_all on `nt.gov.au`
promoted 1,552 practitioner emails via trust-domain.**

## Methodology

1. **AIHW MyHospitals API** → NT public hospitals + LHN
2. **LHN → domain map** in `resolve_domains.py` (NTRHS → `nt.gov.au`)
3. **Postcode → nearest hospital** index for NT postcodes
4. **Halaxy public sitemap** → 316 NT GPs evaluated, 52 matched (16% rate — lowest of any state, NT has small GP footprint)
5. **Clinic domain guesser** → 44 unique clinic clusters → 13 dns+content verified, 9 mx_only, 22 unmatched. **17 GPs got real clinic emails**
6. **Trust-domain seeding** — single Disify probe of `nt.gov.au`
7. **Disify** for 6 small-clinic candidate emails after seed promotion

## What's NOT in this deliverable

- Sending pipeline — held for separate scoping
- LinkedIn channel for NT (LinkedIn agent has been VIC-only so far)
- **All 7 states are now complete.** Next: national merge (Task #6 in AGENT.md) — produce `db_ARPHA/all_states_practitioners_enriched.csv` deduped on AHPRA registration number
