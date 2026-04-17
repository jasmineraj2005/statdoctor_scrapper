# ─────────────────────────────────────────────────────────────────────────────
# config.py  –  All tuneable parameters in one place
# ─────────────────────────────────────────────────────────────────────────────
import os

# ── Paths (relative to repo root, one level up from this folder) ─────────────
ROOT_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THIS_DIR      = os.path.dirname(os.path.abspath(__file__))

INPUT_CSV     = os.path.join(ROOT_DIR, "db_ARPHA", "practitioners_clean.csv")
OUTPUT_LOG    = os.path.join(ROOT_DIR, "outreach_log.csv")
PROGRESS_FILE = os.path.join(ROOT_DIR, "linkedin_progress.txt")
COOKIES_FILE  = os.path.join(THIS_DIR, "linkedin_cookies.json")

# ── Google Sheets ─────────────────────────────────────────────────────────────
# Setup steps:
#   1. console.cloud.google.com → New project
#   2. Enable "Google Sheets API" and "Google Drive API"
#   3. IAM → Service Accounts → Create → download JSON
#   4. Save JSON to linkedin_outreach/credentials/gsheet_creds.json
#   5. Share your Google Sheet with the service account email (Editor access)
GSHEET_CREDENTIALS_FILE = os.path.join(THIS_DIR, "credentials", "gsheet_creds.json")
GSHEET_SPREADSHEET_NAME = "LinkedIn Outreach Tracker"   # exact name of your Google Sheet
GSHEET_WORKSHEET_NAME   = "Outreach"                    # tab name inside the sheet

# ── Targeting filters (applied before any LinkedIn activity) ──────────────────
# Empty list = include all. Add values to restrict.
TARGET_STATES        = ["VIC"]              # e.g. ["NSW", "VIC"] — empty = all states
TARGET_SPECIALTIES   = []                   # e.g. ["Surgery"] — substring match on specialities
EXCLUDE_REG_TYPES    = ["Non Practising"]   # skip these registration types

# ── LinkedIn search ───────────────────────────────────────────────────────────
SEARCH_QUERY_TEMPLATE = "{clean_name}"
MAX_PROFILES_TO_CHECK = 5     # inspect up to N results per search before giving up

# LinkedIn geoUrn IDs — narrow search server-side by location.
# 101452733 = Australia (country); sub-region IDs change, so country-level is the safest default.
SEARCH_GEO_URNS = ["101452733"]    # [] = no location filter (worldwide)

# ── Profile verification ──────────────────────────────────────────────────────
# Given a 30k LIFETIME connection cap per LinkedIn account, bias hard toward
# precision. A missed match is cheap (24k practitioners remain), a false positive
# costs one of our finite invites.
NAME_MATCH_THRESHOLD     = 82    # rapidfuzz token_set_ratio 0-100
REQUIRE_LOCATION_MATCH   = True  # profile location must contain suburb or state
REQUIRE_ACTIVE_ACCOUNT   = True  # skip profiles that look abandoned
REQUIRE_MEDICAL_KEYWORD  = False # profile headline must mention a medical term
REQUIRE_SPECIALITY_MATCH = False # profile headline must mention the practitioner's speciality

# Locations that LinkedIn shows without a state but that plausibly mean VIC.
# Many profiles list just "Melbourne" or "Greater Melbourne Area" with no
# "Victoria" token — earlier verifier rejected those, so we accept them.
VIC_CITY_TOKENS = [
    "melbourne",
    "greater melbourne",
    "greater melbourne area",
    "melbourne, australia",
]

# VIC suburb allowlist: hand-curated inner-metro + the top-50-postcode suburbs
# from data/vic_high_yield_subset.csv. Used for substring match against the
# LinkedIn-listed location when the profile omits the state token.
VIC_SUBURB_ALLOWLIST = {
    # hand-curated inner-metro (spec-locked)
    "footscray", "parkville", "south yarra", "fitzroy", "carlton",
    "richmond", "hawthorn", "prahran", "bentleigh", "kew", "toorak",
    "brighton", "caulfield",
    # derived from top-50 postcodes in the subset
    "ashburton", "ashwood", "bangholme", "bendigo", "berwick", "blackburn",
    "blackburn north", "blackburn south", "botanic ridge", "briar hill",
    "brunswick", "bundoora", "burnside", "cairnlea", "camberwell",
    "carnegie", "caroline springs", "caulfield south", "clifton hill",
    "coburg", "coburg north", "collingwood", "cranbourne", "dandenong",
    "dandenong north", "deer park", "dennington", "doncaster east",
    "east melbourne", "elsternwick", "fairfield", "fitzroy north",
    "flora hill", "gardenvale", "glen huntly", "glen waverley",
    "greensborough", "harkaway", "heathmont", "hoppers crossing",
    "ironbark", "ivanhoe", "ivanhoe east", "kennington", "langwarrin",
    "mitcham", "moonee ponds", "mornington", "mount waverley",
    "murrumbeena", "narre warren", "narre warren south", "noble park",
    "noble park north", "north melbourne", "pakenham", "point cook",
    "preston", "ravenhall", "ringwood", "ringwood east", "ringwood north",
    "ripponlea", "rowville", "royal melbourne hospital", "sandhurst",
    "skye", "south melbourne", "st helena", "sunshine", "sunshine north",
    "sunshine west", "tarneit", "truganina", "wangaratta", "wantirna",
    "wantirna south", "warranwood", "warrnambool", "werribee",
    "west wodonga", "wheelers hill", "windsor", "wodonga",
}

# When the profile lists only "Australia" (no city/state), accept as a soft
# match if the AHPRA postcode is VIC (3xxx). Low-confidence fallback.
VIC_POSTCODE_PREFIX = "3"
MEDICAL_KEYWORDS         = [
    "doctor", "medical", "physician", "gp", "surgeon", "specialist",
    "registrar", "consultant", "mbbs", "md", "anaesth", "oncol",
    "cardiolog", "radiolog", "psychiatr", "paediatr",
]

# Map AHPRA speciality substrings → keywords we'd expect to see in a LinkedIn
# headline. Used as a verification booster: when the practitioner has this
# speciality and the profile headline contains one of these keywords, match
# confidence rises.
SPECIALITY_KEYWORDS = {
    "General practice":   ["gp", "general practitioner", "family medicine", "general practice"],
    "Surgery":            ["surgeon", "surgery", "surgical"],
    "General surgery":    ["general surgeon", "general surgery"],
    "Vascular surgery":   ["vascular"],
    "Orthopaedic":        ["orthopaed", "orthoped", "orthopaedic surgeon"],
    "Cardio":             ["cardiol", "cardiac", "cardiologist"],
    "Dermatolog":         ["dermatol", "skin", "derm"],
    "Anaesthes":          ["anaesth", "anesth"],
    "Psychiatr":          ["psychiatr", "mental health"],
    "Radiolog":           ["radiolog"],
    "Pathology":          ["patholog"],
    "Paediatr":           ["paediatr", "pediatr", "children"],
    "Emergency":          ["emergency", "ed ", "ed,"],
    "Obstetrics":         ["obstetr", "ob/gyn", "gynaecol"],
    "Gynaecology":        ["gynaecol", "gyn"],
    "Oncology":           ["oncol"],
    "Ophthalmology":      ["ophthalm", "eye surgeon"],
    "Neurology":          ["neurolog"],
    "Neurosurgery":       ["neurosurg"],
    "Urology":            ["urolog"],
    "Endocrinology":      ["endocrinol"],
    "Rheumatology":       ["rheumatol"],
    "Gastroenterology":   ["gastroenterol"],
    "Intensive care":     ["intensive care", "icu"],
    "Rehabilitation":     ["rehab"],
    "Sport":              ["sport"],
    "Public health":      ["public health"],
    "Occupational":       ["occupational"],
}

# ── Connection message ────────────────────────────────────────────────────────
# Max 300 characters. {first_name} is replaced at runtime.
CONNECTION_NOTE = (
    "Hi {first_name}, I'd love to connect! I run a medical staffing agency and "
    "think there could be some mutual value in staying in touch."
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
MAX_CONNECTIONS_PER_SESSION  = 40    # hard stop for a single script run
MAX_CONNECTIONS_PER_DAY      = 80    # checked against outreach_log.csv on startup
MAX_CONNECTIONS_PER_WEEK     = 250   # LinkedIn Premium weekly ceiling (with safety buffer)

DELAY_BETWEEN_SEARCHES_SEC    = (10, 25)     # between each new LinkedIn search
DELAY_BETWEEN_PROFILES_SEC    = (3,  8)      # between inspecting profiles in results
DELAY_BETWEEN_CONNECTIONS_SEC = (20, 50)     # after sending a connection request
DELAY_AFTER_TYPING_SEC        = (0.05, 0.15) # per character typed (human-like cadence)

SESSION_BREAK_EVERY_N         = 15           # longer break every N connections sent
SESSION_BREAK_DURATION_SEC    = (120, 300)   # 2–5 min break

# ── Browser ───────────────────────────────────────────────────────────────────
HEADLESS       = False   # keep False — visible browser is harder to detect as a bot
BROWSER_WIDTH  = 1280
BROWSER_HEIGHT = 900
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# ── Dry run ───────────────────────────────────────────────────────────────────
# True = searches + verifies but does NOT click Send on any connection request
DRY_RUN = False
