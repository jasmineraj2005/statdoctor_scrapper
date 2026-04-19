"""influencer_classifier.py — gate the connect queue on "is this a medical
influencer whose content resonates with healthcare professionals".

Consumes a profile dict from `profile_profiler.profile()`. Produces a
classification dict matching the `data/vic_linkedin_classifications.csv`
schema locked in ROADMAP.md §"File I/O contract". Two decision tiers:

  1. Heuristic  — hard filters (followers / posts / recency / avg likes),
                  then a soft score. Clean pass → influencer; clean fail →
                  non_influencer.
  2. Ollama     — soft score sits in the ambiguous band → ask the locally
                  hosted llama3.2:3b for a JSON verdict. Ollama is called
                  via stdlib `urllib.request` (no new deps). If Ollama is
                  unreachable / slow / returns garbage, the classifier
                  defaults to `non_influencer`. NEVER block on Ollama.

Decision branches (per spec):

  verifier_confidence="high"
    hard_fail              → non_influencer (heuristic)
    hard_pass, soft ≥ 3    → influencer     (heuristic)
    hard_pass, soft 1–2    → Ollama         (edge-case)
    hard_pass, soft 0      → non_influencer (heuristic)

  verifier_confidence="medium" (spec: threshold raises to 5)
    hard_fail              → non_influencer (heuristic)
    hard_pass, soft ≥ 5    → influencer     (heuristic)
    hard_pass, soft 1–4    → Ollama         (edge-case; band expanded vs
                                             high-conf — same "ambiguous →
                                             ask Ollama" intent at the
                                             higher medium threshold)
    hard_pass, soft 0      → non_influencer (heuristic)

A profile with `fail_reason` from the profiler (nav error, downgraded
medium-no-signal, etc.) is short-circuited to `error` or `not_found` —
we do not attempt to classify an incomplete scrape.

Output schema (CSV columns, locked):
    practitioner_id, linkedin_url, classification, soft_score,
    hard_filters_passed, follower_count, post_count_90d, last_post_date,
    has_video_90d, creator_mode, bio_signals, classifier_source,
    classifier_confidence, classified_at, fail_reason
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any

import config


# ── Constants ────────────────────────────────────────────────────────────────

# Ollama local HTTP endpoint. Overridable via env so tests can point at a
# stub server, and so the classifier can be disabled by setting it empty.
OLLAMA_URL_DEFAULT = "http://localhost:11434/api/generate"
OLLAMA_MODEL       = "llama3.2:3b"
OLLAMA_TIMEOUT_SEC = 25


# Hard filters (spec-locked)
HF_FOLLOWERS_MIN     = 1500
HF_POSTS_90D_MIN     = 4
HF_LAST_POST_DAYS    = 60
HF_AVG_LIKES_MIN     = 15

# Soft-score thresholds
SOFT_THRESHOLD_HIGH_CONF   = 3   # high-conf row passes
SOFT_OLLAMA_BAND_HIGH      = (1, 2)   # inclusive, high-conf edge case


# ── Public API ───────────────────────────────────────────────────────────────

def classify(profile: dict[str, Any],
             practitioner_id: str = "",
             ahpra_specialty: str = "") -> dict[str, Any]:
    """Run hard filters + soft score → heuristic-or-Ollama decision.

    Returns a dict matching the CSV schema. Pure function modulo the
    optional Ollama HTTP call (which is guarded against every error mode).
    """
    url           = profile.get("url", "") or ""
    verif_conf    = profile.get("verifier_confidence", "") or ""
    profiler_fail = profile.get("fail_reason", "") or ""

    out = _schema_row(
        practitioner_id=practitioner_id,
        linkedin_url=url,
        follower_count=int(profile.get("followers", 0) or 0),
        post_count_90d=int(profile.get("post_count_90d", 0) or 0),
        last_post_date=profile.get("last_post_date"),
        has_video_90d=bool(profile.get("has_video_90d", False)),
        creator_mode=bool(profile.get("creator_mode", False)),
        bio_signals=list(profile.get("bio_signals") or []),
    )

    # Short-circuit on profiler failure. No classification attempted.
    if profiler_fail:
        # medium_no_medical_signal is the deferred-check downgrade — it is a
        # non-match, not a "profile page broken" error. Treat as non_influencer
        # with the reason preserved so sheets/CSV stay informative.
        if profiler_fail == "medium_no_medical_signal":
            out["classification"]        = "non_influencer"
            out["classifier_source"]     = "heuristic"
            out["fail_reason"]           = profiler_fail
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
    soft = _soft_score(profile)
    out["soft_score"] = soft

    # Tier-specific threshold and Ollama band
    if verif_conf == "medium":
        pass_thresh    = config.MEDIUM_CONF_CLASSIFIER_SOFT_SCORE  # 5
        ollama_band_lo = 1
        ollama_band_hi = pass_thresh - 1                           # 1..4
    else:
        # high-conf, or empty verifier_confidence (treat conservatively as high)
        pass_thresh    = SOFT_THRESHOLD_HIGH_CONF                  # 3
        ollama_band_lo, ollama_band_hi = SOFT_OLLAMA_BAND_HIGH     # 1..2

    if soft >= pass_thresh:
        out["classification"]    = "influencer"
        out["classifier_source"] = "heuristic"
        return out

    if soft == 0:
        out["classification"]    = "non_influencer"
        out["classifier_source"] = "heuristic"
        return out

    # Ambiguous band → ask Ollama. On any failure, fall through to non_influencer.
    if ollama_band_lo <= soft <= ollama_band_hi:
        verdict = _call_ollama(profile, ahpra_specialty)
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
        return out

    # Defensive: any score outside defined branches → non_influencer.
    # This path shouldn't trigger with the soft-score calculator, but if
    # thresholds change we'd rather under-connect than misclassify.
    out["classification"]    = "non_influencer"
    out["classifier_source"] = "heuristic"
    out["fail_reason"]       = f"soft_score_out_of_bands:{soft}"
    return out


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
    # datetime.now() is fine — recency rounding is on day boundary.
    age_days = (datetime.now().date() - last_dt).days
    if age_days > HF_LAST_POST_DAYS:
        return False, f"last_post>{HF_LAST_POST_DAYS}d"

    avg_likes = float(profile.get("avg_likes_per_post", 0.0) or 0.0)
    if avg_likes < HF_AVG_LIKES_MIN:
        return False, f"avg_likes<{HF_AVG_LIKES_MIN}"

    return True, ""


# ── Soft score ───────────────────────────────────────────────────────────────

def _soft_score(profile: dict[str, Any]) -> int:
    score = 0

    if profile.get("has_video_90d"):
        score += 2

    if profile.get("creator_mode"):
        score += 2

    # bio keywords already filtered to the spec list by the profiler
    bio_signals = profile.get("bio_signals") or []
    score += min(3, len(bio_signals))

    followers = int(profile.get("followers", 0) or 0)
    # Bands are mutually exclusive. 5k–10k inclusive; >10k takes the higher tier.
    if followers > 10_000:
        score += 4
    elif 5_000 <= followers <= 10_000:
        score += 2

    return score


# ── Ollama edge-case ─────────────────────────────────────────────────────────

def _call_ollama(profile: dict[str, Any], ahpra_specialty: str) -> dict | None:
    """Call local Ollama. Return {classification, confidence} on success,
    None on any error. Never raises.

    `classification` in the returned dict is normalised to the CSV vocabulary
    ("influencer" / "non_influencer").
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
        "has_video":           bool(profile.get("has_video_90d", False)),
        "bio_signals":         profile.get("bio_signals") or [],
    }

    prompt = (
        "You are classifying whether a medical professional's LinkedIn account "
        "is a MEDICAL INFLUENCER whose content would resonate with healthcare "
        "professionals.\n\n"
        f"Candidate JSON: {json.dumps(payload_in, ensure_ascii=False)}\n\n"
        "Reply with ONLY a JSON object: "
        "{\"classification\": \"INFLUENCER\" | \"NOT\", "
        "\"confidence\": 0-1, \"reason\": \"one short line\"}"
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

    # Ollama /api/generate returns {"response": "...model output...", ...}
    try:
        wrap = json.loads(raw)
    except Exception:
        return None
    inner = wrap.get("response", "")
    if not inner:
        return None

    # The model was asked for JSON; format=json on the Ollama side forces it.
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
    # Unknown verbiage — treat as unparseable.
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_iso_date(value: Any):
    """Accept ISO date ('2026-04-15') or datetime str. Return a date or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _schema_row(**kwargs) -> dict[str, Any]:
    """Build a classification row with all CSV columns in locked order."""
    return {
        "practitioner_id":       kwargs.get("practitioner_id", ""),
        "linkedin_url":          kwargs.get("linkedin_url", ""),
        "classification":        "",        # filled by caller
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
    }
