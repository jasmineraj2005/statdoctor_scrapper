"""
Clean and merge all state practitioner CSVs into a single clean output.

Fixes:
- Double spaces in names and registration_type
- Drops empty 'division' and redundant 'profession' columns
- Drops scraping artifact 'postcode_searched'
- Splits 'location' into suburb, state, postcode
- Collapses multi-specialty rows into one row per practitioner
  (specialties joined with ' | ', registration types joined with ' | ')
"""

import csv
import os
import re
from collections import defaultdict

SCRAPER_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR      = os.path.join(SCRAPER_DIR, "..", "db_ARPHA")

STATE_FILES = {
    "NSW": "nsw_practitioners.csv",
    "VIC": "vic_practitioners.csv",
    "QLD": "qld_practitioners.csv",
    "SA":  "sa_practitioners.csv",
    "WA":  "wa_practitioners.csv",
    "TAS": "tas_practitioners.csv",
    "NT":  "nt_practitioners.csv",
}

OUTPUT_FILE = os.path.join(DB_DIR, "practitioners_clean.csv")

OUT_FIELDS = [
    "practitioner_id",
    "name",
    "registration_type",
    "specialities",
    "suburb",
    "state",
    "postcode",
]


def clean_whitespace(s: str) -> str:
    """Collapse multiple spaces and strip edges."""
    return re.sub(r" {2,}", " ", s).strip()


def parse_location(location: str):
    """
    Parse 'Suburb, STATE, 1234' → (suburb, state, postcode).
    Falls back gracefully if format is unexpected.
    """
    parts = [p.strip() for p in location.rsplit(",", 2)]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        return parts[0], parts[1], ""
    else:
        return location, "", ""


def clean_reg_type(reg: str) -> str:
    """Normalise registration type: fix double space, standardise."""
    reg = clean_whitespace(reg)
    # "Specialist  X" → "Specialist (X)"  — already handled by whitespace fix
    return reg


def load_all_rows():
    rows = []
    for state, fname in STATE_FILES.items():
        path = os.path.join(DB_DIR, fname)
        if not os.path.exists(path):
            print(f"  [SKIP] {fname} not found")
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append(r)
        print(f"  Loaded {fname}")
    return rows


def collapse(rows):
    """
    One row per practitioner_id.
    Collect all registration_types and specialities seen for that practitioner.
    Use the first occurrence for name/location (they're identical across rows).
    """
    by_id = defaultdict(list)
    for r in rows:
        by_id[r["practitioner_id"]].append(r)

    collapsed = []
    for pid, recs in by_id.items():
        first = recs[0]
        name = clean_whitespace(first["name"])
        location = first["location"]
        suburb, state, postcode = parse_location(location)

        reg_types_seen = []
        specialities_seen = []
        for r in recs:
            rt = clean_reg_type(r["registration_type"])
            sp = clean_whitespace(r["speciality"])
            if rt and rt not in reg_types_seen:
                reg_types_seen.append(rt)
            if sp and sp not in specialities_seen:
                specialities_seen.append(sp)

        collapsed.append({
            "practitioner_id":  pid,
            "name":             name,
            "registration_type": " | ".join(reg_types_seen),
            "specialities":     " | ".join(specialities_seen),
            "suburb":           suburb,
            "state":            state,
            "postcode":         postcode,
        })

    return collapsed


def main():
    print("Loading CSVs...")
    raw = load_all_rows()
    print(f"  Total raw rows: {len(raw)}")

    print("Collapsing multi-specialty rows...")
    clean = collapse(raw)
    print(f"  Unique practitioners: {len(clean)}")

    # Sort by state then practitioner_id for consistency
    clean.sort(key=lambda r: (r["state"], r["practitioner_id"]))

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(clean)

    print(f"\nDone → {OUTPUT_FILE}")

    # Summary
    from collections import Counter
    by_state = Counter(r["state"] for r in clean)
    print("\nPractitioners per state:")
    for state, count in sorted(by_state.items()):
        print(f"  {state}: {count}")

    reg_counts = Counter()
    for r in clean:
        for rt in r["registration_type"].split(" | "):
            if rt:
                reg_counts[rt] += 1
    print("\nTop registration types:")
    for rt, cnt in reg_counts.most_common(10):
        print(f"  {rt}: {cnt}")


if __name__ == "__main__":
    main()
