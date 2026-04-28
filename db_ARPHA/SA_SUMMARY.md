# South Australia — Final Practitioner Reach Report

**Date:** 2026-04-28
**Source data:** AHPRA practitioner registry (SA), scraped statewise
**Output file:** `sa_practitioners_enriched.csv` (11,927 rows)

## Headline numbers

- **Total SA practitioners:** 11,927
- **Email-deliverable (catch_all):** **10,008 (83.9%)**
- **Unverified:** 1,919
- **Failed / pending:** 0

## Email source breakdown

| email_source | Count | % |
|---|---|---|
| hospital_postcode (synthesized via state hospital domain) | 9,834 | 82.5% |
| gp_clinic (verified GP clinic website + DNS) | 185 | 1.6% |
| gp_unresolved (GP not on Halaxy directory) | 1,783 | 14.9% |
| unresolved_postcode | 125 | 1.0% |

## Top catch_all domains

| Domain | Count |
|---|---|
| sahealth.sa.gov.au | 9,834 |
| trinitymedical.com.au | 13 |
| fountaincorner.com.au / bridgeclinic.com.au | 7 each |
| (other small clinic domains) | ~145 |

## Why SA matches NSW/QLD pattern (83.9%)

SA Health centralizes mail at `sahealth.sa.gov.au`. All 10 SA Local Health
Networks (Central Adelaide, Northern Adelaide, Southern Adelaide, Women's
& Children's, Barossa Hills Fleurieu, Yorke & Northern, Riverland Mallee
Coorong, Eyre & Far North, Limestone Coast, Flinders & Upper North) resolve
to that single domain. **One Disify catch_all on `sahealth.sa.gov.au`
promoted 9,834 hospital staff emails via trust-domain.**

## Methodology

1. **AIHW MyHospitals API** → 101 SA public hospitals with lat/lon + LHN
2. **LHN → domain map** in `resolve_domains.py` (10 SA LHNs all → `sahealth.sa.gov.au`)
3. **Postcode → nearest hospital** index (194 SA postcodes)
4. **Halaxy public sitemap** → 1,968 SA GPs evaluated, 603 matched with full clinic data (30.6% rate, lower than VIC/NSW's ~40%)
5. **Clinic domain guesser** (DNS + content + medical-keyword title) → 471 unique clinic clusters resolved:
   - 128 verified `dns+content` (precision-grade)
   - 130 `mx_only`
   - 213 no MX match
   - **185 GPs got real clinic emails**
6. **Trust-domain seeding** — one targeted Disify probe of `sahealth.sa.gov.au`
   to enable bulk promotion (avoids 9,834 redundant Disify calls)
7. **Disify** for the 116 small-clinic candidate emails after seed promotion

## What's NOT in this deliverable

- Sending pipeline — held for separate scoping
- LinkedIn channel for SA (LinkedIn agent has been VIC-only so far)
- WA / TAS / NT — pipeline ready (hospitals, postcode index, LHN map all pre-staged)
- National dedupe on AHPRA registration number
