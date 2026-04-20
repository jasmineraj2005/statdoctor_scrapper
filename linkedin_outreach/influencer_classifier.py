"""influencer_classifier.py — gate the connect queue on "is this a medical
influencer whose content resonates with healthcare professionals".

Spec v2 (2026-04-21 revision) — target is the genuinely-active medical
content creator with an engaged niche audience, NOT a mainstream celebrity
doctor. A real professional with 400 followers and 5% engagement outranks
a dormant 3k-follower account.

Consumes a profile dict from `profile_profiler.profile()`. Produces a
classification dict matching the v2 CSV schema (15 v1 cols + engagement_rate).
Two decision tiers:

  1. Heuristic  — hard filters (followers / posts / recency / avg likes),
                  then a soft score. Clean pass → influencer; clean fail →
                  non_influencer.
  2. Ollama     — soft score sits in the ambiguous band → ask the locally
                  hosted llama3.2:3b for a JSON verdict. Ollama is called
                  via stdlib `urllib.request` (no new deps). If Ollama is
                  unreachable / slow / returns garbage, the classifier
                  defaults to `non_influencer`. NEVER block on Ollama.

Decision branches:

  verifier_confidence="high" (or empty — treated as high)
    hard_fail              → non_influencer (heuristic)
    hard_pass, soft >= 4   → influencer     (heuristic)
    hard_pass, soft 2–3    → Ollama         (edge-case)
    hard_pass, soft 0–1    → non_influencer (heuristic)

  verifier_confidence="medium"
    hard_fail              → non_influencer (heuristic)
    hard_pass, soft >= 5   → influencer     (heuristic)
    hard_pass, soft 1–4    → Ollama         (edge-case; band expanded)
    hard_pass, soft 0      → non_influencer (heuristic)

    Spec: medium-conf rows require soft >= 5 before a real connect (one
    point tighter than high-conf's 4 — lower-confidence matches must
    clear more signal). An Ollama "influencer" vote on a medium row with
    soft < 5 is recorded in classifications.csv (audit trail) but
    downgraded to `skipped` at main.py's connect gate.

A profile with `fail_reason` from the profiler (nav error, downgraded
medium-no-signal, etc.) is short-circuited to `error` or `not_found` —
we do not attempt to classify an incomplete scrape.

Output schema (CSV columns, locked v2):
    practitioner_id, linkedin_url, classification, soft_score,
    hard_filters_passed, follower_count, post_count_90d, last_post_date,
    has_video_90d, creator_mode, bio_signals, classifier_source,
    classifier_confidence, classified_at, fail_reason, engagement_rate
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

import config


# ── Ollama config ────────────────────────────────────────────────────────────

OLLAMA_URL_DEFAULT = "http://localhost:11434/api/generate"
OLLAMA_MODEL       = "llama3.2:3b"
OLLAMA_TIMEOUT_SEC = 25


# ── Hard filters (spec v2) ───────────────────────────────────────────────────

HF_FOLLOWERS_MIN  = 500   # was 1500 — niche medical creators often sit 500–2k
HF_POSTS_90D_MIN  = 2     # was 4   — consistency > volume for specialists
HF_LAST_POST_DAYS = 90    # was 60  — extended to include quarterly posters
HF_AVG_LIKES_MIN  = 5     # was 15  — small engaged niche audiences yield low abs numbers


# ── Soft-score thresholds (spec v2) ──────────────────────────────────────────

# High-conf rows clear at soft >= 4; the Ollama edge band is 2-3.
SOFT_THRESHOLD_NORMAL  = 4
SOFT_OLLAMA_BAND_NORMAL = (2, 3)

# Medium-conf rows clear at soft >= 5 (one point tighter than high-conf —
# lower-confidence matches must clear more signal before firing a connect).
# Ollama edge band 1-4 covers the gap between soft=0 (heuristic non) and
# soft=5 (heuristic influencer).
SOFT_THRESHOLD_MEDIUM  = 5
SOFT_OLLAMA_BAND_MEDIUM = (1, 4)


# ── Engagement-rate bands (spec v2 — primary signal) ─────────────────────────

ENGAGEMENT_STRONG   = 0.02   # >= 2% → +3
ENGAGEMENT_MODERATE = 0.01   # >= 1% → +2
# < 0.5% is "weak signal" per spec — no points.


# ── Follower bands (spec v2 — mutually exclusive) ────────────────────────────

FOLLOWER_BAND_HIGH  = 2000   # +2
FOLLOWER_BAND_MID   = 1000   # +1


# ── Public API ───────────────────────────────────────────────────────────────

def classify(profile: dict[str, Any],
             practitioner_id: str = "",
             ahpra_specialty: str = "") -> dict[str, Any]:
    """Run hard filters + soft score → heuristic-or-Ollama decision.

    Returns a dict matching the v2 CSV schema. Pure function modulo the
    optional Ollama HTTP call (which is guarded against every error mode).
    """
    url           = profile.get("url", "") or ""
    verif_conf    = profile.get("verifier_confidence", "") or ""
    profiler_fail = profile.get("fail_reason", "") or ""

    engagement = _engagement_rate(profile)

    out = _schema_row(
        practitioner_id=practitioner_id,
        linkedin_url=url,
        follower_count=int(profile.get("followers", 0) or 0),
        post_count_90d=int(profile.get("post_count_90d", 0) or 0),
        last_post_date=profile.get("last_post_date"),
        has_video_90d=bool(profile.get("has_video_90d", False)),
        creator_mode=bool(profile.get("creator_mode", False)),
        bio_signals=list(profile.get("bio_signals") or []),
        engagement_rate=engagement,
    )

    # Short-circuit on profiler failure.
    if profiler_fail:
        if profiler_fail == "medium_no_medical_signal":
            out["classification"]    = "non_influencer"
            out["classifier_source"] = "heuristic"
            out["fail_reason"]       = profiler_fail
            return out
        out["classification"] = "error"
        out["fail_reason"]    = profiler_fail
        return out

    # Hard filters
    hard_pass, hard_fail_reason = _hard_filters(profile)
    out["hard_filters_passed"] = hard_pass
    if not hard_pass:
        out["classification"]    = "non_influencer"
        out["classifier_source"] = "heuristic"
        out["fail_reason"]       = hard_fail_reason
        return out

    # Soft score
    soft = _soft_score(profile, engagement)
    out["soft_score"] = soft

    # Tier-specific threshold + Ollama band
    if verif_conf == "medium":
        pass_thresh = SOFT_THRESHOLD_MEDIUM
        ollama_lo, ollama_hi = SOFT_OLLAMA_BAND_MEDIUM
    else:
        pass_thresh = SOFT_THRESHOLD_NORMAL
        ollama_lo, ollama_hi = SOFT_OLLAMA_BAND_NORMAL

    if soft >= pass_thresh:
        out["classification"]    = "influencer"
        out["classifier_source"] = "heuristic"
        return out

    if soft == 0:
        out["classification"]    = "non_influencer"
        out["classifier_source"] = "heuristic"
        return out

    # Ambiguous band → ask Ollama.
    if ollama_lo <= soft <= ollama_hi:
        verdict = _call_ollama(profile, ahpra_specialty, engagement)
        if verdict is None:
            out["classification"]        = "non_influencer"
            out["classifier_source"]     = "ollama"
            out["classifier_confidence"] = 0.0
            out["fail_reason"]           = "ollama_unreachable"
            return out
        out["classification"]        = verdict["classification"]
        out["classifier_source"]     = "ollama"
        out["classifier_confidence"] = verdict["confidence"]
        out["fail_reason"]           = ""
        # For medium rows: Ollama INFLUENCER + soft < normal_threshold means
        # the connect gate will block anyway; classifier records the tentative
        # verdict so audits can see disagreement, main.py downgrades at send.
        return out

    # Defensive fall-through — any score not covered above → non_influencer.
    out["classification"]    = "non_influencer"
    out["classifier_source"] = "heuristic"
    out["fail_reason"]       = f"soft_score_out_of_bands:{soft}"
    return out


# ── Engagement rate ──────────────────────────────────────────────────────────

def _engagement_rate(profile: dict[str, Any]) -> float:
    """avg_likes_per_post / follower_count. 0 when followers is 0 (protects
    against div-by-zero AND against new accounts inflating ratio artificially).
    """
    fol = int(profile.get("followers", 0) or 0)
    if fol <= 0:
        return 0.0
    avg = float(profile.get("avg_likes_per_post", 0.0) or 0.0)
    return round(avg / fol, 6)


# ── Hard filters ─────────────────────────────────────────────────────────────

def _hard_filters(profile: dict[str, Any]) -> tuple[bool, str]:
    followers = int(profile.get("followers", 0) or 0)
    if followers < HF_FOLLOWERS_MIN:
        return False, f"followers<{HF_FOLLOWERS_MIN}"

    posts_90 = int(profile.get("post_count_90d", 0) or 0)
    if posts_90 < HF_POSTS_90D_MIN:
        return False, f"post_count_90d<{HF_POSTS_90D_MIN}"

    last_dt = _parse_iso_date(profile.get("last_post_date"))
    if last_dt is None:
        return False, "no_last_post_date"
    age_days = (datetime.now().date() - last_dt).days
    if age_days > HF_LAST_POST_DAYS:
        return False, f"last_post>{HF_LAST_POST_DAYS}d"

    avg_likes = float(profile.get("avg_likes_per_post", 0.0) or 0.0)
    if avg_likes < HF_AVG_LIKES_MIN:
        return False, f"avg_likes<{HF_AVG_LIKES_MIN}"

    return True, ""


# ── Soft score (spec v2) ─────────────────────────────────────────────────────

def _soft_score(profile: dict[str, Any], engagement: float) -> int:
    score = 0

    # Engagement rate (mutually exclusive — higher tier wins)
    if engagement >= ENGAGEMENT_STRONG:
        score += 3
    elif engagement >= ENGAGEMENT_MODERATE:
        score += 2

    if profile.get("has_video_90d"):
        score += 2

    if profile.get("creator_mode"):
        score += 2

    # Follower bands (mutually exclusive — higher tier wins)
    followers = int(profile.get("followers", 0) or 0)
    if followers >= FOLLOWER_BAND_HIGH:
        score += 2
    elif followers >= FOLLOWER_BAND_MID:
        score += 1

    # Bio keywords — profiler already filtered to the spec-v2 list
    bio_signals = profile.get("bio_signals") or []
    score += min(3, len(bio_signals))

    # Medical/clinical content in post previews
    if _posts_are_medical(profile):
        score += 2

    # Posts regularly (>= 1/month on average over 90d → >= 3 posts)
    if int(profile.get("post_count_90d", 0) or 0) >= 3:
        score += 1

    return score


def _posts_are_medical(profile: dict[str, Any]) -> bool:
    """True iff at least one post preview in the last 90 days contains a
    medical keyword. Covers the "medical content, not just reshares of
    generic news or career posts" spec. The profiler already excludes
    reshares from post_previews_90d, so this is effectively a
    'is-original-post-medical' check.
    """
    previews = profile.get("post_previews_90d", []) or []
    if not previews:
        return False
    joined = " ".join(p for p in previews if p).lower()
    for kw in config.MEDICAL_KEYWORDS:
        if kw in joined:
            return True
    return False


# ── Ollama edge-case ─────────────────────────────────────────────────────────

def _call_ollama(profile: dict[str, Any],
                 ahpra_specialty: str,
                 engagement: float) -> dict | None:
    """Call local Ollama. Return {classification, confidence} on success,
    None on any error. Never raises.
    """
    url = os.environ.get("OLLAMA_URL", OLLAMA_URL_DEFAULT)
    if not url:
        return None

    payload_in = {
        "name":                profile.get("name", "") or "",
        "specialty":           ahpra_specialty or "",
        "recent_post_topics":  (profile.get("post_previews_90d") or [])[:10],
        "follower_count":      int(profile.get("followers", 0) or 0),
        "avg_likes":           float(profile.get("avg_likes_per_post", 0.0) or 0.0),
        "engagement_rate":     engagement,
        "has_video":           bool(profile.get("has_video_90d", False)),
        "bio_signals":         profile.get("bio_signals") or [],
    }

    prompt = (
        "This is a medical professional on LinkedIn. Based on their posting "
        "activity and engagement, would healthcare professionals consider them "
        "a trusted voice or active content creator in their field? They do not "
        "need a large following — consistent, relevant medical content with an "
        "engaged niche audience qualifies.\n\n"
        f"Candidate JSON: {json.dumps(payload_in, ensure_ascii=False)}\n\n"
        "Reply with ONLY a JSON object: "
        "{\"classification\": \"INFLUENCER\" | \"NOT\", "
        "\"confidence\": 0-1, \"reason\": \"one line\"}"
    )

    body = json.dumps({
        "model":   OLLAMA_MODEL,
        "prompt":  prompt,
        "format":  "json",
        "stream":  False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None
    except Exception:
        return None

    try:
        wrap = json.loads(raw)
    except Exception:
        return None
    inner = wrap.get("response", "")
    if not inner:
        return None

    try:
        parsed = json.loads(inner)
    except Exception:
        return None

    verdict = str(parsed.get("classification", "")).strip().upper()
    try:
        conf = float(parsed.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    if verdict == "INFLUENCER":
        return {"classification": "influencer", "confidence": conf}
    if verdict in ("NOT", "NOT_INFLUENCER", "NON_INFLUENCER"):
        return {"classification": "non_influencer", "confidence": conf}
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_iso_date(value: Any):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _schema_row(**kwargs) -> dict[str, Any]:
    """Build a v2 classification row with all CSV columns in locked order."""
    return {
        "practitioner_id":       kwargs.get("practitioner_id", ""),
        "linkedin_url":          kwargs.get("linkedin_url", ""),
        "classification":        "",
        "soft_score":            0,
        "hard_filters_passed":   False,
        "follower_count":        kwargs.get("follower_count", 0),
        "post_count_90d":        kwargs.get("post_count_90d", 0),
        "last_post_date":        kwargs.get("last_post_date"),
        "has_video_90d":         kwargs.get("has_video_90d", False),
        "creator_mode":          kwargs.get("creator_mode", False),
        "bio_signals":           kwargs.get("bio_signals", []),
        "classifier_source":     "heuristic",
        "classifier_confidence": None,
        "classified_at":         datetime.now().isoformat(timespec="seconds"),
        "fail_reason":           "",
        "engagement_rate":       kwargs.get("engagement_rate", 0.0),
    }
