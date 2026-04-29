# LinkedIn Agent Prompt

You are the LinkedIn outreach agent for the ARPHA statdoctor scraper.
Working directory: `/Users/jasminebaldevraj/Desktop/ARPHA/statdoctor_scrapper/linkedin_outreach`

**READ `ROADMAP.md` IN FULL before doing anything.** Then skim recent
`git log --oneline` for the last 3–5 commits so you know which step
completed most recently.

## Current state (2026-04-29 evening)

Day-4 wrapped. Classifier v2.1.2 still active. **Two architectural fixes
landed today** that change how the next batches will behave:

1. **Per-row watchdog** in `main.py` (commit `9076ab2`) — SIGALRM-bounded
   3-min cap per row. Batch #4 hit 3 mid-run hangs in one session before
   this; restarts are no longer needed for this failure mode.
2. **Verifier rank-by-medical-signal** in `verifier.py` + `searcher.py`
   (commit `8d27f7b`) — searcher now collects ALL top-5 same-name matches
   and picks the one whose headline carries medical signal, instead of
   taking Result #1. Closes the wrong-person-namesake failure mode that
   produced 0/120 yield in batch #4 even though the queue contained real
   doctors (Belinda Drew, Helen Hsu, Helen Smith — all matched as their
   non-medical namesakes and post-scrape-rejected).

### Day-4 batches under v2.1.2
- **Batch #1** — 2 clean influencers, 2/2 auto-connected: Dr David Pilcher
  (Senior ICU Specialist – Alfred Health), Dr Dinusha Katugampalage
  (Consultant Psychiatrist @ Forensicare).
- **Batch #2** — 2 clean influencers, 2/2 auto-connected: Dr Eugene Ek
  (Orthopaedic Surgeon, Monash), Dr Edwina Wright AM (Professor @ Alfred
  Health). Wright was the first v2.1.2 sub-threshold rescue in the wild.
- **Batch #3** — 4 clean influencers, 2/4 auto-connected, 2 errored on
  the modal-Send selector miss (Harris, Naidoo). User confirmed both are
  1st-degree on follow-up; backfilled into Connections Sent tab via
  `_append_manual_connects.py` (commit `33fcb5e`).
- **Batch #4 (2026-04-28 → 29)** — 5 attempts because of hangs.
  Combined coverage: ~120 doctors classified across H–J letters.
  - 1 NEW influencer found (Dr Graham Leslie Barrett, soft=7, 2059
    followers, https://www.linkedin.com/in/grahambarrettaus) but he was
    HOT-locked when phase 2 ran on the final attempt and never received a
    connect attempt.
  - Final session: 0 sends, 0 candidates remaining in pending.
  - **Audit revealed** 16 high-conf rejections were namesake mis-matches.
    Drove the verifier rank-by-signal fix.

Day-4 totals: **6 auto-connects sent** (all from batches 1-3),
2 manual backfills (Harris, Naidoo). Batch #4 net: 0 sends.

## Pending decisions (PICK UP HERE on next session)

### 1. Three HOT-locked candidates need force re-attempt (READY TO RUN)

The Day-4 batch #4 hangs marked these as visited but never classified or
connected. Forty-eight-hour cool-down expires roughly 2026-04-30 evening
(natural retry) OR fire `reprofile_approved.py`-style harness now (user-
authorised bypass).

| Name | URL | Notes |
|---|---|---|
| Hany Georgeos | https://www.linkedin.com/in/dr-hany-georgeos | Skin Cancer College Mentor & Fellow, high-conf in batch #4 search. Looks legit. |
| Graham Leslie Barrett | https://www.linkedin.com/in/grahambarrettaus | Already classified influencer (soft=7) in run #2; just needs the connect step. |
| Glenn Richard Hocking | https://www.linkedin.com/in/glen-hocking-90226a346 | Anatomical Pathology, high-conf, never profiled. |

Practitioner IDs and locations captured in repo task list. Fresh `_reprofile_hot_locked.py` to be created mirroring `reprofile_approved.py`.

### 2. Modal-Send selector miss — STILL OPEN

Batch #3 (Harris, Naidoo) hit "Connect modal opened but no Send button
found." User-side both turned out to be 1st-degree (suggests the modal
auto-fired or they were connected separately). Probe `_modal_send_probe.py`
exists (commit `e4a936e`) but first run dumped a "Modal Window" with 0
items — script needs longer wait + full innerHTML dump before re-running
on a fresh seed.

Risk for next batch: ~25% of auto-sends might error at this step. We can
backfill manually as we did for Harris+Naidoo, but a real fix would let
the script finish autonomously.

### 3. Day-5 batch — verifier rank-by-signal validation needed

The Day-4 yield (0/120 net sends, 1/120 NEW influencer found before HOT
loss) suggests the wrong-person matches were sinking real doctors. The
new ranking should fix it. Run a fresh `--limit 60 --connect-cap 40`
batch as the validation. Watch for:
- "Selected Result #N over X other candidate(s)" log lines — confirms
  the ranking is choosing among multiple matches
- Yield should jump to ~3-5% (matching batches 1-3) or higher

Most recent commits (LOCAL ONLY — do not push without user approval):
- `8d27f7b` linkedin_outreach: verifier+searcher — rank same-name matches by medical signal
- `9076ab2` linkedin_outreach: per-row watchdog in main.py phase-1 loop
- `a4e745f` linkedin_outreach: extend .gitignore for auto-generated artefacts
- `e4a936e` linkedin_outreach: add `_modal_send_probe.py` for invite-modal DOM diagnosis
- `33fcb5e` linkedin_outreach: backfill Harris + Naidoo manual sends
- `5db8024` linkedin_outreach: AGENT — Day-4 batches #2/#3 + modal-Send open question
- `bec4ea1` linkedin_outreach: prevent 985-row gap on new sheet tabs
- `d323b12` linkedin_outreach: AGENT — v2.1.2 + Day-4 batch #1 results
- `9fc5d5c` **classifier v2.1.2** — recover real doctors lost to v2.1.1
- `92622c8` **classifier v2.1.1** — Fixes A/B/C in one commit, locked together
- `6b1d588` connector v2 — title-prefix strip, multi-match iteration, More-menu text fallback
- `ed1bac0` classifier v2.1 (300/1/drop avg_likes) + reprofile_approved.py

### Verifier rank-by-medical-signal (active, `8d27f7b`)
`searcher.search_and_find_profile` no longer returns the first match.
It now iterates ALL `cards[:MAX_PROFILES_TO_CHECK]` (capped at 5),
collects every is_match=True via `verifier.verify_profile_with_signal`,
and ranks them:
- signal_strength=2 if `headline_is_medical` AND
  `headline_matches_speciality(AHPRA speciality, headline)`
- signal_strength=1 if `headline_is_medical` only
- signal_strength=0 otherwise
- Sort key: `(-signal_strength, +linkedin_rank)` — higher signal wins,
  LinkedIn order breaks ties.
- If no candidate has signal, picks rank 1 (preserves prior behaviour
  for sparse-headline doctors).

`verifier.verify_profile` itself is unchanged; the new logic lives in a
wrapper `verifier.verify_profile_with_signal`. Rolling back is a one-line
change in searcher.py.

### Per-row watchdog (active, `9076ab2`)
`main.py` installs `signal.SIGALRM` at the top of phase 1 and calls
`signal.alarm(WATCHDOG_PER_ROW_SEC=180)` before each row, cancelling
after `_profile_and_classify` returns. On `RowTimeoutError`:
- log the row id+name
- `logger.set_stage(pr, STAGE_SKIPPED, detail="watchdog: per-row timeout")`
- attempt `page.goto("https://www.linkedin.com/feed/")` recovery
- continue to next row
- 3 consecutive timeouts → break out of phase 1 (page presumed dead)

Note: SIGALRM is Unix-only (macOS fine). Doesn't interrupt deeply C-level
blocking, but Playwright's IPC polling typically surfaces it within
seconds.

### Days 1–4 recap
- **Day-1 (2026-04-24):** 201 rows under v2.0 → 0 influencers (over-gating).
- **Day-2 (2026-04-25):** 200 rows under v2.1 → 7 influencers, 0 connects
  (connector bugs). User connected manually to all 7 — 6 real, 1 FP
  caught by user (Belinda Zhou).
- **Day-3 (2026-04-25):** Aggressive run under v2.1+connector v2 → 3
  fully-automated connects fired, **all 3 turned out to be non-doctors**:
  Christopher McCormack (Project Manager), Claire Stewart (PA), Christine
  Rizkallah (Senior Lecturer). Triggered the v2.1.1 audit + fix.
- **Day-3 batch #3 (2026-04-26 morning, v2.1.1):** 120 rows → 1 influencer
  (Dr David Fineberg, GP/ME Research Clinic). Yield 0.8% — too low,
  triggered v2.1.2 fixes.
- **Day-4 batch #1 (2026-04-26 afternoon, v2.1.2):** 120 rows → 2
  influencers, 2 auto-connects (Dr Pilcher Alfred Health ICU,
  Dr Katugampalage Consultant Psychiatrist Forensicare). Both clean.
  Yield 1.7%.
- **Day-4 batch #2 (2026-04-26 evening):** 2/2 auto-connects (Dr Eugene
  Ek, Dr Edwina Wright AM). Yield 1.7%.
- **Day-4 batch #3 (2026-04-26 evening):** 4 influencers / 120, 2/4
  auto-connects (Koufogiannis, Russell). Harris + Naidoo errored on the
  modal-Send selector miss; both later confirmed 1st-degree by user;
  manual-backfilled to Connections Sent.
- **Day-4 batch #4 (2026-04-28 → 29, 5 attempts due to hangs):** 1 NEW
  influencer found (Graham Barrett, soft=7) but HOT-locked by a hang
  before phase 2 could connect. **Net: 0 sends, 0 phase-2 candidates.**
  Audit of the queue's high-confidence rejections revealed 16 wrong-person
  namesake matches (Belinda Drew→QLD govt, Helen Hsu→Whitehorse Council,
  etc.), motivating the verifier rank-by-medical-signal fix shipped same
  day (commit `8d27f7b`).

### Classifier v2.1.2 (active, `9fc5d5c`)
Hard filters unchanged (followers≥300, posts≥1, last_post≤90d). Three
v2.1.1 fixes (A/B/C, commit `92622c8`) plus three v2.1.2 recovery fixes
(commit `9fc5d5c`):

**v2.1.1 base (still active):**
- **Fix A** — verifier near-name rescue: Δtok=2 OR sort∈[85,95) AND on-card
  medical signal (Dr/Prof prefix, STRONG_MEDICAL keyword, or speciality
  keyword) → promote to medium instead of name-reject.
- **Fix B** — universal medical-signal post-scrape gate: medium AND high
  confidence both run `medical_signal_in_text`. fail_reason =
  `{tier}_no_medical_signal`.
- **Fix C** — STRONG vs WEAK keyword split in config.py:
  `STRONG_MEDICAL_KEYWORDS` only (physician, surgeon, mbbs, fracp,
  anaesthetist, oncologist, etc.); WEAK kept for documentation but no
  longer used by gate. `VIC_HOSPITAL_TOKENS` lost broad universities,
  gained specific medical sub-units.

**v2.1.2 additions (recover real-doctor recall):**
- `headline_is_medical` ALSO checks VIC_HOSPITAL_TOKENS (was only the
  deeper `medical_signal_in_text`). Fixes "VMO at Northern Health" /
  "Intensivist at Alfred Health" rescue at the verifier stage.
- New sub-threshold name rescue band [80, 85) for cards with on-card
  medical signal — both sort and set floors relaxed to 80. Caps at
  "medium". Fixes nickname cases ("Danny" vs "Daniel" sort=84).
- STRONG_MEDICAL_KEYWORDS expanded with allied-health (physiotherapist,
  TCM practitioner, psychologist, midwife, nurse practitioner, dentist,
  pharmacist, etc.) and clinical roles (VMO, general surgery, medical
  officer, consultant physician, staff specialist, fellow).

Audit script `_audit_influencers.py` re-checks classifications.csv against
current rules; `_drive_test_missed.py` finds rejections that the new
rescue path would recover.

### Connector v2 (active, `6b1d588`)
- Strips "Dr"/"Prof"/"A/Prof" from owner_name before formatting CONNECT_BUTTON_FMT
- Iterates over duplicate visible Connect anchors (top-card + sticky bar)
  with scroll_into_view + short-timeout click
- More-menu Connect: text-based locator fallback for empty-aria
  `<a role="menuitem">Connect</a>` shape
- :visible filter on CONNECT_BUTTON_FMT/FOLLOW_BUTTON_FMT

### already_connected detection fix (`d8c97d4`)
Old `_get_relationship_label()` scanned `page.content()` for "message" —
matched left-nav "Messaging" link on every profile (false-positive). Now
uses scoped `main button[aria-label^="Pending|Message|Following "]`.
STATUS_ALREADY_CONNECTED now logs as `already_connected/skipped`.

### Mid-run hangs — MITIGATED via watchdog (2026-04-29)
Across the campaign, 6+ hangs observed total (3 in batch #4 alone).
Process stays alive but log goes silent for hours; CPU near zero. Root
cause unknown (likely `page.evaluate` or `page.click` without explicit
timeout). **Watchdog now active** (commit `9076ab2`, see "Per-row watchdog"
section above). 3-min cap per row + best-effort page recovery. Run #5 of
batch #4 was the first run to walk all 120 rows without restart.

### Connections Sent tab (active, `c980f8e`)
Dedicated client-facing tab in Google Sheets with one row per actual
connect-sent. Auto-populated from `update_connect_status` when status ==
STATUS_SENT. Backfilled with 14 manual sends via
`_append_manual_connects.py` (idempotent dedup).

## YOUR NEXT STEPS IN ORDER

1. **STEP 11 — Force-reprofile the 3 HOT-locked candidates.** Mirror
   `reprofile_approved.py` shape; insert Hany Georgeos
   (MED0001219184), Graham Leslie Barrett (MED0001122716), Glenn Richard
   Hocking (MED0001139840). User-authorised bypass of `is_hot`. Real
   connect attempts. Quick (~2 min). Commit `_reprofile_hot_locked.py`
   afterwards.

2. **STEP 12 — Day-5 batch with verifier rank-by-signal validation.**
   `main.py --limit 60 --connect-cap 40` (smaller than 120 to validate
   the new ranking before we go big). Watch logs for "Selected Result
   #N over X other candidate(s)" lines — confirms ranking is biting.
   Yield should be ≥3% if the wrong-person-namesake fix works as
   intended; the H-J alphabetical stretch in batch #4 had 1/120
   (only Graham Barrett) under the old ranking.

3. **STEP 13 — Modal-Send selector fix (still pending).** Improve
   `_modal_send_probe.py` (longer settle wait, dump full innerHTML),
   re-run on a fresh seed, patch `li_selectors.SEND_WITHOUT_NOTE_BUTTON`,
   live-test. Without this, ~25% of auto-sends will need manual
   backfill after each batch.

4. **Manual FP withdrawal (carry-over from prior days).** 5 sent FPs from
   Day-2/Day-3 to be withdrawn on LinkedIn (user does this): McCormack,
   Stewart, Rizkallah, Andrew White, Andrew Carter. Bradley Smith was
   originally flagged but user confirmed he's real (career-pivot
   psychiatrist→physiotherapist).

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
| `influencer_classifier.py` | **v2.1.2** hard filters 300/1/90d; engagement_rate soft-only; soft threshold 4 (normal) / 5 (medium); Ollama edge call; medium AND high both run medical-signal post-scrape gate; verifier rescues near-name (Δtok=2 or sort∈[80,95)) with medical signal |
| `reprofile_approved.py` | One-off: re-profile+classify+connect specific URLs, bypasses is_hot. User-authorised only |
| `_audit_influencers.py` | Re-classify existing influencers under current rules; flag FPs already sent. Run after each classifier change |
| `_drive_test_missed.py` | Find name-rejected real doctors that the new rescue path would recover |
| `_append_manual_connects.py` | Backfill Connections Sent tab with manually-fired connects (idempotent dedup) |
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
