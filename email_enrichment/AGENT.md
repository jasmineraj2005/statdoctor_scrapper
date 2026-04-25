# Email Enrichment Agent Prompt

You are the email enrichment agent for the ARPHA statdoctor scraper.
Working directory: `/Users/jasminebaldevraj/Desktop/ARPHA/statdoctor_scrapper/email_enrichment`

**READ `ROADMAP.md` first**, then skim `git log --oneline -10` for recent
commits. This AGENT.md captures the current live state including work done
today (2026-04-25) that post-dates the ROADMAP.

## Current state (2026-04-25)

**Pipeline rebuilt end-to-end with major scope changes. All local, no remote push.**

### Big changes since ROADMAP.md was last written

1. **GP handling rewritten.** Previously, 4,424 GPs were given hospital-postcode emails
   (e.g. a Sunbury GP got `@westernhealth.org.au`) — wrong: GPs work at private clinics.
   `synthesise_email()` in `apply_to_practitioners.py` now short-circuits GPs
   (speciality contains "general practice") → `email_source=gp_unresolved`.

2. **LinkedIn-pipeline rows now also get email candidates.** The `pipeline=linkedin`
   branch in `apply_to_practitioners.py` used to leave `candidate_email=""` and
   `email_confidence=n_a`. Now all rows get email synthesis regardless of pipeline —
   LinkedIn vs email is about outreach channel, not whether an email is generated.

3. **Halaxy sitemap-based GP resolver** (`gp_resolver_sitemap.py`, new, replaces
   parked `gp_resolver.py`). Halaxy publishes 30 public practitioner sitemaps
   (robots-allowed, no auth). Build a local name-slug index once
   (`data/halaxy_sitemap_index.json`, 17,623 GP entries AU-wide), then match VIC GPs
   locally. Fetch matched profile → parse JSON-LD `worksFor` block → extract
   clinic_name, phone, street, suburb, postcode. **Zero search engines, zero captcha.**
   Output: `data/gp_practices.csv`.
   - **Match rate on VIC GPs: 40% (2,628 / 6,506)** — genuine ceiling; the other ~60% are simply not on Halaxy.
   - Fuzzy cascade: exact `first|last` → initial+last → token+last → last-only-if-globally-unique.
   - Precision filter: reject cross-state fuzzy matches (clinic postcode not 3xxx/8xxx).

4. **Clinic domain guesser** (`gp_domain_guesser.py`, new). For each unique clinic
   from gp_practices.csv: generate candidate domains from clinic name, DNS-verify
   MX, fetch homepage, verify by content+title match. Precision-first — requires
   BOTH a distinctive clinic-name token AND a medical-specific title keyword
   (`medical`, `clinic`, `gp `, `medicare`, etc.). "health" alone is disallowed —
   too generic, causes false positives on suburb-tourism / corporate-health sites.
   Output: `data/gp_clinic_domains.json`.

5. **Postcode index bug fix** (`build_postcode_index.py`). Previously seeded only from
   `postcode_searched` column, which is corrupted to 2-char alpha codes (`AB`, `AC`)
   for ~1,200 rows. Now falls back to regex-extracting a 4-digit postcode from
   the `location` field. Recovered 952 rows from `unresolved_postcode`.

6. **Reverify flow** (`reverify_unverified.py`, new). Disify's `unverified` verdict
   often clears on retry (API flakiness, transient DNS). Bypasses disify_verify's
   `load_done` dedup and re-runs for every `email_confidence=unverified` row;
   `_load_smtp_results()` in apply_to_practitioners picks the latest `verified_at`
   so results auto-upgrade on re-apply.

### Current numbers (VIC, 24,997 practitioners)

After apply_to_practitioners run with jobs A+B complete, C partial:

| email_confidence | count | meaning |
|---|---|---|
| catch_all | ~11,500+ | domain-verified, safe to send |
| unverified | ~8,000-12,000 | email generated, domain unconfirmed |
| failed | ~300 | invalid domain |
| (no email) | ~4,000 | gp_unresolved + unresolved_postcode + unresolved_name |

Target ceiling (after A+C complete): **~13,500–15,000 sendable emails** (55-60% of 24,997).
Structural gap: ~3,800 GPs simply aren't on Halaxy — LinkedIn is their only channel.

### Jobs running / just finished (check before starting new work)

- **A. `reverify_unverified.py`** — re-Disifies all 5,800 `unverified` rows. Logs at `/tmp/reverify.log`. ~1-1.5 hrs.
- **B. `disify_verify.py`** — verifies the 952 new candidates unlocked by postcode fix. Logs at `/tmp/disify_952.log`. Already done.
- **C. `gp_domain_guesser.py`** — resolves 1,977 unique clinic domains. Logs at `/tmp/domain_guess.log`. ~2-3 hrs.

Check process status with `ps -ef | grep -E "reverify|disify|gp_domain"` before launching anything that touches the same state files.

## Architecture (updated)

```
Step 1abc  fetch_*  → data/hospitals_vic.csv (68 MX-verified)
Step 2     build_postcode_index.py → data/postcode_domains.json (445 postcodes)
Step 3     discover_formats.py     → data/domain_formats.json (81 domains)
Step 4a    gp_resolver_sitemap.py  → data/gp_practices.csv     (2,628 GPs w/ clinic data)
Step 4b    gp_domain_guesser.py    → data/gp_clinic_domains.json (per-clinic domain)
Step 5a    disify_verify.py        → data/disify_probe_log.csv (~24k rows)
Step 5b    reverify_unverified.py  → appends to disify_probe_log.csv
Step 6     apply_to_practitioners.py → db_ARPHA/vic_practitioners_enriched.csv
```

## YOUR NEXT STEPS IN ORDER

1. **Wait for jobs A & C to finish**, then re-run `apply_to_practitioners.py` to
   pick up all fresh Disify results + clinic data.

2. **Wire GP clinic emails into `apply_to_practitioners.py`.** After C finishes,
   `data/gp_clinic_domains.json` contains per-clinic verified domains. Extend
   `synthesise_email()`: if row is GP, look up clinic in gp_practices.csv, then
   look up resolved domain in gp_clinic_domains.json, synthesize
   `firstname.lastname@clinic_domain`. Fall back to `gp_unresolved` when no
   verified domain. Re-run Disify on the new synthesized emails.

3. **Freeze VIC deliverable.** Final `vic_practitioners_enriched.csv` + one-page
   client summary with reachability column (linkedin / email / both / none). Commit, hold push.

4. **NSW pilot.** Run the full pipeline on NSW (largest state; if it breaks on
   scale or a state-specific quirk, we find out once). Compare catch_all rate to
   VIC baseline. Then fan out QLD/SA/WA/NT in parallel.

5. **National merge.** `all_states_practitioners_enriched.csv` deduped on AHPRA
   registration number. Cross-reference against LinkedIn classification output
   so no practitioner is double-contacted across channels.

## HARD RULES

- **Never push to remote.** Hold all commits local until user approves.
- **Never assign a hospital domain to a GP.** GPs work at private clinics, not
  hospitals. The `if "general practice" in speciality` short-circuit in
  `synthesise_email` must stay.
- **Precision over recall on clinic domain matching.** A false positive (wrong
  clinic → wrong email) damages sender reputation. Keep the
  `TITLE_KEYWORDS` + `BODY_SIGNALS` + distinctive-token gate in
  `gp_domain_guesser.verify_domain_for_clinic`.
- **Never reject an exact (first, last) match as cross-state** — the doctor may
  have relocated. Only fuzzy matches (initial / token / last-only) face the
  `is_vic` postcode check.
- **Rate-limit hygiene.** 48h profile cool-down on LinkedIn is the LinkedIn
  agent's problem, not ours. But any web-scraping here should use polite
  sleeps (`0.8-1.8s` jitter per HTTP) and cache aggressively.
- **One commit per logical step.** HEREDOC commit messages with
  `Co-Authored-By:` trailer.
- **Do not touch** `linkedin_outreach/` — that's a separate agent.

## File-by-file map

| File | Role |
|---|---|
| `ROADMAP.md` | Historical — see this file for 2026-04-25+ state |
| `config.py` | Paths, confidence tier labels, tunables |
| `common.py` | CSV read/write, `parse_name`, `synth_email` |
| `apply_to_practitioners.py` | **Step 6** — joins all signals into final enriched CSV. GP short-circuit + location-postcode fallback live here |
| `build_postcode_index.py` | Step 2 — postcode→hospital-domain index with location fallback |
| `discover_formats.py` | Step 3 — email format discovery per hospital domain |
| `disify_verify.py` | Step 5 — Disify API domain validation with DNS MX fallback |
| `reverify_unverified.py` | Re-runs Disify on `unverified` rows; results auto-upgrade on next apply |
| `gp_resolver_sitemap.py` | Step 4a — Halaxy sitemap download + local GP match + JSON-LD extract |
| `gp_domain_guesser.py` | Step 4b — clinic-name → domain via DNS + content verification |
| `gp_probe*.py`, `gp_resolver_pw.py` | Abandoned exploration — kept for reference. Do not run |
| `data/halaxy_sitemap_index.json` | 17,623 national GP profile URLs by (first, last) |
| `data/gp_practices.csv` | 2,628 matched VIC GPs with clinic name/phone/address |
| `data/gp_clinic_domains.json` | (in progress) Per-clinic verified domain |

## When you are handed a new directive

- If it changes GP-handling logic, update BOTH `apply_to_practitioners.py`
  AND this AGENT.md. Include the reason in the commit body.
- If it changes schema of `vic_practitioners_enriched.csv`, note it here —
  the LinkedIn agent consumes this file.
- If a new data source is found (e.g. non-Halaxy GP directory), prefer
  sitemap/bulk download approaches over per-query search engines. Search
  engines captcha single IPs fast on this workload.
- Update THIS file (`AGENT.md`) so the next agent picks up accurate state.

## REPORT BACK with

- Step completed, files changed, commit hash.
- For any resolver / Disify run: rows in/out, verdict breakdown, any API
  errors / rate-limit hits.
- Projected sendable-email count delta after the step.
- Any user decision needed before the next step.
