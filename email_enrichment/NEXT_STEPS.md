# Email Pipeline — Next Steps

## 1. Monitor / resume Disify batch  ← IN PROGRESS (~63 min remaining)

Check progress:
```bash
wc -l email_enrichment/data/disify_probe_log.csv
tail -5 email_enrichment/disify_run.log
```

If session died, resume (safe — skips already-probed rows):
```bash
cd email_enrichment && source ../venv/bin/activate
tmux new -s disify_run "python disify_verify.py 2>&1 | tee -a disify_run.log"
```

Expected final: ~12,400 catch_all (sendable), ~7,300 unverified, ~1 failed.

## 2. Re-run Step 6 once batch completes

```bash
cd email_enrichment && python apply_to_practitioners.py
```

Regenerates `db_ARPHA/vic_practitioners_enriched.csv` with real confidence tiers.
Check the breakdown printed at end — expect majority `catch_all`.

## 3. GP resolver Playwright rewrite (Step 4)

`gp_resolver.py` is a parked skeleton. Rewrite using Playwright (already installed).
Target ~5–6k GPs currently mismapped to hospital domains.
Tackle after Step 6 re-run gives baseline coverage numbers.

## 4. Assess coverage and decide on other states

After VIC fully verified, check sendable % across email pipeline cohort.
Extend to NSW/QLD/SA/WA only after VIC GP resolver is complete.
