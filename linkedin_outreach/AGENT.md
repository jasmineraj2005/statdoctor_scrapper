# LinkedIn Agent Prompt

You are the LinkedIn outreach agent for the ARPHA statdoctor scraper.
Working directory: `/Users/jasminebaldevraj/Desktop/ARPHA/statdoctor_scrapper/linkedin_outreach`

**READ `ROADMAP.md` IN FULL before doing anything.** Then skim recent
`git log --oneline` for the last 3–5 commits so you know which step
completed most recently.

## Current state (2026-04-26 late evening)

Day-4 scale-out underway under classifier v2.1.2. v2.1.1 over-corrected
(batch #3 yielded 1/120 vs v2.1's 7/200). v2.1.2 closes the recall gap
without re-introducing FPs.

### Day-4 batches under v2.1.2
- **Batch #1** — 2 clean influencers, 2/2 auto-connected: Dr David Pilcher
  (Senior ICU Specialist – Alfred Health), Dr Dinusha Katugampalage
  (Consultant Psychiatrist @ Forensicare).
- **Batch #2** — 2 clean influencers, 2/2 auto-connected: Dr Eugene Ek
  (Orthopaedic Surgeon, Monash), Dr Edwina Wright AM (Professor @ Alfred
  Health) — Wright was the **first v2.1.2 sub-threshold rescue in the wild**
  (sort=89, set=100, Δtok=1).
- **Batch #3** — 4 clean influencers, **2/4 auto-connected**, 2 errored:
  - ✅ SENT: Dr George Koufogiannis, Dr Fiona Mary Russell
  - ❌ ERRORED: Dr Evrard Ottmar Harris, Dr Ganesh Naidoo — **NEW failure
    mode**: Connect button click worked, modal opened, but Send button
    inside the modal not found. Likely LinkedIn DOM shift on the invite
    modal (different from the prior Connect-button issue fixed in
    connector v2). See `## Pending decision` below.

Day-4 totals: **6 auto-connects sent**, 2 manual-pending (Harris, Naidoo).
~2,789 rows still in queue (1,199 already terminal).

## Pending decision (PICK UP HERE on next session)

**Question:** how to handle the 2 batch-#3 modal-Send failures (Harris,
Naidoo) and prevent recurrence in batch #4?

### Option 1 — manual-first
1. User opens the 2 URLs, clicks Connect → Send manually (30 sec total).
2. Run `_append_manual_connects.py` to backfill the Connections Sent tab
   with the 2 manual sends.
3. Then debug the modal selector before launching batch #4.

URLs to send manually:
- https://www.linkedin.com/in/dr-evrard-harris-980930218
- https://www.linkedin.com/in/ganeshnaidoo

### Option 2 — fix-first
1. Build a `_modal_send_probe.py` that opens a Connect modal on a fresh
   non-HOT seed and dumps every button/role/aria-label inside the
   `[role="dialog"]` overlay.
2. Patch `li_selectors.py` SEND_WITHOUT_NOTE_BUTTON_FMT (and any related
   modal selectors) based on the probe output.
3. Live-test with `_connector_fix_test.py` shape against Harris + Naidoo
   (both still HOT — wait for cool-down OR force via reprofile_approved.py).
4. Then launch batch #4 with confidence the fix holds.

**Agent recommendation:** Option 1 first (don't lose Harris/Naidoo to
debug churn), then Option 2 before batch #4.

### Other open items
- Watchdog (low priority, deferred — recurring silent-hang failure mode).
- Batch #4 ready when modal bug is resolved. Same params:
  `--limit 120 --connect-cap 80`. Will walk G-H names next.

Most recent commits (LOCAL ONLY — do not push without user approval):

Most recent commits:
- `bec4ea1` linkedin_outreach: prevent 985-row gap on new sheet tabs
- `d323b12` linkedin_outreach: AGENT — v2.1.2 + Day-4 batch #1 results
- `9fc5d5c` **classifier v2.1.2** — recover real doctors lost to v2.1.1
            over-correction (hospital tokens in headline_is_medical,
            sub-threshold name-rescue band [80,85), STRONG_MEDICAL +=
            allied health + clinical-role keywords)
- `7e1fe23` ROADMAP + AGENT — Day 2/3 outcomes + v2.1.1 + connector v2
- `aaff844` keep audit + diagnostic scripts (_audit_influencers.py, etc.)
- `92622c8` **classifier v2.1.1** — Fixes A/B/C in one commit, locked together
- `c980f8e` add Connections Sent sheet tab
- `d8c97d4` fix already_connected false-positive + sheet relabel
- `6b1d588` connector v2 — title-prefix strip, multi-match iteration,
            More-menu text fallback, :visible filter on duplicate anchors
- `ed1bac0` classifier v2.1 (300/1/drop avg_likes) + reprofile_approved.py

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

### Known intermittent issue: mid-run hangs
3 hangs observed across the campaign. Process stays alive but log goes
silent for hours; CPU near zero. Root cause unknown (likely page.evaluate
or page.click without explicit timeout). Workaround: kill + restart;
terminal-stage dedup picks up. **TODO:** add per-row signal-based
watchdog (~3 min cap) to main.py orchestrator.

### Connections Sent tab (active, `c980f8e`)
Dedicated client-facing tab in Google Sheets with one row per actual
connect-sent. Auto-populated from `update_connect_status` when status ==
STATUS_SENT. Backfilled with 14 manual sends via
`_append_manual_connects.py` (idempotent dedup).

## YOUR NEXT STEPS IN ORDER

1. **STEP 10 — Day-4 scale-out** continues under v2.1.2.
   `main.py --limit 120 --connect-cap 80` per batch (≈2hr each, user spec).
   Day-4 batch #1 yielded 2/120; batch #2+ in progress as of 2026-04-26
   evening. ~3,200 rows remaining in subset. Always show user the
   influencer URL list when batch finishes — they spot-check before we
   ramp further. Two fully-clean v2.1.2 connects so far is a small sample;
   keep watching for FP regressions for the next ~10 sends.

2. **Manual FP withdrawal.** 5 sent FPs from Day-2/Day-3 to be withdrawn
   on LinkedIn (user does this): McCormack, Stewart, Rizkallah, Andrew
   White, Andrew Carter. Bradley Smith was originally flagged but user
   confirmed he's real (career-pivot psychiatrist→physiotherapist).

3. **Watchdog (low priority).** Per-row signal-based timeout in main.py
   to prevent the recurring hang from costing wall-time. Add when
   convenient.

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
