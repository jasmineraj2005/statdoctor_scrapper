# LinkedIn Agent Prompt

You are the LinkedIn outreach agent for the ARPHA statdoctor scraper.
Working directory: `/Users/jasminebaldevraj/Desktop/ARPHA/statdoctor_scrapper/linkedin_outreach`

**READ `ROADMAP.md` IN FULL before doing anything.** Then skim recent
`git log --oneline` for the last 3–5 commits so you know which step
completed most recently.

## Current state (2026-04-24)

Steps 1 – 9 Day 1 complete. Classifier v2.1 landed. Day 1 real run
produced 0 new connects (v2.0 over-gated); v2.1 re-score surfaced exactly
the 4 profiles the user approved from the near-miss list. Most recent
commits:
- `d8c97d4` fix already_connected false-positive + sheet relabel
- `ed1bac0` classifier v2.1 (300/1/drop avg_likes) + reprofile_approved.py
- `6bbce13` spec v2 — step5 harness fixtures + ROADMAP update
- `dd65859` spec v2 — niche engagement classifier + engagement_rate column
- `1156c03` step-6b fix — session_limit→send_cap + profile progress print

All commits from `826322a` onward are LOCAL ONLY — **do not push without
user approval**.

### Day-1 real run (2026-04-24) results
`main.py --limit 200 --connect-cap 10` walked 201 rows. 0 influencers, 0
connects. Fail breakdown (under v2.0): 74% followers<500, 15%
post_count_90d<2, ~2% avg_likes<5. User reviewed the 28 non-influencer
rows with ≥300 followers; approved 4 (Fakhouri 7.8k fol, Alan Paul 2.6k,
Alice Bergin 448, Amanda Osborne 379).

### Classifier v2.1 (active)
- `HF_FOLLOWERS_MIN = 300` (v2.0: 500)
- `HF_POSTS_90D_MIN = 1` (v2.0: 2)
- `HF_LAST_POST_DAYS = 90` (unchanged)
- `HF_AVG_LIKES_MIN` **dropped** — profiler's activity-feed scrape doesn't
  capture like counts reliably. Engagement remains a soft-score signal
  (+3 if ≥2%, +2 if ≥1%) but not a hard gate.
- Soft threshold unchanged: normal=4, medium=5. Ollama band: normal=2-3, medium=1-4.

### reprofile_approved.py run (2026-04-24)
Bypassed is_hot for the 4 user-approved profiles. 3/4 passed v2.1
heuristic (Fakhouri soft=8, Paul soft=6, Bergin soft=6); Amanda Osborne
landed in Ollama band (soft=3) and was overruled. Connect attempt found
all 4 were already 1st-degree connections — user had manually connected
between the Day-1 scrape and today. Net new connects: 0.

### already_connected detection fix (`d8c97d4`)
Old `_get_relationship_label()` scanned `page.content()` for "message" —
which matches the left-nav "Messaging" link on every profile, producing
false-positive already_connected on any connector-failure path. Replaced
with scoped `main button[aria-label^="Pending|Message|Following "]`
locator checks. Also fixed sheet relabel: STATUS_ALREADY_CONNECTED now
logs as `already_connected/skipped` instead of `connect_failed/fail`.

## YOUR NEXT STEPS IN ORDER

1. **STEP 9 Day 2 — v2.1 staged run.** `main.py --limit 200 --connect-cap 25`.
   Day-1's 201 rows auto-skip via terminal-stage dedup; queue pulls fresh
   subset rows. Expected yield ~4 influencers / 200 (2% rate) based on
   Day-1 re-score.
   - Monitor Ollama calls: v2.1 lowers the hard-pass bar so more rows
     reach the soft score; medium-conf rows that pass hard + soft 1-4
     hit the Ollama edge band.
   - Monitor the first real Follow-primary connect attempt —
     `MORE_MENU_CONNECT_FMT` was validated on Dawid Naude at probe time,
     but still untested in a live run (Day-1 hit 0 influencers so no
     Follow-primary connect fired).

2. **STEP 10 — Scale out** after Day 2 validates. Remaining ~3,600 subset
   rows processed in batches respecting daily cap (40 once proven; 25 for
   now), 30k lifetime cap, 48h per-profile cool-down.

## Upstream changes you should know about (2026-04-25)

The email_enrichment agent has been working on the pipeline today. Two changes
may affect how you read `vic_practitioners_enriched.csv`:

- **`email_confidence=n_a` is gone.** Previously, `pipeline=linkedin` rows had
  `email_source=n_a` + `email_confidence=n_a`. Now LinkedIn-pipeline rows also
  get email synthesis, so they'll have a real `email_source` (usually
  `hospital_postcode` or `gp_unresolved`) and a Disify verdict. The `pipeline`
  column still authoritatively tells you which channel is primary — use that
  for your gating, not email_source.
- **GPs now flagged `email_source=gp_unresolved` instead of being given hospital
  emails.** If you were using candidate_email presence as a reachability
  signal for anything, note that GPs on the LinkedIn pipeline may have empty
  candidate_email (gp_unresolved) even though a clinic phone/address is
  available in `email_enrichment/data/gp_practices.csv`. Don't read that file
  yourself — if LinkedIn needs clinic info for a row, request a schema update
  to `vic_practitioners_enriched.csv` via the user.

See `email_enrichment/AGENT.md` for full details. Do not edit anything in that
directory.

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
| `influencer_classifier.py` | **v2.1** hard filters 300/1/90d; engagement_rate soft-only; soft threshold 4 (normal) / 5 (medium); Ollama edge call |
| `reprofile_approved.py` | One-off: re-profile+classify+connect specific URLs, bypasses is_hot. User-authorised only |
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
