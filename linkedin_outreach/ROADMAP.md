# linkedin_outreach — Roadmap & Handoff

For a new agent picking this up cold. Status as of 2026-04-20.

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
2. **`data/vic_linkedin_classifications.csv`** — NOT YET WRITTEN. Appended as
   classifier runs. Columns: `practitioner_id, linkedin_url, classification
   (influencer|non_influencer|not_found|error), soft_score,
   hard_filters_passed, follower_count, post_count_90d, last_post_date,
   has_video_90d, creator_mode, bio_signals (pipe-delimited),
   classifier_source (heuristic|ollama), classifier_confidence, classified_at,
   fail_reason`

## Progress by step

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | Secret not leaked (`gsheet_creds.json` in gitignored dir) | ✅ done | — |
| 2 | VIC high-yield subset builder (`build_subset.py`) + CSV | ✅ **pushed** | `4b2aa2f` |
| 3 | Selectors rewritten for 2026 LinkedIn DOM | ✅ **pushed** | `385acca`, `c0dbc24` |
| 4a | `profile_profiler.py` — followers/creator-mode/posts/bio | ✅ local | `826322a` |
| 4b-v1 | Location-match loosen (Melbourne/greater/Australia-only soft) | ✅ local | `0c0e663` |
| 4b-v2 | Empty-loc accept when name_score≥95 (first pass) | ✅ local | `39155a3` |
| 4b-v3 | Two-scorer (token_sort + token_set), 3-tier confidence | ✅ local | `dca0179` |
| 4b-v4 | **Deferred medical check to post-scrape + VIC hospital registry** | ✅ local | `acd955d` |
| 4c | Re-audit seed=42 (measure verifier ceiling) | ✅ done (4/10 legit, Pala cleanly rejected) | — |
| 4d | Profiler dry-test on 5 new matches | ✅ done seed=7+42; 10 legit across 20 rows | — |
| 4e | Medical-signal headline bypasses `no_degree_badge` in `is_active_account` (Christos fix) | ✅ local | *pending commit* |
| 5 | `influencer_classifier.py` — heuristic + Ollama edge-case | ⏳ next up (Christos resolved) | — |
| 6 | Gate `connector.py` on `is_influencer` + no-note flow | ⏳ blocked on 5 | — |
| 7 | Extend `sheets_logger.py` — Influencers VIC + Reviewed Skipped + Processing Status tabs | ⏳ blocked on 5 | — |
| 8 | Full 50-row dry-run | ⏳ blocked on 7 | — |
| 9 | Staged real run — Day 1: 10 connects; Day 2: 25 | ⏳ blocked on 8 + user approval | — |

### Christos decision (2026-04-20) — RESOLVED

Chosen fix: **reorder — medical headline signal bypasses `no_degree_badge`**.
Rationale: degree badge reflects viewer-to-profile network distance, not
account liveness. An out-of-network real doctor has no badge but is clearly
alive if the headline contains medical content. Structural dead-account
signals (`empty_headline`, `no_action_button`) still reject.

Implementation:
- `verifier.is_active_account(profile, medical_signal=False)` now takes
  an optional flag; when True, the `no_degree_badge` branch is skipped.
- `verifier.verify_profile` computes `headline_is_medical(headline)` and
  passes it in.
- Rejected A1 (defer `is_active_account` wholesale to post-scrape): would
  have doubled profile-visit budget on junk, unacceptable at 30k lifetime
  cap. A2 keeps the cheap pre-visit gate for truly-dead non-medical accounts.
- Deeper post-scrape full-text medical check (`profile_profiler`) is
  unchanged and still acts as the safety net for medium-conf (empty-loc)
  rows.

## Commits — what's local vs pushed

**Pushed to origin/main:**
- `4b2aa2f` subset builder + CSV
- `c0dbc24` initial selector rewrite (broken PROFILE_DATA_JS, superseded)
- `385acca` PROFILE_DATA_JS fix + Connect anchor tag

**LOCAL ONLY** (do not push without user approval):
- `826322a` profile_profiler.py
- `0c0e663` location-match loosen v1
- `e1a17a6` visit tracker + cached-HTML mode + nav watchdog
- `39155a3` empty-loc accept v1 (superseded by dca0179)
- `dca0179` two-scorer + three-tier confidence
- `acd955d` deferred medical check + VIC hospital registry + `step4d_audit.py`
- *pending* 4e — medical-headline bypass of `no_degree_badge`

## Key files

| File | Role |
|---|---|
| `build_subset.py` | One-shot subset builder. Rerun only if heuristic changes. |
| `searcher.py` | LinkedIn people-search + JS-based result extraction. `EXTRACT_RESULTS_JS` uses `a[href*='/in/']` walker (class-agnostic). |
| `verifier.py` | Name scoring (two-scorer: token_sort primary, token_set floor); location matching; post-scrape `medical_signal_in_text` helper. |
| `li_selectors.py` | **Semantic-only** selectors (aria-label, role, tags — NO class names — LinkedIn's classes are opaque hashes). `PROFILE_DATA_JS` extracts name/headline/location/URL; `CONNECT_BUTTON_FMT` is `a[aria-label="Invite {name} to connect"]` (owner Connect is an anchor, sidebar is a button — this split auto-disambiguates). |
| `profile_profiler.py` | Scrapes followers, creator_mode, bio, experience, recent activity. Post-scrape re-evaluates medium-conf rows via `verifier.medical_signal_in_text`. Downgrades to `""` + `fail_reason="medium_no_medical_signal"` when no signal. |
| `connector.py` | **Will break at runtime** — references removed selector names (`CONNECT_BUTTON_PRIMARY`, `SEND_BUTTON`, etc.). Needs rewrite at step 6. Dry-run mode still safe (early-return before touching selectors). |
| `_visit_tracker.py` | JSON-backed 48h cool-down. `mark_visited` / `is_hot` / `hot_set`. URL canonicalisation strips overlay/recent-activity suffixes. |
| `visited_profiles.json` | HOT-set store. Editable by hand if needed. |
| `selector_dry_run.py` | 10-row audit harness. Use `--sample --seed N` (rotate seeds; don't reuse). |
| `reprobe_profiles.py --cached HTML_PATH` | Offline selector validation against saved HTML in `dry_run_debug/`. **Zero network.** Strips trusted-types CSP to allow `set_content`. |
| `profiler_test.py` | Run profiler on explicit URL list. HOT URLs auto-skipped. |
| `step4d_audit.py` | Seed-rotating orchestrator: search → verify → profiler. Respects HOT cool-down. Good regression harness for future verifier/profiler changes. |

## Hard decisions (locked)

- Scope: VIC only.
- Connect: plain, NO note. `CONNECTION_NOTE` in config.py is **dead** — step 6 must use `SEND_WITHOUT_NOTE_BUTTON`.
- Daily cap: 20–25 connects (spec lock).
- Subset: ~4k VIC specialists in top-50 postcodes — do not pad with regional or plain-General GPs.

### Influencer classifier spec (locked — for step 5)

**Hard filters** (ALL must pass, else `non_influencer`):
- `follower_count >= 1500`
- `>= 4 original posts in last 90 days` (not reshares)
- `last_post_date within 60 days`
- `avg_likes_per_post >= 15`

**Soft score** (if hard pass):
- +2 any video in last 90 days
- +2 Creator Mode on / prominent Follow button
- +1 per bio keyword (`speaker, author, educator, podcast, media, researcher`), max +3
- +2 if followers 5k–10k
- +4 if followers 10k+

**Decision**:
- Hard fail → `non_influencer` (heuristic)
- Hard pass + soft ≥ 3 → `influencer` (heuristic)
- Hard pass + soft 1–2 → **Ollama edge-case call**
- Hard pass + soft 0 → `non_influencer` (heuristic)

**Ollama prompt**: JSON input `{name, specialty, recent_post_topics[≤10], follower_count, avg_likes, has_video, bio_signals}`; ask: *"Is this a medical influencer whose content would resonate with healthcare professionals? Reply JSON: {classification: INFLUENCER|NOT, confidence: 0-1, reason: one line}"*. If Ollama unreachable or parse fails → default `non_influencer`. Never block on Ollama.

**Classifier addendum (user instruction)**: medium-confidence rows require `soft_score >= 5` before connect (vs 3 for high). Value stored in `config.MEDIUM_CONF_CLASSIFIER_SOFT_SCORE`.

### Google Sheets output (step 7)

Sheet: `"LinkedIn Outreach Tracker"` (exists). Three tabs:
- `Influencers VIC`: practitioner_id, name, speciality, postcode, linkedin_url, follower_count, post_count_90d, has_video, soft_score, classifier_source, connect_status, connect_sent_at, last_checked
- `Reviewed Skipped`: practitioner_id, name, linkedin_url, fail_reason, follower_count, last_post_date, checked_date
- `Processing Status` (user addendum): per-practitioner pipeline stage (pending/searched/profiled/classified/connected/skipped) + last_updated

CSV is source of truth, Sheet mirrors it.

## Safety rules enforced mid-session (KEEP)

1. **Seed rotation** — never reuse the same seed on consecutive audits. Rotate 7, 13, 17, 23, 29… seed=42 and seed=7 are now saturated.
2. **Cached-HTML-first selector validation** — any selector-only iteration uses `reprobe_profiles.py --cached dry_run_debug/<file>.html`. Live reprobes only for genuinely new DOM cases.
3. **48h cool-down** — `_visit_tracker.is_hot(url)` gate before any profile visit. All entry points respect it (profile_profiler, selector_dry_run, reprobe_profiles live mode, profiler_test, step4d_audit).
4. **Nav watchdog** — abort if a single `page.goto` takes > 30s (possible throttle). Implemented in selector_dry_run + reprobe live.
5. **Stop on challenge/captcha** — `searcher._is_rate_limited` already checks for captcha iframes + weekly-limit banner text. Don't bypass.
6. **No push without approval** — user has been reviewing each commit. Hold all local commits until explicit approval.
7. **Profile-visit budget** — ~200–300/day session cap per spec. Connect sends 20–25/day FIFO by soft_score desc.

## HOT set (as of 2026-04-20, ages in hours)

Full 48h cool-down on these — any work involving them must use cached HTML dumps in `dry_run_debug/`:

```
dr-jason-ha
susie-tang-2a0baa344
philip-bloom-439846382       (empty-profile variant, probably not the real Dr Bloom)
philip-bloom-2258a273        (real Dr Philip David Bloom — "Sports Physician")
peter-lange-987ba575
glendon-bates-a33799399
hong-tran-46235784
mustafa-ebrahimjee-48472b1a3
dylan-rajeswaran-48821b269
pala-ravindra-reddy-a762b5234 (WRONG PERSON — rejected post-fix; don't re-confuse)
dennis-shandler-b3325230
fiona-christie-b7415139       (rejected post-scrape; not a doctor)
davidgillproperty             (rejected post-scrape; not a doctor)
```

## Known edge cases / issues

- **connector.py will break at runtime** — references `selectors.CONNECT_BUTTON_PRIMARY` etc. which no longer exist. Dry-run early-return protects the step-8 dry-run path. Must be rewritten at step 6 to use `CONNECT_BUTTON_FMT.format(name=name)` (anchor, not button), `SEND_WITHOUT_NOTE_BUTTON` (no note flow), and the More-menu fallback for Follow-primary profiles.
- **More-menu → Connect path untested live.** Step 6 must dry-run nav (click More → find Connect in dropdown) on one profile before any real connect fires. LinkedIn has at least 2 UI variants; wrong click can accidentally Follow or Message.
- **Pala-type false positives**: Mitigated by two-scorer (`sort=84` rejects). Keep `NAME_TOKEN_DELTA_MAX=1` and `NAME_HIGH_CONF_SCORE=95`.
- **Out-of-network researchers (Christos)**: Resolved 2026-04-20. Medical
  headline signal bypasses `no_degree_badge` in `is_active_account`. See
  "Christos decision (2026-04-20) — RESOLVED" above.
- **Ollama not installed yet** — `brew install ollama && ollama pull llama3.2:3b` needed before step 5. Step 5 code must gracefully handle `ollama` unreachable (default to `non_influencer`).
- **Existing `main.py` `load_queue` expects old schema** (`state`, `suburb`, `specialities`, `registration_type`). Subset CSV has (`postcode_searched`, `location`, `speciality`). `selector_dry_run.adapt_row` + `step4d_audit.adapt_row` already handle mapping; **main.py must be updated at step 6** to read the subset CSV with the same adapter.

## Resume checklist for a new agent

1. **Read this file first**, then `CLAUDE.md` / auto-memory / recent git log (`git log --oneline`).
2. `git status` — verify no unexpected uncommitted state. Nothing should be staged.
3. **Do not push** local commits; user reviews each.
4. Christos decision is resolved (see section above). Proceed to step 5:
   - Install Ollama if needed (`brew install ollama && ollama pull llama3.2:3b`)
   - Build `influencer_classifier.py` with spec above
   - Hand-label 10 profiles, check classifier agrees, report accuracy
5. **Before touching connector.py (step 6)**: live dry-run the More-menu → Connect nav path on ONE non-HOT profile. Confirm the right element clicks.
6. **Before step 8**: adapt `main.py` to consume `data/vic_high_yield_subset.csv` + feed `verifier_confidence` all the way through to the classifier + sheets_logger.
7. **Step 8 gate**: `python main.py --dry-run --limit 50` must complete with the CSV populating, both sheet tabs populating, and ZERO connects firing. Report before any real-run.

## Anti-patterns (do not repeat)

- Running audits on the same seed repeatedly (was burning Jason Ha). Rotate seeds.
- Re-hitting a profile for selector debugging when its HTML is already in `dry_run_debug/`. Use cached mode.
- Force-pushing, skipping hooks (`--no-verify`), amending published commits — all forbidden per session defaults.
- Accepting "researcher" into `MEDICAL_KEYWORDS` (whack-a-mole). The correct path is the deferred post-scrape check on full profile text (already implemented in `acd955d`).

## Session memory reference

Memory file: `project_linkedin_outreach.md` in the auto-memory system. Update there when user's preferences/decisions solidify.
