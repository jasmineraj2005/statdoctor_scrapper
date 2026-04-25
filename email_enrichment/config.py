"""Central tunables for the email_enrichment pipeline."""
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
DATA_DIR = THIS_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

VIC_PRACTITIONERS_CSV = REPO_ROOT / "db_ARPHA" / "vic_practitioners.csv"
LINKEDIN_SUBSET_CSV = REPO_ROOT / "linkedin_outreach" / "data" / "vic_high_yield_subset.csv"
LINKEDIN_CLASSIFICATIONS_CSV = REPO_ROOT / "linkedin_outreach" / "data" / "vic_linkedin_classifications.csv"

# Outputs
HOSPITALS_RAW_CSV = DATA_DIR / "hospitals_vic_raw.csv"
HOSPITALS_CSV = DATA_DIR / "hospitals_vic.csv"
POSTCODE_DOMAINS_JSON = DATA_DIR / "postcode_domains.json"
DOMAIN_FORMATS_JSON = DATA_DIR / "domain_formats.json"
GP_PRACTICES_CSV = DATA_DIR / "gp_practices.csv"
SMTP_PROBE_LOG_CSV  = DATA_DIR / "smtp_probe_log.csv"     # legacy SMTP (backup)
DISIFY_PROBE_LOG_CSV = DATA_DIR / "disify_probe_log.csv"  # active verifier
CANARY_CACHE_JSON = DATA_DIR / "canary_cache.json"
ENRICHED_CSV = REPO_ROOT / "db_ARPHA" / "vic_practitioners_enriched.csv"

# ── SMTP ─────────────────────────────────────────────────────────────────────
HELO_DOMAIN = os.environ.get("FREEDNS_HELO_DOMAIN", "arpha-probe.jumpingcrab.com")
MAIL_FROM = os.environ.get("PROBE_MAIL_FROM", f"probe@{HELO_DOMAIN}")
SMTP_TIMEOUT_S = 15
SMTP_PROBES_PER_DOMAIN_PER_DAY = 200
SMTP_JITTER_S = (3, 8)  # seconds between probes on same domain

# ── HTTP scraping ────────────────────────────────────────────────────────────
HTTP_TIMEOUT_S = 20
HTTP_JITTER_S = (2, 5)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/123.0.0.0 Safari/537.36"
)

# ── Confidence tiers (write these to the enriched CSV) ───────────────────────
CONF_VERIFIED = "verified"       # canary 550, target 250
CONF_CATCH_ALL = "catch_all"     # canary 250 → domain accepts anything
CONF_UNVERIFIED = "unverified"   # probe errored / HELO rejected / timeout
CONF_FAILED = "failed"           # target 550 with "user unknown" reason
CONF_IP_BLOCKED = "ip_blocked"   # 550 due to Spamhaus/PBL/IP reputation — no mailbox info
CONF_NA = "n_a"                  # not attempted (e.g. LinkedIn pipeline)

# ── Specialty → preferred hospital-tier ranking ──────────────────────────────
# Used in STEP 2 to order candidate domains per practitioner.
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
    "": ["tertiary", "teaching", "community"],  # unknown specialty
}
DEFAULT_TIER_ORDER = ["tertiary", "teaching", "community", "specialty", "private"]
