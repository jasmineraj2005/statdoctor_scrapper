# LinkedIn Agent Prompt

You are the LinkedIn outreach agent for the ARPHA statdoctor scraper.
Working directory: `/Users/jasminebaldevraj/Desktop/ARPHA/statdoctor_scrapper/linkedin_outreach`

**READ `ROADMAP.md` IN FULL before doing anything.** Then skim recent
`git log --oneline` for the last 3–5 commits so you know which step
completed most recently.

## Current state (2026-04-26)

Step 9 Day-3 complete. Connector v2 + Classifier v2.1.1 landed after a
hard lesson: Day-3 fired 3 fully-automated connects to non-doctors
(Project Manager, PA, Senior Lecturer) — perfect-name look-alikes that the
v2.1 verifier accepted because (a) the medical-signal gate only ran for
medium-confidence matches, (b) the keyword list contained dual-use words
("doctor", "consultant", "specialist", "md"), and (c) "university of
melbourne" was in the hospital token list. Audit found 7 of 18 existing
influencers were FPs (6 sent — flagged in sheet, 1 caught by user).

Most recent commits (LOCAL ONLY — do not push without user approval):
- `aaff844` keep audit + diagnostic scripts (_audit_influencers.py, etc.)
- `92622c8` **classifier v2.1.1** — Fixes A/B/C in one commit, locked together
- `c980f8e` add Connections Sent sheet tab
- `d8c97d4` fix already_connected false-positive + sheet relabel
- `6b1d588` connector v2 — title-prefix strip, multi-match iteration,
            More-menu text fallback, :visible filter on duplicate anchors
- `ed1bac0` classifier v2.1 (300/1/drop avg_likes) + reprofile_approved.py

### Days 1–3 recap
- **Day-1 (2026-04-24):** 201 rows under v2.0 → 0 influencers (over-gating).
- **Day-2 (2026-04-25):** 200 rows under v2.1 → 7 influencers, 0 connects
  (connector bugs). User connected manually to all 7 — 6 real, 1 FP
  caught by user (Belinda Zhou).
- **Day-3 (2026-04-25):** Aggressive run under v2.1+connector v2 → 3
  fully-automated connects fired, **all 3 turned out to be non-doctors**:
  Christopher McCormack (Project Manager), Claire Stewart (PA), Christine
  Rizkallah (Senior Lecturer). Triggered the v2.1.1 audit + fix.

### Classifier v2.1.1 (active, `92622c8`)
Hard filters unchanged from v2.1 (followers≥300, posts≥1, last_post≤90d).
Three coupled fixes (single commit):
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

Audit script `_audit_influencers.py` re-checks classifications.csv against
v2.1.1; `_drive_test_missed.py` finds rejections that v2.1.1 would now
rescue.

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

1. **STEP 10 — Day-4 scale-out** under v2.1.1.
   `main.py --limit 120 --connect-cap 80` (≈2hr per batch per user spec).
   Expected ~3-5 influencers per 120 rows (lower than v2.1 yield because
   of tighter gate). Connects fire automatically; only STRONG keyword
   matches qualify so precision should be much higher. Until v2.1.1 has
   logged ~30 successful real connects, surface the influencer URL list
   to the user for sanity-check before relying on auto-send.

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
| `influencer_classifier.py` | **v2.1.1** hard filters 300/1/90d; engagement_rate soft-only; soft threshold 4 (normal) / 5 (medium); Ollama edge call; medium AND high both run medical-signal post-scrape gate |
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
