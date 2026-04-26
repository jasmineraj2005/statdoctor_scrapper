"""Central tunables for the email_enrichment pipeline.

State-aware: paths that depend on the practitioner state are exposed as
functions taking a state code (e.g. 'vic', 'nsw', 'qld', 'sa', 'wa', 'nt').
Cross-state caches (Disify log, domain formats, Halaxy sitemap) stay shared.
"""
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
DATA_DIR = THIS_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Default state for backward compat with scripts that haven't been parametrized yet
DEFAULT_STATE = os.environ.get("EE_STATE", "vic").lower()

# State-aware accessors -------------------------------------------------------
def state_lc(state: str | None = None) -> str:
    return (state or DEFAULT_STATE).lower()


def practitioners_csv(state: str | None = None) -> Path:
    return REPO_ROOT / "db_ARPHA" / f"{state_lc(state)}_practitioners.csv"


def enriched_csv(state: str | None = None) -> Path:
    return REPO_ROOT / "db_ARPHA" / f"{state_lc(state)}_practitioners_enriched.csv"


def hospitals_raw_csv(state: str | None = None) -> Path:
    return DATA_DIR / f"hospitals_{state_lc(state)}_raw.csv"


def hospitals_csv(state: str | None = None) -> Path:
    return DATA_DIR / f"hospitals_{state_lc(state)}.csv"


def postcode_domains_json(state: str | None = None) -> Path:
    return DATA_DIR / f"postcode_domains_{state_lc(state)}.json"


def gp_practices_csv(state: str | None = None) -> Path:
    return DATA_DIR / f"gp_practices_{state_lc(state)}.csv"


def gp_clinic_domains_json(state: str | None = None) -> Path:
    return DATA_DIR / f"gp_clinic_domains_{state_lc(state)}.json"


# LinkedIn artefacts are state-aware too (LinkedIn agent has been VIC-only so far).
def linkedin_subset_csv(state: str | None = None) -> Path:
    return REPO_ROOT / "linkedin_outreach" / "data" / f"{state_lc(state)}_high_yield_subset.csv"


def linkedin_classifications_csv(state: str | None = None) -> Path:
    return REPO_ROOT / "linkedin_outreach" / "data" / f"{state_lc(state)}_linkedin_classifications.csv"


# Cross-state shared caches ---------------------------------------------------
DOMAIN_FORMATS_JSON   = DATA_DIR / "domain_formats.json"        # shared
HALAXY_SITEMAP_JSON   = DATA_DIR / "halaxy_sitemap_index.json"  # shared (national)
SMTP_PROBE_LOG_CSV    = DATA_DIR / "smtp_probe_log.csv"         # legacy SMTP
DISIFY_PROBE_LOG_CSV  = DATA_DIR / "disify_probe_log.csv"       # shared (cross-state email cache)
CANARY_CACHE_JSON     = DATA_DIR / "canary_cache.json"          # SMTP-era cache

# ── Backwards-compatible aliases (default to VIC) ────────────────────────────
# Older scripts reference these constants directly. Will be removed once all
# call sites use the state-aware accessors.
VIC_PRACTITIONERS_CSV       = practitioners_csv("vic")
LINKEDIN_SUBSET_CSV         = linkedin_subset_csv("vic")
LINKEDIN_CLASSIFICATIONS_CSV = linkedin_classifications_csv("vic")
HOSPITALS_RAW_CSV           = hospitals_raw_csv("vic")
HOSPITALS_CSV               = hospitals_csv("vic")
POSTCODE_DOMAINS_JSON       = postcode_domains_json("vic")
GP_PRACTICES_CSV            = gp_practices_csv("vic")
ENRICHED_CSV                = enriched_csv("vic")

# ── SMTP ─────────────────────────────────────────────────────────────────────
HELO_DOMAIN = os.environ.get("FREEDNS_HELO_DOMAIN", "arpha-probe.jumpingcrab.com")
MAIL_FROM = os.environ.get("PROBE_MAIL_FROM", f"probe@{HELO_DOMAIN}")
SMTP_TIMEOUT_S = 15
SMTP_PROBES_PER_DOMAIN_PER_DAY = 200
SMTP_JITTER_S = (3, 8)

# ── HTTP scraping ────────────────────────────────────────────────────────────
HTTP_TIMEOUT_S = 20
HTTP_JITTER_S = (2, 5)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/123.0.0.0 Safari/537.36"
)

# ── Confidence tiers (write these to the enriched CSV) ───────────────────────
CONF_VERIFIED = "verified"
CONF_CATCH_ALL = "catch_all"
CONF_UNVERIFIED = "unverified"
CONF_FAILED = "failed"
CONF_IP_BLOCKED = "ip_blocked"
CONF_NA = "n_a"

# ── Specialty → preferred hospital-tier ranking ──────────────────────────────
SPECIALTY_AFFINITY = {
    "Cardiology": ["tertiary", "teaching"],
    "Physician, Cardiology": ["tertiary", "teaching"],
    "General paediatrics": ["childrens", "tertiary"],
    "Paediatrics and child health, General paediatrics": ["childrens", "tertiary"],
    "Obstetrics and gynaecology": ["womens", "tertiary"],
    "Emergency medicine": ["tertiary", "teaching", "community"],
    "Anaesthesia": ["tertiary", "teaching"],
    "Radiology, Diagnostic radiology": ["tertiary", "private_radiology", "teaching"],
    "Surgery, General surgery": ["tertiary", "teaching"],
    "Surgery, Orthopaedic surgery": ["tertiary", "teaching", "private"],
    "Psychiatry": ["psychiatric", "tertiary", "community"],
    "Physician, Geriatric medicine": ["community", "teaching", "tertiary"],
    "General practice": ["community", "gp_private"],
    "": ["tertiary", "teaching", "community"],
}
DEFAULT_TIER_ORDER = ["tertiary", "teaching", "community", "specialty", "private"]
