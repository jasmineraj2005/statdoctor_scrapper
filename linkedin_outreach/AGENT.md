# LinkedIn Agent Prompt

You are the LinkedIn outreach agent for the ARPHA statdoctor scraper.
Working directory: `/Users/jasminebaldevraj/Desktop/ARPHA/statdoctor_scrapper/linkedin_outreach`

**READ `ROADMAP.md` IN FULL before doing anything.** Then skim recent
`git log --oneline` for the last 3–5 commits so you know which step
completed most recently.

## Current state (2026-04-21)

Steps 1 – 8 complete. Classifier v2 landed. Most recent commits:
- `6bbce13` spec v2 — step5 harness fixtures + ROADMAP update
- `dd65859` spec v2 — niche engagement classifier + engagement_rate column
- `1156c03` step-6b fix — session_limit→send_cap + profile progress print
- `5fbc755` step-6b — main.py consumes subset CSV + new pipeline
- `0e7c2c3` step-7 — sheets_logger adds 3 new tabs
- `7a12bdc` step-6 — rewrite connector.py (semantic selectors, no-note)
- `3421191` step-6 prereq — More-menu live probe (Connect-primary confirmed)

All commits from `826322a` onward are LOCAL ONLY — **do not push without
user approval**.

### Step-8 dry-run (2026-04-20) results
50-row `--dry-run --limit 50` passed all 5 gate checks: classifications.csv
(52 rows), Processing Status (52), Reviewed Skipped populated, Influencers
VIC empty (0 influencers in sample — genuinely low yield), zero connects
fired, zero exceptions.

### Two open user decisions blocking step 9
1. **Medium-conf threshold.** Spec v2 said *"soft >= 4 required before
   connect (same as before, threshold unchanged)"*. Currently implemented
   as `MEDIUM == NORMAL == 4` (no medium uplift). If user wanted medium = 5
   with normal = 4 (gap of 1), bump `SOFT_THRESHOLD_NORMAL` consumers
   accordingly in `influencer_classifier.py`.
2. **Re-eval of the 52 dry-run rows.** Next run auto-migrates
   `vic_linkedin_classifications.csv` → `.v1.bak` (fresh v2 file), but
   `vic_processing_status.csv` still deduplicates those 52 rows out.
   Options A (delete status CSV → re-eval all 3988), B (clear only the
   terminal rows → re-eval 52), C (leave as-is).

## YOUR NEXT STEPS IN ORDER

1. **AWAIT user decisions** on the two open items above. Do not proceed
   to step 9 until resolved.

2. **STEP 9 — Staged real run** (requires explicit user approval even
   after (1) resolves).
   - Day 1: 10 connects. Monitor the first Follow-primary profile closely
     — `MORE_MENU_CONNECT_FMT` is a 4-way union that has NOT been exercised
     live (probe day only hit Connect-primary). First miss goes into the
     debug dump and the union gets extended.
   - Day 2: 25 connects if Day 1 is clean.
   - `main.py --limit 25` (or 10 on Day 1). Real mode; `config.DRY_RUN`
     stays False.
   - Commit after each day's results (row counts, connect_status breakdown,
     any Follow-primary profile encountered + whether fallback clicked).

3. **STEP 10 — Scale out** after staged run validates. Remaining ~3900
   rows processed in batches respecting 20–25/day cap, 30k lifetime cap,
   48h per-profile cool-down.

## HARD RULES (unchanged from v1)

- **Never push to remote.** Hold all commits local until user approves.
- **Never reuse seeds 7 or 42.** Use 13, 17, 23, 29.
- **Never bypass 48h cool-down** (`_visit_tracker.is_hot`). The
  orchestrator gates profile visits on it in `main._process_practitioner`.
- **Semantic selectors only** in `li_selectors.py` — no CSS class names
  (LinkedIn's classes are hashed and rotate).
- **30k lifetime connect cap** — reject ambiguous matches; precision over
  recall. `NAME_TOKEN_DELTA_MAX=1`, `NAME_HIGH_CONF_SCORE=95`.
- **Spec-lock: plain connect, NO note.** If the modal offers only
  Add-a-note, `connector` returns `send_needs_note` and stops.
- **One commit per logical step.** No batching. HEREDOC commit messages
  with `Co-Authored-By:` trailer.
- **Do not touch** `email_enrichment/`, `scraper/`, `db_ARPHA/`.
- **Stop on captcha/challenge.** `searcher._is_rate_limited` already
  covers the known shapes; don't bypass.

## REPORT BACK with

- Step completed + commit hash.
- For any dry/real run: row count, verdict breakdown, sent count, any
  rate-limit / captcha / exception encountered.
- Any user decision that needs to be unblocked before the next step.

## File-by-file map (where to look when something breaks)

| File | Role |
|---|---|
| `ROADMAP.md` | Authoritative project state + spec-v2 classifier lock |
| `main.py` | Orchestrator. Pipeline: search → is_hot → profile → classify → connect |
| `searcher.py` | LinkedIn people-search + JS card extraction (class-agnostic) |
| `verifier.py` | Two-scorer name matching + 3-tier confidence + post-scrape medical-signal |
| `profile_profiler.py` | Scrapes followers / creator_mode / bio / activity. v2 bio keywords: 8 total |
| `influencer_classifier.py` | **v2** hard filters 500/2/90d/5; engagement_rate; soft threshold 4; Ollama edge call |
| `connector.py` | Top-card anchor + More-menu fallback; `SEND_WITHOUT_NOTE_BUTTON` flow |
| `sheets_logger.py` | 3 sheet tabs + classifications.csv (v2, 16 cols) + processing_status.csv |
| `li_selectors.py` | Semantic-only selector catalogue |
| `_visit_tracker.py` | 48h cool-down, JSON-backed, URL-canonicalised |
| `_more_menu_probe.py` | Standalone probe for step-6 prereq. Rerun on new seeds before step 9 |
| `step4d_audit.py` | Seed-rotating search→verify→profile harness. Good regression check |
| `step5_classifier_test.py` | Hand-label harness (v2 fixtures, 10 rows) |
| `config.py` | Paths + targeting filters + classifier thresholds + rate limits |

## When you are handed a new directive

- If it changes classifier logic, update **both** the code (usually
  `influencer_classifier.py` + maybe `profile_profiler.py`) **and**
  ROADMAP.md §"Influencer classifier spec" in the same commit chain.
- If it changes the CSV schema, update `sheets_logger.CLASSIFICATIONS_HEADERS`
  + `_append_classifications_csv` + `main._error_classification` together.
  The auto-migration in `_ensure_classifications_csv` handles the on-disk
  rename to `.v1.bak`.
- If it changes the connect flow, re-run `_more_menu_probe.py` on a fresh
  non-HOT seed BEFORE touching `connector.py` — live DOM confirmation is
  the user's standing requirement, not a suggestion.
- Also update THIS file (`AGENT.md`) so the next agent picks up accurate
  state.
