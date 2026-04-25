# Email Enrichment Pipeline — Agent Handoff Roadmap

> **Who reads this:** A new agent picking up the email enrichment pipeline.
> The LinkedIn outreach pipeline is a separate agent on a separate CLI — do not conflate the two.
> This pipeline feeds into the LinkedIn agent only via `vic_practitioners_enriched.csv`.

---

## Architecture (6 steps)

```
Step 1a  fetch_aihw_hospitals.py       → data/hospitals_vic_raw.csv
Step 1b  fetch_vic_health_directory.py → data/healthvic_directory.csv
Step 1c  resolve_domains.py            → data/hospitals_vic.csv          (MX-verified)
Step 2   build_postcode_index.py       → data/postcode_domains.json       (postcode → top-8 hospital domains)
Step 3   discover_formats.py           → data/domain_formats.json         (email format per domain)
Step 4   gp_resolver.py                → data/gp_practices.csv            ⚠ PARKED — see below
Step 5   disify_verify.py              → data/disify_probe_log.csv        (Disify API domain validation)
Step 6   apply_to_practitioners.py     → db_ARPHA/vic_practitioners_enriched.csv
```

All config (paths, tunables, confidence tiers) lives in `config.py`.
Shared utilities (CSV read/write, name parsing, email synthesis) live in `common.py`.

### Step 5 — SMTP → Disify migration (2026-04-20)

`smtp_verify.py` is retired. SMTP was blocked on all cloud providers (port 25 closed)
and home IPs hit Spamhaus PBL on hospital mail servers.

Replacement: `disify_verify.py` — uses `https://disify.com/api/email/{email}`.
- Free REST API, no key, no rate limit documented. Runs on local Mac. No VM required.
- Validates format + domain MX + DNS + disposable flag.
- **Domain-level only** — cannot confirm individual mailboxes.
- Confidence label: **`catch_all`** for passing results (domain real, inbox unconfirmable).
- Output: `data/disify_probe_log.csv`. `apply_to_practitioners.py` reads this first.
- `smtp_verify.py` renamed to `smtp_verify.py.bak` — do not delete.

**Known bugs fixed (2026-04-21):**
1. URL was `www.disify.com` (301 redirect) → fixed to `disify.com`.
2. Shared `consecutive_failures` counter caused permanent DNS fallback after 3 concurrent failures → replaced with per-request retry + backoff. Each request independently retries up to 3 times then falls back to DNS MX for that row only.

---

## Current State (as of 2026-04-21)

| Step | Status | Notes |
|------|--------|-------|
| 1a | ✅ Done | `hospitals_vic_raw.csv` populated |
| 1b | ✅ Done | `healthvic_directory.csv` populated |
| 1c | ✅ Done | `hospitals_vic.csv` populated, MX-verified |
| 2  | ✅ Done | `postcode_domains.json` built |
| 3  | ✅ Done | `domain_formats.json` built — 75 domains, all have formats |
| 4  | ⛔ Parked | HotDoc/HealthEngine are SPA-rendered — requests gets empty shell. Needs Playwright rewrite. |
| 5  | 🔄 **Running** | `disify_verify.py` in `tmux session: disify_run`. ~36% done, ~63 min remaining. Resumable. |
| 6  | ⏳ Blocked on 5 | Run `apply_to_practitioners.py` once Step 5 completes. |

### Live batch progress (last checked 2026-04-21)
- ~7,100 / 19,730 rows processed (36%)
- catch_all: ~4,450 (63%) — sendable
- unverified: ~2,678 (37%) — DNS fallback (smaller/regional domains)
- failed: 1
- Projected final: ~12,400 sendable, ~7,300 unverified

---

## Open Tasks — in priority order

### TASK 1 — Wait for / resume Disify batch  ← IN PROGRESS

Check progress:
```bash
wc -l email_enrichment/data/disify_probe_log.csv
tail -5 email_enrichment/disify_run.log
```

If tmux session died, resume safely (skips already-probed rows):
```bash
cd email_enrichment && source ../venv/bin/activate
tmux new -s disify_run "python disify_verify.py 2>&1 | tee -a disify_run.log"
```

Rate limits: 1.5s jitter, 5 concurrent async requests, per-request retry with backoff.

---

### TASK 2 — Re-run Step 6 after batch completes

```bash
cd email_enrichment && python apply_to_practitioners.py
```

Pure join — no external I/O. Regenerates `vic_practitioners_enriched.csv`.
Reads `disify_probe_log.csv` automatically (configured via `DISIFY_PROBE_LOG_CSV` in config.py).
Also emits fresh `smtp_targets.csv` for any remaining pending rows.

---

### TASK 3 — GP resolver Playwright rewrite (Step 4)

`gp_resolver.py` is a parked skeleton — HotDoc/HealthEngine are React SPAs.
Rewrite using Playwright. Target ~5–6k GPs (`speciality` contains `"General practice"`)
currently mismapped to hospital domains. See ROADMAP below for spec.
Tackle after Step 6 re-run gives baseline coverage numbers.

---

### TASK 4 — Extend pipeline to other states (NSW, QLD, SA, WA)

Do not start until VIC is fully verified and GP resolver is complete.

---

## File Map (email_enrichment/)

```
email_enrichment/
├── config.py                     ← all paths + tunables (DISIFY_PROBE_LOG_CSV added)
├── common.py                     ← shared utils
├── fetch_aihw_hospitals.py       ← Step 1a (do not modify)
├── fetch_vic_health_directory.py ← Step 1b (do not modify)
├── resolve_domains.py            ← Step 1c (do not modify)
├── build_postcode_index.py       ← Step 2  (do not modify)
├── discover_formats.py           ← Step 3  (do not modify)
├── gp_resolver.py                ← Step 4  (PARKED — Playwright rewrite needed)
├── disify_verify.py              ← Step 5  (active verifier — USE THIS)
├── smtp_verify.py.bak            ← Step 5  (retired — backup only, do not use)
├── test_disify.py                ← dry-run test for Disify API
├── apply_to_practitioners.py     ← Step 6
├── decisions.log                 ← autonomous decision audit log
├── disify_run.log                ← stdout from current batch run
├── requirements.txt
└── data/
    ├── hospitals_vic_raw.csv     ← Step 1a output
    ├── hospitals_vic.csv         ← Step 1c output (MX-verified)
    ├── healthvic_directory.csv   ← Step 1b output
    ├── postcode_domains.json     ← Step 2 output
    ├── domain_formats.json       ← Step 3 output (75 domains, all have formats)
    ├── gp_practices.csv          ← Step 4 output (empty until rewrite)
    ├── disify_probe_log.csv      ← Step 5 output (ACTIVE — being written now)
    ├── smtp_probe_log.csv        ← Step 5 legacy (~50 rows, westernhealth only)
    ├── smtp_targets.csv          ← Step 6 emits; Step 5 consumes (19,730 rows)
    ├── canary_cache.json         ← legacy SMTP cache
    └── australian_postcodes.csv  ← reference data
```

Output consumed by other agents:
- `db_ARPHA/vic_practitioners_enriched.csv` → LinkedIn outreach agent reads `pipeline` + `email_confidence`

---

## Confidence Tier Definitions (config.py)

| Value | Meaning | Source |
|-------|---------|--------|
| `catch_all` | Domain+MX valid, not disposable; individual inbox unconfirmable | Disify (active) |
| `verified` | SMTP canary 550 + target 250 = confirmed mailbox | Legacy SMTP only (retired) |
| `failed` | Disify: disposable/domain false \| SMTP: 550 user unknown | Both |
| `unverified` | Disify API retries exhausted → DNS MX fallback \| SMTP: timeout | Both |
| `ip_blocked` | SMTP IP reputation rejection — legacy only | Legacy SMTP only |
| `pending` | Email synthesised, not yet probed | — |
| `n_a` | LinkedIn pipeline — email not needed | — |

**Sendable:** `catch_all` + `verified`
**`ip_blocked` no longer produced** — Disify runs locally, no IP reputation issue.

---

## Format audit results (2026-04-21)

All 75 domains have entries in `domain_formats.json`. No missing formats.

Non-standard format domains (emails already synthesised to these):
| Domain | Format | Targets |
|--------|--------|---------|
| `lrh.com.au` | `flastname` | 252 |
| `mbph.org.au` | `firstnamelastname` | 166 |
| `yh.org.au` | `flastname` | 26 |
| `seymourhealth.org.au` | `firstnamelastname` | 18 |
| `ewhs.org.au` | `flastname` | 10 |
| `adh.org.au` | `firstnamelastname` | 9 |
| `redhs.com.au` | `flastname` | 7 |
| `ydhs.com.au` | `flastname` | 4 |
| `bshs.org.au` | `flastname` | 2 |

41 domains use `default` confidence (`firstname.lastname` assumed, unconfirmed).
Largest: `visioneyeinstitute.com.au` (1,277), `healthecare.com.au` (965), `grampianshealth.org.au` (463).

---

## How to run end-to-end from scratch (if all data lost)

```bash
cd email_enrichment
pip install -r requirements.txt
playwright install chromium  # only needed for Step 4 rewrite

python fetch_aihw_hospitals.py          # Step 1a
python fetch_vic_health_directory.py    # Step 1b
python resolve_domains.py               # Step 1c
python build_postcode_index.py          # Step 2
python discover_formats.py              # Step 3
# Step 4: run after Playwright rewrite
python test_disify.py                   # Step 5 dry-run — verify API first
python disify_verify.py                 # Step 5 full batch (~99 min on local Mac)
python apply_to_practitioners.py        # Step 6
```

All steps safe to run on local Mac. No VM required.
