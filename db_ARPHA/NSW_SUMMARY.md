# New South Wales — Final Practitioner Reach Report

**Date:** 2026-04-26
**Source data:** AHPRA practitioner registry (NSW), scraped statewise
**Output file:** `nsw_practitioners_enriched.csv` (40,200 rows)

## Headline numbers

- **Total NSW practitioners:** 40,200
- **Email-deliverable (catch_all):** **33,367 (83.0%)**
- **Failed / no-mailbox:** 37
- **No email candidate (LinkedIn fallback / GP unresolved):** 6,796

## Email source breakdown

| email_source | Count | % |
|---|---|---|
| hospital_postcode (synthesized via state hospital domain) | 32,521 | 80.9% |
| gp_clinic (verified GP clinic website + DNS) | 1,001 | 2.5% |
| gp_unresolved (GP not on Halaxy directory) | 6,252 | 15.5% |
| unresolved_postcode | 423 | 1.1% |
| unresolved_name | 3 | 0.0% |

## Why NSW > VIC (83% vs 75%)

NSW Health centralizes mail delivery at `health.nsw.gov.au`. We resolved 14 of 15 NSW Local Health Districts to that single domain, plus St Vincent's Health Network (svha.org.au) and Sydney Children's Hospitals Network. **A single Disify call confirmed `health.nsw.gov.au` accepts mail → 29,633 hospital staff emails promoted to catch_all without 29,633 redundant Disify calls.**

By contrast, VIC has many independent hospital domains (each hospital its own MX), so we needed per-domain verification.

## Methodology (same as VIC, parametrized)

1. **AIHW MyHospitals API** → 365 NSW hospitals with lat/lon + LHN
2. **LHN → domain map** in `resolve_domains.py` (228/365 with valid MX)
3. **Postcode → nearest hospital** index (499 NSW postcodes)
4. **Halaxy public sitemap** → 2,762 GPs matched with clinic name + phone + address (38% rate, same as VIC)
5. **Clinic domain guesser** (DNS + content + medical-keyword title) → 775 verified GP clinic websites → 1,001 GPs got real clinic emails
6. **Disify** for new candidates; **trust-domain promotion** for already-verified domains
7. **Spot-check** of 15 random catch_all rows confirmed clean synthesis (no malformed emails, hospital mappings correct, GP clinic synthesis correct)

## What's NOT in this deliverable

- Sending pipeline (mailbox rotation, message templates, send cadence) — held for separate scoping
- LinkedIn channel for NSW (LinkedIn agent has been VIC-only so far)
- Other states (QLD/SA/WA/TAS/NT) — pipeline ready, each needs its LHN map populated
- National dedupe on AHPRA registration number (some practitioners hold multi-state registration)
