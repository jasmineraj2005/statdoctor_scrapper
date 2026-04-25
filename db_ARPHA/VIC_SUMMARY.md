# Victoria — Final Practitioner Reach Report

**Date:** 2026-04-25
**Source data:** AHPRA practitioner registry (Victoria), scraped statewise
**Output file:** `vic_practitioners_enriched.csv` (24,997 rows)

## Headline numbers

- **Total VIC practitioners:** 24,997
- **Reachable via email or LinkedIn:** 20,728 (**82.9%**)
- **Email-deliverable (catch_all):** 18,853 (**75.4%**)

## Channel breakdown

| Channel | Count | % of total |
|---|---|---|
| ✅ Email — domain verified, safe to send | 18,853 | 75.4% |
| 🔵 LinkedIn pipeline (parallel reach) | 3,383 | 13.5% |
| ❌ No reach found | 4,269 | 17.1% |

(Some practitioners are reachable via both channels — totals don't sum to 100%.)

## Email confidence detail

| email_confidence | Count | Action |
|---|---|---|
| catch_all | 18,853 | Send |
| failed | 335 | Don't send (bad domain) |
| (no email) | 5,809 | LinkedIn or unreachable |

The "no email" 5,809 split:
- 5,540 GPs not on Halaxy directory (LinkedIn fallback)
- 265 practitioners with no usable postcode/location
- 4 with name parsing failures

## Bonus deliverables

- **966 GPs** also have a verified **clinic name + phone + street address** (from Halaxy sitemap).
  Available for phone outreach or direct mail. See `email_enrichment/data/gp_practices.csv`.
- **698 GP clinic websites** discovered + verified (in `email_enrichment/data/gp_clinic_domains.json`).

## Methodology

1. Hospital-domain synthesis for hospital-based specialists (postcode → nearest MX-verified hospital → `firstname.lastname@hospitaldomain`)
2. Halaxy public sitemap parsed locally to extract clinic info for 2,628 GPs (no search-engine queries, no captcha)
3. Clinic websites resolved by candidate-domain generation + DNS MX check + content/title verification (precision-first — required medical-specific keyword in page title)
4. Email-domain validation via Disify API (free); rate-limit avoidance via concurrency=2, jitter=3s, IP rotation
5. Domain-trust promotion: any unverified email on a domain that's been catch_all elsewhere is auto-flipped

## What's NOT in the deliverable yet

- Sending pipeline (mailbox rotation, message templates, send cadence) — held for separate scoping
- Other states (NSW pilot starting next; QLD/SA/WA/NT after)
- National dedupe on AHPRA registration number (some practitioners hold multi-state registration)
