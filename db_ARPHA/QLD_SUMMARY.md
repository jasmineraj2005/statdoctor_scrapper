# Queensland — Final Practitioner Reach Report

**Date:** 2026-04-28
**Source data:** AHPRA practitioner registry (QLD), scraped statewise
**Output file:** `qld_practitioners_enriched.csv` (27,032 rows)

## Headline numbers

- **Total QLD practitioners:** 27,032
- **Email-deliverable (catch_all):** **22,317 (82.6%)**
- **Unverified:** 4,705
- **Failed:** 10

## Email source breakdown

| email_source | Count | % |
|---|---|---|
| hospital_postcode (synthesized via state hospital domain) | 21,975 | 81.3% |
| gp_clinic (verified GP clinic website + DNS) | 416 | 1.5% |
| gp_unresolved (GP not on Halaxy directory) | 4,320 | 16.0% |
| unresolved_postcode | 320 | 1.2% |
| unresolved_name | 1 | 0.0% |

## Top catch_all domains

| Domain | Count |
|---|---|
| health.qld.gov.au | 19,799 |
| visioneyeinstitute.com.au | 1,028 |
| svha.org.au | 989 |
| ramsayhealth.com.au | 158 |
| (small clinic domains) | ~343 |

## Why QLD ≈ NSW (82.6% vs 83.0%)

QLD Health centralizes mail at `health.qld.gov.au` — the same pattern as
`health.nsw.gov.au`. All 16 QLD Hospital and Health Services (LHNs) resolve to
that one domain. **One verified Disify catch_all on `health.qld.gov.au`
promoted 19,799 hospital staff emails without 19,799 redundant Disify calls**
(trust-domain promotion in `apply_to_practitioners.py`).

By contrast, VIC has many independent hospital domains, so per-domain
verification was needed → 75.4% reach.

## Methodology

1. **AIHW MyHospitals API** → 220 QLD public hospitals with lat/lon + LHN
2. **LHN → domain map** in `resolve_domains.py` (16 QLD LHNs all → `health.qld.gov.au`)
3. **Postcode → nearest hospital** index (311 QLD postcodes)
4. **Halaxy public sitemap** → 4,736 QLD GPs in `gp_practices_qld.csv`, 1,325 with full clinic name+address
5. **Clinic domain guesser** (DNS + content + medical-keyword title) → 1,121 unique clinic clusters resolved:
   - 339 verified `dns+content` (precision-grade)
   - 290 `mx_only` (MX exists but homepage didn't pass medical-content gate)
   - 492 no MX match
   - **416 GPs got real clinic emails**
6. **Disify** for the 312 small-clinic candidate emails after trust-domain promotion handled the bulk
7. **gp_domain_guesser throughput patch (2026-04-28):** mid-run the QLD tail
   collapsed from ~15 clusters/min to ~0.4/min (~28 hr ETA). Patched in place:
   capped candidates at 15/cluster (was up to 60) + added 30s wallclock budget
   per cluster. Restored throughput; resume from saved JSON was free.

## What's NOT in this deliverable

- Sending pipeline (mailbox rotation, message templates, send cadence) — held for separate scoping
- LinkedIn channel for QLD (LinkedIn agent has been VIC-only so far)
- Remaining states (SA/WA/TAS/NT) — pipeline ready, LHN map already seeded for all four (`resolve_domains.py` 2026-04-28 update)
- National dedupe on AHPRA registration number (some practitioners hold multi-state registration)
