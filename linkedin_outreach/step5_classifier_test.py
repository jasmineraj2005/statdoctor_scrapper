"""step5_classifier_test.py — hand-label harness for influencer_classifier.

Combines the 4 real profiled rows in step4d_profiler_output.json with 6
synthetic fixtures that cover every decision branch under SPEC V2 (2026-
04-21 revision: 500/2/90d/5 hard filters, engagement_rate soft signal,
new 4-pt threshold), yielding a 10-row labelled set. Runs `classify()`
on each and reports agreement.

Run: `python3 step5_classifier_test.py`  (from linkedin_outreach/)

Zero network: OLLAMA_URL is set to empty so the Ollama branch always
returns None → non_influencer(ollama_unreachable). With Ollama up, rows
in the edge-case band may legitimately diverge — the offline labels are
the conservative fallback.

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
    # 1. Classic volume influencer. High-conf, clean hard pass, soft >= 4.
    {
        "label": "influencer",
        "why":   "high-conf hard_pass soft>=4 (video+creator+fol>=2k+bio+regular) → heuristic",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-a",
            "name": "Dr High Follower",
            "followers": 25_000,
            "post_count_90d": 12,
            "last_post_date": _recent(5),
            "has_video_90d": True,
            "avg_likes_per_post": 120.0,    # ER 0.48% → 0 pts (mainstream passive audience)
            "creator_mode": True,
            "bio_signals": ["speaker", "author"],
            "verifier_confidence": "high",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
    # 2. Niche-engagement influencer — small but highly engaged audience.
    #    Exactly the case spec v2 is designed to catch.
    {
        "label": "influencer",
        "why":   "niche 5% engagement rate + medical posts → heuristic influencer",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-b",
            "name": "Dr Niche Engaged",
            "followers": 600,
            "post_count_90d": 4,
            "last_post_date": _recent(10),
            "has_video_90d": False,
            "avg_likes_per_post": 30.0,     # ER 5% → +3
            "creator_mode": False,
            "bio_signals": [],
            "verifier_confidence": "high",
            "post_previews_90d": [
                "thoughts on the new cardiology guidelines",
                "a tricky ecg case from this week's clinic",
            ],                              # medical keywords → +2
            "fail_reason": "",
        },
    },
    # 3. Hard fail on followers (< 500). Even with great activity, gate rejects.
    {
        "label": "non_influencer",
        "why":   "hard_fail followers<500 → non_influencer (heuristic)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-c",
            "name": "Dr Too Small",
            "followers": 300,
            "post_count_90d": 5,
            "last_post_date": _recent(5),
            "has_video_90d": True,
            "avg_likes_per_post": 40.0,
            "creator_mode": True,
            "bio_signals": ["speaker"],
            "verifier_confidence": "high",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
    # 4. Hard pass, soft = 0 — quiet hard-floor passer with no creator signals.
    #    999 followers keeps us under the +1 fol band; 5 avg_likes at 999 fol
    #    is ER 0.5% (below moderate tier). No creator, no video, no bio, no
    #    medical, posts<3 for regular.
    {
        "label": "non_influencer",
        "why":   "high-conf hard_pass soft=0 → non_influencer (heuristic)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-d",
            "name": "Dr Silent Pass",
            "followers": 999,
            "post_count_90d": 2,
            "last_post_date": _recent(15),
            "has_video_90d": False,
            "avg_likes_per_post": 5.0,
            "creator_mode": False,
            "bio_signals": [],
            "verifier_confidence": "high",
            "post_previews_90d": [],
            "fail_reason": "",
        },
    },
    # 5. High-conf Ollama edge case (soft 2-3). Creator mode alone → +2.
    #    Ollama off → default non_influencer(ollama_unreachable).
    {
        "label": "non_influencer",
        "why":   "high-conf soft=2 Ollama edge; Ollama off → non_influencer(ollama_unreachable)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-e",
            "name": "Dr Edge",
            "followers": 999,
            "post_count_90d": 2,
            "last_post_date": _recent(10),
            "has_video_90d": False,
            "avg_likes_per_post": 5.0,
            "creator_mode": True,           # +2
            "bio_signals": [],
            "verifier_confidence": "high",
            # deliberately non-medical preview — medical keyword would push
            # soft to 4 and skip the Ollama edge band we're testing here.
            "post_previews_90d": ["attending the rsl annual dinner last night"],
            "fail_reason": "",
        },
    },
    # 6. Medium-conf Ollama edge (soft band 1-3 for medium). fol>=2000 → +2,
    #    nothing else. Soft=2. Ollama off → non_influencer(ollama_unreachable).
    {
        "label": "non_influencer",
        "why":   "medium-conf soft=2 Ollama edge; Ollama off → non_influencer(ollama_unreachable)",
        "profile": {
            "url": "https://www.linkedin.com/in/inf-f",
            "name": "Dr Medium Border",
            "followers": 2_500,             # fol>=2000 → +2
            "post_count_90d": 2,
            "last_post_date": _recent(20),
            "has_video_90d": False,
            "avg_likes_per_post": 5.0,      # ER 0.2% → 0 pts; clears hard-floor
            "creator_mode": False,
            "bio_signals": [],
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
