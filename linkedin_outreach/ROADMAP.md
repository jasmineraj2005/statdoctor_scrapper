# linkedin_outreach — Roadmap & Handoff

For a new agent picking this up cold. Status as of 2026-04-21.

## Scope (locked — do not re-open)

- **Task 1 of a two-task parallel build.** Task 2 is email enrichment in
  `email_enrichment/` — **do not touch** anything outside `linkedin_outreach/`.
- VIC only. Plain connect, **no note**. Daily connect cap 20–25.
- High-yield subset (~4k rows, not 8–10k): `Specialist` registration in top-50
  VIC postcodes, excluding `Non Practising` / `Limited` / `Provisional`.
- Free-tier only: **Ollama `llama3.2:3b` locally**, no Claude/OpenAI/SerpAPI.
- Working estimate: ~40% verifier match rate across the full subset
  (~1,500 of 3,988 reachable).

## File I/O contract (email terminal depends on these paths)

1. **`data/vic_high_yield_subset.csv`** — shipped (commit `4b2aa2f`, pushed).
   Columns: `practitioner_id, name, speciality, postcode_searched, location`
2. **`data/vic_linkedin_classifications.csv`** — v2 schema (active).
   Columns: `practitioner_id, linkedin_url, classification
   (influencer|non_influencer|not_found|error), soft_score,
   hard_filters_passed, follower_count, post_count_90d, last_post_date,
   has_video_90d, creator_mode, bio_signals (pipe-delimited),
   engagement_rate, classifier_source (heuristic|ollama),
   classifier_confidence, classified_at, fail_reason`
   v1 backup at `vic_linkedin_classifications.csv.v1.bak`.

## Progress by step

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | Secret not leaked (`gsheet_creds.json` in gitignored dir) | ✅ done | — |
| 2 | VIC high-yield subset builder (`build_subset.py`) + CSV | ✅ **pushed** | `4b2aa2f` |
| 3 | Selectors rewritten for 2026 LinkedIn DOM | ✅ **pushed** | `385acca`, `c0dbc24` |
| 4a | `profile_profiler.py` — followers/creator-mode/posts/bio | ✅ local | `826322a` |
| 4b-v1 | Location-match loosen | ✅ local | `0c0e663` |
| 4b-v2 | Empty-loc accept when name_score≥95 | ✅ local | `39155a3` |
| 4b-v3 | Two-scorer (token_sort + token_set), 3-tier confidence | ✅ local | `dca0179` |
| 4b-v4 | Deferred medical check + VIC hospital registry | ✅ local | `acd955d` |
| 4c | Re-audit seed=42 | ✅ done | — |
| 4d | Profiler dry-test on 5 new matches | ✅ done | — |
| 4e | Medical-signal headline bypasses `no_degree_badge` | ✅ local | `1c53533` |
| 5a | `profile_profiler` emits `post_previews_90d` | ✅ local | `65fb9d8` |
| 5b | `influencer_classifier.py` v1 | ✅ local | `fe03ec3` |
| 5c | Hand-label harness v1 — 10/10 agreement | ✅ local | `67cf172` |
| 5d | **Classifier v2** — engagement_rate, relaxed thresholds, Ollama v2 | ✅ local | `dd65859` |
| 5e | Harness v2 + ROADMAP — 10/10 offline, 9/10 Ollama live | ✅ local | `6bbce13` |
| 6 | `connector.py` rewrite — semantic selectors, no-note, influencer gate | ✅ local | — |
| 7a | `sheets_logger.py` — Influencers VIC + Reviewed Skipped + Processing Status | ✅ local | — |
| 7b | **Live Run Log + Summary tabs** — real-time per-event sheet writes for client | ✅ local | — |
| 8 | 50-row dry-run — 0 connects fired, schema migration clean | ✅ done | — |
| 9 Day 1 | Staged real run — 203 rows, 0 connects (0 influencers under v2.0) | ✅ done | — |
| 9.5 | **Classifier v2.1** — followers≥300, posts≥1, dropped avg_likes filter | ✅ local | `ed1bac0` |
| 9.5b | Re-profile 4 user-approved candidates — all 4 already 1st-degree | ✅ done | `ed1bac0` |
| 9.5c | Fix already_connected false-positive + sheet relabel | ✅ local | `d8c97d4` |
| 9 Day 2 | Staged real run — 200 rows, 7 influencers, 0 connects (connector bugs) | ✅ done | — |
| 9.6 | Connector v2 — title-prefix strip, multi-match iteration, More-menu text fallback, :visible | ✅ local | `6b1d588` |
| 9.7 | Add Connections Sent tab + backfill 11 manual sends | ✅ local | `c980f8e` |
| 9 Day 3 | Aggressive run — 3 connects fired automatically, all 3 turned out to be FPs | ✅ done | — |
| 9.8 | **Classifier v2.1.1** — Fix A/B/C: medical signal required for ALL tiers, near-name rescue, STRONG vs WEAK keyword split, drop broad institution tokens | ✅ local | `92622c8` |
| 9.8b | Audit existing 18 influencers — 7 FPs found, 6 sent, flagged in sheet (1 later confirmed real) | ✅ local | `aaff844` |
| 10 | Day 4+ — scale-out under v2.1.1 (~3,500 rows remaining, ~120/2hr batches) | ⏳ next | — |

## Classifier v2.1.1 (locked — replaces v2.1, 2026-04-25)

Day-3 v2.1 fired 3 fully-automated connects to **non-doctors** (Christopher
McCormack/Project Manager, Claire Stewart/PA, Christine Rizkallah/Senior
Lecturer) because the verifier accepted perfect-name look-alikes when the
real doctor was rejected on token strictness. Audit found 7 of the 18
existing influencer rows were FPs. Three coupled fixes (single commit
`92622c8`):

**Fix A — verifier near-name rescue.** Δtok=2 OR sort∈[85,95) AND
on-card medical signal (Dr/Prof prefix, MEDICAL_KEYWORD in headline, or
SPECIALITY_KEYWORD match) → promote to `medium` instead of name-reject.
Recovers real doctors with nicknames (Christine "Tina" Rizkallah pattern).

**Fix B — universal medical-signal post-scrape gate.** Both `medium` and
`high` confidence rows now run `medical_signal_in_text` against
headline+bio+experience after profiling. fail_reason = `{tier}_no_medical_signal`.
Closes the high-conf bypass that let the 3 Day-3 FPs through.

**Fix C — STRONG vs WEAK keyword split.** Old MEDICAL_KEYWORDS contained
"doctor" (matches PhD/research), "consultant" (business consultant),
"specialist" (marketing specialist), "md" (Managing Director), "gp" (General
Partner). Renamed to STRONG_MEDICAL_KEYWORDS — only unambiguous tokens
(physician, surgeon, mbbs, fracp, anaesthetist, etc.) plus speciality
roots. Old names kept as WEAK_MEDICAL_KEYWORDS but no longer used by the
gate. VIC_HOSPITAL_TOKENS dropped "university of melbourne" + "monash
university" (~50k staff/students each, vast majority not medical).

Audit recovery test: drive_test_missed.py would now also rescue Dr Anwar Nan
+ Dr Carolyn Bosak (real GPs previously rejected on Δtok=1). Modest +2 recall.

## Classifier v2.1 (superseded by v2.1.1)

Day-1 real run showed v2.0 was over-gating — 0 influencers across 201 rows.
User reviewed the 28 non-influencer near-misses (followers ≥ 300) and
approved exactly 4 under v2.1 thresholds:

- `follower_count >= 300`          ← v2.0 was 500
- `post_count_90d >= 1`            ← v2.0 was 2
- `last_post_date within 90 days`  ← unchanged
- ~~`avg_likes_per_post >= 5`~~    ← DROPPED (profiler bug: like counts
  aren't captured from the activity feed — logged 0 for every Day-1 row
  including weekly posters; was a false-fail gate)

Engagement stays as a soft-score signal only. Re-score of Day-1 data under
v2.1 surfaced exactly the 4 user-approved profiles: Fakhouri (7.8k fol, 4
posts, soft=8), Alan Paul (2.6k, 2, soft=6), Alice Bergin (448, 10, soft=6),
Amanda Osborne (379, 3, soft=3 → Ollama overruled to non_influencer).

**Day-1 outcome:** 0 new connects. The 3 user-approved heuristic-influencer
matches were all already 1st-degree connections (user had connected manually
between the Day-1 scrape and the re-profile run).

## Classifier v2 spec (superseded by v2.1 above — kept for history)

### Hard filters (ALL must pass, else `non_influencer`)
- `follower_count >= 500`             ← lowered from 1500
- `post_count_90d >= 2`               ← lowered from 4
- `last_post_date within 90 days`     ← extended from 60
- `avg_likes_per_post >= 5`           ← lowered from 15

### Engagement rate (new primary signal)
`engagement_rate = avg_likes_per_post / follower_count`
- >= 2% → strong; >= 1% → moderate; < 0.5% → weak

### Soft score
- +3 engagement_rate >= 2%
- +2 engagement_rate >= 1%
- +2 any video in last 90 days
- +2 Creator Mode on / prominent Follow button
- +2 followers >= 2000
- +1 followers >= 1000
- +1 per bio keyword (speaker, author, educator, podcast, media, researcher, presenter, columnist) — max +3
- +2 posts contain medical/clinical content
- +1 posts >= 1/month average over 90 days

### Decision thresholds
- Hard fail → `non_influencer` (heuristic)
- Hard pass + soft >= 4 → `influencer` (heuristic)
- Hard pass + soft 2–3 → Ollama edge-case call
- Hard pass + soft 0–1 → `non_influencer` (heuristic)
- **Medium-conf rows: soft >= 5 required before connect** (normal=4, medium=5)

### Ollama prompt (v2)
JSON input: `{name, specialty, recent_post_topics[≤10], follower_count, avg_likes, engagement_rate, has_video, bio_signals, post_frequency_per_month}`
Ask: *"This is a medical professional on LinkedIn. Based on their posting activity and engagement, would healthcare professionals consider them a trusted voice or active content creator in their field? They do not need a large following — consistent, relevant medical content with an engaged niche audience qualifies. Reply JSON: {classification: INFLUENCER|NOT, confidence: 0-1, reason: one line}"*

## V2 validation results (2026-04-21)

- 10/10 offline agreement on re-labelled fixtures; 9/10 Ollama live (row 6 medium-edge correctly downgraded by step 5b gate).
- **First v2 hard-filter passer: Adam Bystrzycki** (MED0001154842) — 939 followers, 11 posts/90d, last post 2026-04-13. Rejected under v1 (followers<1500). Passes all v2 hard gates. Soft score pending re-profile (avg_likes not in v1 CSV). Will enter queue naturally after 48h cool-down.
- Decision taken: Option C — sufficient evidence v2 thresholds work. Proceed to step 9 after gates clear.

## Step 7b spec — Live Google Sheets reporting

Sheet: `"LinkedIn Outreach Tracker"` (exists).

**Tab: `Live Run Log`** (new) — one row per event, appended in real time:
`timestamp, practitioner_id, name, speciality, linkedin_url, event (searched|profiled|classified|connect_sent|connect_failed|skipped_hot|skipped_non_influencer|not_found), outcome (success|fail|skipped|pending), detail (one-line), daily_connect_count (e.g. 3/10)`

**Tab: `Summary`** (new) — 6 live-updating cells:
`Run date, Total processed today, Connects sent today / daily cap, Total influencers found (all time), Total connects sent (all time), Last updated`

**Tab: `Processing Status`** (exists) — update stage per practitioner in real time.

Implementation rules:
- Write after EVERY event. Sheet failure must never crash the run — log locally, retry once.
- Add `log_live_event(row)` and `update_status(pid, stage, detail)` to `sheets_logger.py`.
- Call from: `searcher.py`, `profile_profiler.py`, `influencer_classifier.py`, `connector.py`, `main.py` (skips).
- `daily_connect_count` shows X/10 Day 1, X/25 Day 2.
- Commit: `"linkedin_outreach: step-7b live sheets reporting — Live Run Log + Summary tabs"`

## Commits — what's local vs pushed

**Pushed to origin/main:**
- `4b2aa2f` subset builder + CSV
- `c0dbc24` initial selector rewrite
- `385acca` PROFILE_DATA_JS fix + Connect anchor tag

**LOCAL ONLY** (do not push without user approval):
- `826322a` profile_profiler.py
- `0c0e663` location-match loosen v1
- `e1a17a6` visit tracker + cached-HTML mode + nav watchdog
- `39155a3` empty-loc accept v1
- `dca0179` two-scorer + three-tier confidence
- `acd955d` deferred medical check + VIC hospital registry
- `1c53533` 4e — medical-headline bypass
- `65fb9d8` 5a — profiler emits post_previews_90d
- `fe03ec3` 5b — influencer_classifier v1
- `67cf172` 5c — hand-label harness v1
- `dd65859` 5d — classifier v2
- `6bbce13` 5e — harness v2 + ROADMAP
- *(connector.py rewrite — step 6)*
- *(sheets_logger step 7a)*

## Key files

| File | Role |
|---|---|
| `build_subset.py` | One-shot subset builder. |
| `searcher.py` | LinkedIn people-search + JS result extraction. |
| `verifier.py` | Name scoring (two-scorer); location matching; medical signal helper. |
| `li_selectors.py` | **Semantic-only** selectors — NO class names. |
| `profile_profiler.py` | Scrapes followers, creator_mode, bio, experience, post_previews_90d. |
| `influencer_classifier.py` | v2 — heuristic + engagement_rate + Ollama edge-case. |
| `connector.py` | Step 6 rewrite. `CONNECT_BUTTON_FMT.format(name=name)` + `SEND_WITHOUT_NOTE_BUTTON`. More-menu fallback. |
| `sheets_logger.py` | Google Sheets writer. Step 7b adds `log_live_event` + `update_status`. |
| `_visit_tracker.py` | JSON-backed 48h cool-down. |
| `step4d_audit.py` | Seed-rotating orchestrator for regression testing. |
| `step5_classifier_test.py` | v2 hand-label harness. |

## Hard decisions (locked)

- Scope: VIC only.
- Connect: plain, NO note. `CONNECTION_NOTE` in config.py is **dead**.
- Daily cap: 20–25 connects.
- Subset: ~4k VIC specialists in top-50 postcodes.
- Medium-conf classifier threshold: soft >= 5 (normal >= 4, gap of 1).

## Safety rules (KEEP)

1. **Seed rotation** — never reuse 7, 42. Rotate 13, 17, 23, 29…
2. **Cached-HTML-first** — selector work uses `reprobe_profiles.py --cached`.
3. **48h cool-down** — `_visit_tracker.is_hot(url)` before any profile visit. No bypass without explicit user approval.
4. **Nav watchdog** — abort if `page.goto` > 30s.
5. **Stop on challenge/captcha** — `searcher._is_rate_limited` checks captcha + weekly-limit banner.
6. **No push without approval** — hold all local commits.
7. **Profile-visit budget** — ~200–300/day session cap.

## HOT set (as of 2026-04-21)

48h cool-down. Use cached HTML in `dry_run_debug/` for selector work:

```
dr-jason-ha
susie-tang-2a0baa344
philip-bloom-439846382
philip-bloom-2258a273
peter-lange-987ba575
glendon-bates-a33799399
hong-tran-46235784
mustafa-ebrahimjee-48472b1a3
dylan-rajeswaran-48821b269
pala-ravindra-reddy-a762b5234  (WRONG PERSON — rejected; don't re-confuse)
dennis-shandler-b3325230
fiona-christie-b7415139        (rejected post-scrape; not a doctor)
davidgillproperty              (rejected post-scrape; not a doctor)
adam-bystrzycki-9462ba1b       (HOT from step-8 re-eval; first v2 hard-filter passer — re-profile after cool-down)
```

## Known issues

- **More-menu → Connect path untested live.** Hard gate before Step 9.
- **post_count_90d may undercount.** Profiler scrapes general activity feed. If zero-influencer outcomes persist at scale, switch profiler to scrape Posts tab directly. Spot-check 5 profiles manually first.
- **Ollama must be running** (`ollama serve`) before classified runs.
- **main.py** uses `adapt_row` for subset CSV schema — do not revert.

## Resume checklist for a new agent

1. Read this file. Then `git log --oneline` and `git status`.
2. Do not push. User reviews each commit.
3. **Next task: Step 7b** — Live Run Log + Summary tabs in `sheets_logger.py`.
4. After 7b: `main.py --dry-run --limit 5` — confirm "Live Run Log" populates.
5. Live More-menu → Connect nav test on one Follow-primary profile.
6. Step 9 Day 1: 10 connects. Report before Day 2.

## Anti-patterns (do not repeat)

- Reusing saturated seeds (7, 42).
- Re-hitting HOT profiles for selector debugging — use cached HTML.
- Force-pushing, skipping hooks, amending published commits.
- Adding class-name selectors to `li_selectors.py` — semantic only.
- Bypassing `is_hot` without explicit user approval.
- Batching sheet writes — write per-event, not end-of-run.
