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


def _simplify_ahpra(name: str) -> str:
    """Reduce AHPRA's 'First [Middle…] Last' to 'First Last'.

    AHPRA stores the legal full name; LinkedIn shows the common (first+last)
    name. Comparing raw AHPRA to LinkedIn destroys the score on any legit
    match that has middle names. So we strip middles before scoring.
    """
    toks = _clean_name(name).split()
    if len(toks) <= 2:
        return " ".join(toks)
    return f"{toks[0]} {toks[-1]}"


def name_scores(practitioner_name: str, linkedin_name: str) -> tuple[int, int, int]:
    """Return (token_sort, token_set, token_delta).

    - token_sort is the primary signal (penalises extra LinkedIn tokens).
    - token_set is the secondary signal (more permissive; acts as a floor).
    - token_delta is |len(simplified_ahpra_tokens) - len(linkedin_tokens)|.
    """
    ahpra  = _simplify_ahpra(practitioner_name)
    linked = _clean_name(linkedin_name)
    sort_s = fuzz.token_sort_ratio(ahpra, linked)
    set_s  = fuzz.token_set_ratio(ahpra, linked)
    delta  = abs(len(ahpra.split()) - len(linked.split()))
    return int(sort_s), int(set_s), delta


def name_matches(practitioner_name: str, linkedin_name: str) -> tuple[bool, int, int, int]:
    """Hard gate for whether the names match at all. Returns
    (passes_gate, sort_score, set_score, token_delta).
    """
    sort_s, set_s, delta = name_scores(practitioner_name, linkedin_name)
    ok = (sort_s >= config.NAME_SORT_THRESHOLD
          and set_s >= config.NAME_SET_THRESHOLD
          and delta <= config.NAME_TOKEN_DELTA_MAX)
    return ok, sort_s, set_s, delta


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


def medical_signal_in_text(text: str, specialities: str) -> tuple[bool, str]:
    """Post-scrape medical-signal check for medium-confidence matches.

    `text` should combine everything we scraped — headline, bio/about,
    experience titles and companies. Returns (True, reason) on first match,
    (False, "no_signal") otherwise.

    Check order (cheapest first):
      1. Any config.MEDICAL_KEYWORDS substring
      2. Any SPECIALITY_KEYWORDS entry for the practitioner's speciality
      3. Verbatim AHPRA speciality string
      4. Any VIC_HOSPITAL_TOKENS entry
    """
    if not text:
        return False, "no_text"
    t = text.lower()

    for kw in config.MEDICAL_KEYWORDS:
        if kw in t:
            return True, f"medical_keyword:{kw}"

    for kw in _speciality_keywords(specialities):
        if kw in t:
            return True, f"speciality_keyword:{kw}"

    sp = (specialities or "").strip().lower()
    if sp and sp in t:
        return True, "speciality_verbatim"

    for hosp in config.VIC_HOSPITAL_TOKENS:
        if hosp in t:
            return True, f"hospital:{hosp}"

    return False, "no_signal"


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

    Three confidence tiers:
      "high"   — location matched normally + sort_score >= NAME_HIGH_CONF_SCORE
      "medium" — location was EMPTY, sort_score >= NAME_HIGH_CONF_SCORE, AND
                 the headline/speciality shows a medical signal
      ""       — rejected (includes "low": sort in [85, 95) range → reject today)

    Profile dict must have: name, location, headline, has_degree_badge,
    has_headline, has_action_button.
    """
    name_ok, sort_s, set_s, delta = name_matches(
        practitioner["name"], profile.get("name", "")
    )
    if not name_ok:
        return (
            False,
            f"name_mismatch (sort={sort_s}, set={set_s}, Δtok={delta})",
            "",
        )

    # Tier based on primary score. Sort in [85, 95) is "low" — rejected today;
    # kept in code as an explicit branch so future relaxations are one line.
    if sort_s < config.NAME_HIGH_CONF_SCORE:
        return (
            False,
            f"name_low_confidence (sort={sort_s}, set={set_s}, Δtok={delta})",
            "",
        )

    confidence = ""
    loc = (profile.get("location", "") or "").strip()

    if config.REQUIRE_LOCATION_MATCH:
        if loc:
            if not location_matches(practitioner.get("suburb", ""),
                                    practitioner.get("state", ""),
                                    loc,
                                    postcode=practitioner.get("postcode", "")):
                return (
                    False,
                    f"location_mismatch (sort={sort_s})",
                    "",
                )
            confidence = "high"
        elif config.ACCEPT_EMPTY_LOCATION_WITH_STRONG_NAME:
            # Empty-loc route: mark as medium-confidence. Medical-signal check
            # is DEFERRED to post-scrape (profile_profiler) so we can evaluate
            # against bio + experience, not just the sparse search-card
            # headline. Profiler downgrades to "low" on no signal → rejected.
            confidence = "medium"
        else:
            return False, f"empty_location (sort={sort_s})", ""
    else:
        confidence = "high"

    if config.REQUIRE_ACTIVE_ACCOUNT:
        alive, why = is_active_account(profile)
        if not alive:
            return False, f"dead_account:{why} (sort={sort_s})", ""

    if config.REQUIRE_MEDICAL_KEYWORD:
        if not headline_is_medical(profile.get("headline", "")):
            return False, f"no_medical_keyword (sort={sort_s})", ""

    if config.REQUIRE_SPECIALITY_MATCH and practitioner.get("specialities"):
        sp_ok, hits = headline_matches_speciality(
            practitioner["specialities"], profile.get("headline", "")
        )
        if not sp_ok:
            return False, f"speciality_mismatch (sort={sort_s})", ""

    _sp_ok, hits = headline_matches_speciality(
        practitioner.get("specialities", ""), profile.get("headline", "")
    )
    boost = f", speciality_hits={hits}" if hits else ""
    return (
        True,
        f"matched (sort={sort_s}, set={set_s}, Δtok={delta}, "
        f"confidence={confidence}{boost})",
        confidence,
    )
