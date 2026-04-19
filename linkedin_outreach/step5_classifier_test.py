"""step5_classifier_test.py — hand-label harness for influencer_classifier.

Combines the 4 real profiled rows in step4d_profiler_output.json with 6
synthetic fixtures that cover every decision branch, yielding a 10-row
labelled set. Runs `classify()` on each and reports agreement.

Run: `python3 step5_classifier_test.py`  (from linkedin_outreach/)

Zero network: OLLAMA_URL is set to empty so the Ollama branch always
returns None → non_influencer(ollama_unreachable). Once Ollama is
installed locally, re-run without the env override to see real verdicts.

This is NOT step 7's CSV writer — output goes to stdout only. The classifier
decision logic is the only thing under test here.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

# Force Ollama off for deterministic offline testing. Remove this line (or
# `unset OLLAMA_URL`) once `brew install ollama && ollama pull llama3.2:3b`
# is done to measure real heuristic/Ollama disagreement.
os.environ["OLLAMA_URL"] = ""

import influencer_classifier as ic  # noqa: E402


HERE = Path(__file__).resolve().parent


def _recent(days_ago: int) -> str:
    return (datetime.now().date() - timedelta(days=days_ago)).isoformat()


# ── Synthetic fixtures (6) ───────────────────────────────────────────────────
# Each covers a distinct decision branch. Hand-label = what the human
# expects the classifier to output given the spec.

SYNTHETIC = [
    # 1. Classic medical influencer. High-conf, clean hard pass, soft ≥ 3.
    {
        "label": "influencer",
        "why":   "high-conf hard_pass soft>=3 → heuristic influencer",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-a",
            "name": "Dr High Follower",
            "followers": 25_000,           # +4
            "post_count_90d": 12,
            "last_post_date": _recent(5),
            "has_video_90d": True,         # +2
            "avg_likes_per_post": 120.0,
            "creator_mode": True,          # +2
            "bio_signals": ["speaker", "author"],
            "verifier_confidence": "high",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
    # 2. Hard fail on followers. Even with activity the gate rejects.
    {
        "label": "non_influencer",
        "why":   "hard_fail followers<1500 → non_influencer (heuristic)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-b",
            "name": "Dr Small Network",
            "followers": 800,
            "post_count_90d": 10,
            "last_post_date": _recent(2),
            "has_video_90d": True,
            "avg_likes_per_post": 40.0,
            "creator_mode": True,
            "bio_signals": ["speaker"],
            "verifier_confidence": "high",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
    # 3. Hard pass, soft=0. Borderline but cleanly non_influencer (heuristic).
    {
        "label": "non_influencer",
        "why":   "high-conf hard_pass soft=0 → non_influencer (heuristic)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-c",
            "name": "Dr Silent Active",
            "followers": 2_000,            # below 5k, 0 soft pts
            "post_count_90d": 5,
            "last_post_date": _recent(15),
            "has_video_90d": False,
            "avg_likes_per_post": 18.0,
            "creator_mode": False,
            "bio_signals": [],
            "verifier_confidence": "high",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
    # 4. High-conf Ollama edge case (soft 1-2). With Ollama off → defaults to
    #    non_influencer with fail_reason ollama_unreachable.
    {
        "label": "non_influencer",
        "why":   "high-conf soft=2 Ollama edge; Ollama off → non_influencer(ollama_unreachable)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-d",
            "name": "Dr Edge",
            "followers": 2_500,            # no follower band pts
            "post_count_90d": 6,
            "last_post_date": _recent(10),
            "has_video_90d": False,
            "avg_likes_per_post": 22.0,
            "creator_mode": True,          # +2
            "bio_signals": [],
            "verifier_confidence": "high",
            "post_previews_90d": ["teaching junior doctors about ECG reading"],
            "fail_reason": "",
        },
    },
    # 5. Medium-conf hard_pass, soft=5. Hits the medium threshold → influencer.
    {
        "label": "influencer",
        "why":   "medium-conf hard_pass soft>=5 → heuristic influencer",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-e",
            "name": "Dr Medium Match",
            "followers": 7_500,            # +2 (5k-10k)
            "post_count_90d": 6,
            "last_post_date": _recent(12),
            "has_video_90d": True,         # +2
            "avg_likes_per_post": 30.0,
            "creator_mode": True,          # +2
            "bio_signals": ["researcher"], # +1
            "verifier_confidence": "medium",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
    # 6. Medium-conf hard_pass, soft=3. Falls in expanded Ollama band (1-4)
    #    because medium threshold is 5. With Ollama off → non_influencer.
    {
        "label": "non_influencer",
        "why":   "medium-conf soft=3 falls in expanded Ollama band (1-4); Ollama off → non_influencer(ollama_unreachable)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-f",
            "name": "Dr Medium Border",
            "followers": 2_500,            # 0 pts from band
            "post_count_90d": 5,
            "last_post_date": _recent(20),
            "has_video_90d": False,
            "avg_likes_per_post": 20.0,
            "creator_mode": True,          # +2
            "bio_signals": ["author"],     # +1
            "verifier_confidence": "medium",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
]


# ── Real fixtures (4 from step4d_profiler_output.json) ───────────────────────
# Hand-label reasoning for each (followers/posts/headline — inspected):
#   Fiona Christie   → non_influencer (medium_no_medical_signal downgrade)
#   David Gill       → non_influencer (medium_no_medical_signal downgrade)
#   Dennis Shandler  → non_influencer (followers=9 → hard_fail)
#   Philip Bloom     → non_influencer (followers=2 → hard_fail)

REAL_LABELS = {
    "https://www.linkedin.com/in/fiona-christie-b7415139":   "non_influencer",
    "https://www.linkedin.com/in/davidgillproperty":         "non_influencer",
    "https://www.linkedin.com/in/dennis-shandler-b3325230":  "non_influencer",
    "https://www.linkedin.com/in/philip-bloom-2258a273":     "non_influencer",
}


def _load_real() -> list[dict]:
    path = HERE / "step4d_profiler_output.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    out = []
    for p in data.get("profiled", []):
        # Default previews field for pre-5.1 captures — the profiler only
        # started persisting it as of this step's first commit.
        p.setdefault("post_previews_90d", [])
        label = REAL_LABELS.get(p.get("url", ""))
        if label is None:
            continue
        out.append({
            "label":      label,
            "why":        f"real profile ({p.get('name','?')}); "
                          f"{p.get('fail_reason','') or 'heuristic'}",
            "profile":    p,
            "prac_id":    p.get("practitioner_id", ""),
            "specialty":  "",
        })
    return out


# ── Harness ──────────────────────────────────────────────────────────────────

def main() -> None:
    rows = []
    for i, f in enumerate(SYNTHETIC, start=1):
        rows.append({
            "name": f"syn-{i} {f['profile']['name']}",
            "expected": f["label"],
            "why": f["why"],
            "profile": f["profile"],
            "prac_id": "",
            "specialty": "",
        })
    for r in _load_real():
        rows.append({
            "name": f"real  {r['profile'].get('name','?')}",
            "expected": r["label"],
            "why": r["why"],
            "profile": r["profile"],
            "prac_id": r["prac_id"],
            "specialty": r["specialty"],
        })

    agree = 0
    total = len(rows)
    print(f"\n=== step5 classifier harness — {total} rows, Ollama off ===\n")
    print(f"{'#':>3}  {'name':40s}  {'expected':16s}  {'got':16s}  {'soft':>4s}  "
          f"{'hard':>4s}  agree  why")
    print("-" * 140)
    for i, r in enumerate(rows, start=1):
        out = ic.classify(
            r["profile"],
            practitioner_id=r["prac_id"],
            ahpra_specialty=r["specialty"],
        )
        got = out["classification"]
        match = (got == r["expected"])
        agree += int(match)
        print(f"{i:>3}  {r['name'][:40]:40s}  {r['expected']:16s}  {got:16s}  "
              f"{out['soft_score']:>4d}  "
              f"{'Y' if out['hard_filters_passed'] else 'N':>4s}  "
              f"{'✓' if match else '✗':5s}  {r['why']}")

    print("-" * 140)
    pct = (agree / total * 100) if total else 0.0
    print(f"agreement: {agree}/{total} ({pct:.0f}%)\n")

    if agree != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
