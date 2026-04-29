# email_enrichment

Email-deliverability enrichment pipeline for AHPRA-scraped Australian
practitioners. Takes the raw practitioner registry (`db_ARPHA/{state}_practitioners.csv`)
and produces an enriched CSV with a candidate email, source, format, and
confidence tier per row.

**Status (2026-04-29):** all 7 states complete.
**National total:** 103,256 sendable / 126,837 (81.4%).

| State | Sendable | Rate | Summary |
|---|---|---|---|
| VIC | 18,853 / 24,997 | 75.4% | `db_ARPHA/VIC_SUMMARY.md` |
| NSW | 33,367 / 40,200 | 83.0% | `db_ARPHA/NSW_SUMMARY.md` |
| QLD | 22,317 / 27,032 | 82.6% | `db_ARPHA/QLD_SUMMARY.md` |
| SA  | 10,008 / 11,927 | 83.9% | `db_ARPHA/SA_SUMMARY.md` |
| WA  | 13,724 / 16,591 | 82.7% | `db_ARPHA/WA_SUMMARY.md` |
| TAS |  3,418 /  4,193 | 81.5% | `db_ARPHA/TAS_SUMMARY.md` |
| NT  |  1,569 /  1,897 | 82.7% | `db_ARPHA/NT_SUMMARY.md` |

## How emails are synthesised

Three sources, in order of confidence:

1. **Hospital postcode** — practitioner's location postcode → nearest public
   hospital → hospital's mail domain. Most rows for hospital-based
   specialties (anaesthetics, surgery, etc.). Centralized state-health
   domains (e.g. `health.nsw.gov.au`) cover most of these in one shot.
2. **GP clinic** — for general practitioners only. Match name against the
   Halaxy public sitemap → JSON-LD clinic block → DNS-verify a guessed
   domain → fetch homepage → confirm medical-keyword match in title +
   distinctive clinic-name token in body. Precision-first; false positives
   damage sender reputation.
3. **Unresolved** — no email synthesised. Practitioner falls through to the
   LinkedIn outreach channel.

Once an email is synthesised it gets a confidence tier:

| Tier | Meaning |
|---|---|
| `catch_all` | Domain accepts mail (Disify-verified or trust-domain-promoted). Sendable. |
| `unverified` | Email generated but Disify call timed out / domain unconfirmable. |
| `failed` | Domain invalid (no MX, disposable, etc.). |
| `pending` | Email generated, hasn't been Disify-verified yet. Should not exist after a final apply. |
| `n_a` | LinkedIn-only row, no email expected. |

## Pipeline

```
fetch_aihw_hospitals.py        → data/hospitals_{state}.csv
resolve_domains.py             → attaches mail domain to each hospital via LHN map
build_postcode_index.py        → data/postcode_domains_{state}.json
discover_formats.py            → data/domain_formats.json (shared, cross-state)
gp_resolver_sitemap.py         → data/gp_practices_{state}.csv (Halaxy match)
gp_domain_guesser.py           → data/gp_clinic_domains_{state}.json
disify_verify.py               → data/disify_probe_log.csv (shared)
apply_to_practitioners.py      → db_ARPHA/{state}_practitioners_enriched.csv
```

## Run a new state (or re-run an existing one)

```bash
cd email_enrichment
export EE_STATE=qld   # or pass --state on each call below
../venv/bin/python fetch_aihw_hospitals.py --state $EE_STATE
# Inspect data/hospitals_${EE_STATE}_raw.csv lhn_name distribution.
# Add LHN_DOMAINS entries in resolve_domains.py — query MX records before
# adding (most LHN websites have no MX; mail centralizes at state-health
# domain).
../venv/bin/python resolve_domains.py --state $EE_STATE
../venv/bin/python build_postcode_index.py --state $EE_STATE
../venv/bin/python -u gp_resolver_sitemap.py --state $EE_STATE --all
../venv/bin/python -u gp_domain_guesser.py --state $EE_STATE
../venv/bin/python apply_to_practitioners.py --state $EE_STATE

# ⚠ TRUST-DOMAIN SEED: if the centralized state-health domain has zero
# catch_all entries in disify_probe_log.csv, the first apply leaves
# thousands of rows pending. Probe ONE email on that domain (one-off
# Disify call → append catch_all row to disify_probe_log.csv), then
# re-apply. See SA_SUMMARY.md "Methodology" for the recipe. Saves ~10k
# Disify calls per state.

../venv/bin/python disify_verify.py        # any remaining clinic emails
../venv/bin/python apply_to_practitioners.py --state $EE_STATE   # final
```

## Hard rules

- **Never assign a hospital domain to a GP.** GPs work at private clinics.
  The `if "general practice" in speciality` short-circuit in
  `synthesise_email()` (`apply_to_practitioners.py`) must stay.
- **Precision over recall on clinic domain matching.** Keep
  `TITLE_KEYWORDS` + `BODY_SIGNALS` + distinctive-token gate in
  `gp_domain_guesser.verify_domain_for_clinic`. False positives damage
  sender reputation.
- **Always launch long runs with `python -u`.** Without `-u`, stdout is
  block-buffered when redirected and you can't tell if a job is hung or
  just quiet.
- **One commit per logical step.** HEREDOC commit messages with the
  `Co-Authored-By:` trailer.

## Files

| File | Role |
|---|---|
| `AGENT.md` | Live operational state — current numbers, jobs running, RESUME-FROM-HERE notes for the next agent |
| `ROADMAP.md` | Historical / pre-pilot planning; superseded by AGENT.md for current state |
| `config.py` | Paths, confidence tier labels, tunables |
| `common.py` | CSV read/write, name parsing, email synthesis primitives |
| `apply_to_practitioners.py` | Step 6 — joins all signals into the enriched CSV. GP short-circuit + trust-domain promotion live here |
| `build_postcode_index.py` | Step 2 — postcode → hospital-domain index |
| `discover_formats.py` | Step 3 — email format discovery per hospital domain |
| `disify_verify.py` | Step 5 — Disify API domain validation with DNS MX fallback |
| `reverify_unverified.py` | Re-runs Disify on `unverified` rows (results auto-upgrade on next apply) |
| `gp_resolver_sitemap.py` | Step 4a — Halaxy sitemap download + local match + JSON-LD extract |
| `gp_domain_guesser.py` | Step 4b — clinic-name → domain via DNS + content verification |
| `resolve_domains.py` | LHN → mail-domain map, attaches domains to hospitals |
| `data/halaxy_sitemap_index.json` | 17,623 national GP profile URLs by (first, last) — shared across states |
| `data/disify_probe_log.csv` | Cross-state Disify verdict log (trust-domain promotion reads from this) |
| `data/domain_formats.json` | Cross-state email-format-by-domain cache |

## What's NOT here

- **Sending pipeline** (mailbox rotation, message templates, send cadence,
  AU Spam Act footer with physical address + opt-out) — held for separate
  scoping. See "Open questions — sender pipeline" in `AGENT.md`.
- **National merge** — `db_ARPHA/all_states_practitioners_enriched.csv`
  deduped on AHPRA `practitioner_id`. Some practitioners hold multi-state
  registration; pick the row whose `pipeline=email` has
  `email_confidence=catch_all` if available, else the LinkedIn pipeline
  row. Not yet built.
- **LinkedIn outreach** — that's a separate agent in `linkedin_outreach/`,
  out of scope here.
