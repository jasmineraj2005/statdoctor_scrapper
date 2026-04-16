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


def location_matches(suburb: str, state: str, linkedin_location: str) -> bool:
    loc = linkedin_location.lower()
    if not loc:
        return False
    state_tokens = _STATE_TOKENS.get(state.upper(), [state.lower()])
    suburb_match = bool(suburb) and suburb.lower() in loc
    state_match  = any(t in loc for t in state_tokens)
    return suburb_match or state_match


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


def verify_profile(practitioner: dict, profile: dict) -> tuple[bool, str]:
    """
    Full verification pipeline — returns (is_match, reason).
    Profile dict must have: name, location, headline, has_degree_badge,
    has_headline, has_action_button.
    """
    name_ok, score = name_matches(practitioner["name"], profile.get("name", ""))
    if not name_ok:
        return False, f"name_mismatch (score={score})"

    if config.REQUIRE_LOCATION_MATCH:
        if not location_matches(practitioner.get("suburb", ""),
                                practitioner.get("state", ""),
                                profile.get("location", "")):
            return False, f"location_mismatch (score={score})"

    if config.REQUIRE_ACTIVE_ACCOUNT:
        alive, why = is_active_account(profile)
        if not alive:
            return False, f"dead_account:{why} (score={score})"

    if config.REQUIRE_MEDICAL_KEYWORD:
        if not headline_is_medical(profile.get("headline", "")):
            return False, f"no_medical_keyword (score={score})"

    # Speciality booster: if configured AND practitioner has specialities,
    # require at least one matching keyword in the headline.
    if config.REQUIRE_SPECIALITY_MATCH and practitioner.get("specialities"):
        sp_ok, hits = headline_matches_speciality(
            practitioner["specialities"], profile.get("headline", "")
        )
        if not sp_ok:
            return False, f"speciality_mismatch (score={score}, expected one of {_speciality_keywords(practitioner['specialities'])})"

    # All gates passed
    sp_ok, hits = headline_matches_speciality(
        practitioner.get("specialities", ""), profile.get("headline", "")
    )
    boost = f", speciality_hits={hits}" if hits else ""
    return True, f"matched (name_score={score}{boost})"
