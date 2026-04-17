# ─────────────────────────────────────────────────────────────────────────────
# verifier.py  –  Multi-signal match: name + location + medical + activity.
# Given a 30k lifetime connection cap, we bias hard toward precision.
# ─────────────────────────────────────────────────────────────────────────────
import re
from rapidfuzz import fuzz
import config


def _clean_name(name: str) -> str:
    """Strip titles (Dr, Prof, Mr, Ms) and collapse whitespace."""
    name = re.sub(r"\b(Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|A/Prof\.?|Assoc\.?)\b",
                  "", name, flags=re.IGNORECASE)
    return " ".join(name.split()).lower()


def name_matches(practitioner_name: str, linkedin_name: str) -> tuple[bool, int]:
    p = _clean_name(practitioner_name)
    l = _clean_name(linkedin_name)
    score = fuzz.token_set_ratio(p, l)
    return score >= config.NAME_MATCH_THRESHOLD, int(score)


_STATE_TOKENS = {
    "NSW": ["new south wales", "nsw"],
    "VIC": ["victoria", "vic"],
    "QLD": ["queensland", "qld"],
    "WA":  ["western australia", "wa"],
    "SA":  ["south australia", "sa"],
    "TAS": ["tasmania", "tas"],
    "NT":  ["northern territory", "nt"],
    "ACT": ["australian capital territory", "act"],
}


def location_matches(suburb: str, state: str, linkedin_location: str,
                     postcode: str = "") -> bool:
    """Return True iff the LinkedIn-listed location plausibly matches the
    AHPRA record. Looser than the original suburb-or-state rule to cover
    the common case of profiles that omit the state token.

    Order of checks (any one is enough):
      1. AHPRA suburb substring in the location
      2. State token (victoria, vic, etc.) present
      3. Melbourne / Greater Melbourne / etc. (VIC only)
      4. A VIC suburb from the allowlist in the location
      5. Location is effectively "Australia" AND AHPRA postcode is VIC
    """
    loc = (linkedin_location or "").strip().lower()
    if not loc:
        return False

    # 1. AHPRA suburb (practice address)
    if suburb and suburb.lower() in loc:
        return True

    # 2. Explicit state token
    state_tokens = _STATE_TOKENS.get(state.upper(), [state.lower()])
    if any(t in loc for t in state_tokens):
        return True

    st = (state or "").upper()

    # 3/4. VIC-specific fallbacks (only when we expect a VIC profile)
    if st == "VIC":
        for tok in config.VIC_CITY_TOKENS:
            if tok in loc:
                return True
        for sub in config.VIC_SUBURB_ALLOWLIST:
            if sub in loc:
                return True

    # 5. Only-"Australia" soft match when AHPRA postcode implies VIC
    pc = (postcode or "").strip()
    if pc.startswith(config.VIC_POSTCODE_PREFIX):
        compact = re.sub(r"[^a-z]", "", loc)
        if compact == "australia":
            return True

    return False


def headline_is_medical(headline: str) -> bool:
    hl = headline.lower()
    return any(kw in hl for kw in config.MEDICAL_KEYWORDS)


def _speciality_keywords(specialities: str) -> list[str]:
    """Given the practitioner's speciality string, return the keywords we'd
    expect to find in a matching LinkedIn headline."""
    out = set()
    sp = specialities.lower()
    for key, kws in config.SPECIALITY_KEYWORDS.items():
        if key.lower() in sp:
            out.update(kws)
    return list(out)


def headline_matches_speciality(specialities: str, headline: str) -> tuple[bool, list[str]]:
    """Returns (match, which_keywords_hit)."""
    kws = _speciality_keywords(specialities)
    if not kws:
        return False, []
    hl = headline.lower()
    hits = [k for k in kws if k in hl]
    return bool(hits), hits


def is_active_account(profile: dict) -> tuple[bool, str]:
    """Return (True, reason) if the card has signals of a real, active
    account; (False, why_rejected) otherwise."""
    if not profile.get("has_degree_badge"):
        return False, "no_degree_badge"
    if not profile.get("has_headline"):
        return False, "empty_headline"
    # action_button is "Connect" / "Message" / "Follow" / "Pending"
    if not profile.get("has_action_button"):
        return False, "no_action_button"
    return True, "ok"


def verify_profile(practitioner: dict, profile: dict) -> tuple[bool, str, str]:
    """
    Full verification pipeline — returns (is_match, reason, confidence).

    confidence is one of:
      "high"   — location matched normally (AHPRA suburb / state token / VIC
                 fallback list)
      "medium" — location was EMPTY but the name matched at >=
                 MEDIUM_CONF_NAME_SCORE (95). Downstream classifier should
                 apply stricter influencer gates to these.
      ""       — not a match (is_match == False)

    Profile dict must have: name, location, headline, has_degree_badge,
    has_headline, has_action_button.
    """
    name_ok, score = name_matches(practitioner["name"], profile.get("name", ""))
    if not name_ok:
        return False, f"name_mismatch (score={score})", ""

    confidence = "high"
    if config.REQUIRE_LOCATION_MATCH:
        loc = (profile.get("location", "") or "").strip()
        if not loc:
            # Empty-loc acceptance — only when the name match is very strong,
            # and only if the feature is enabled. Produces medium-confidence.
            if config.ACCEPT_EMPTY_LOCATION_WITH_STRONG_NAME and \
                    score >= config.MEDIUM_CONF_NAME_SCORE:
                confidence = "medium"
            else:
                return False, f"empty_location (score={score})", ""
        elif not location_matches(practitioner.get("suburb", ""),
                                  practitioner.get("state", ""),
                                  loc,
                                  postcode=practitioner.get("postcode", "")):
            return False, f"location_mismatch (score={score})", ""

    if config.REQUIRE_ACTIVE_ACCOUNT:
        alive, why = is_active_account(profile)
        if not alive:
            return False, f"dead_account:{why} (score={score})", ""

    if config.REQUIRE_MEDICAL_KEYWORD:
        if not headline_is_medical(profile.get("headline", "")):
            return False, f"no_medical_keyword (score={score})", ""

    if config.REQUIRE_SPECIALITY_MATCH and practitioner.get("specialities"):
        sp_ok, hits = headline_matches_speciality(
            practitioner["specialities"], profile.get("headline", "")
        )
        if not sp_ok:
            return False, \
                f"speciality_mismatch (score={score}, expected one of " \
                f"{_speciality_keywords(practitioner['specialities'])})", \
                ""

    sp_ok, hits = headline_matches_speciality(
        practitioner.get("specialities", ""), profile.get("headline", "")
    )
    boost = f", speciality_hits={hits}" if hits else ""
    return True, f"matched (name_score={score}, confidence={confidence}{boost})", confidence
