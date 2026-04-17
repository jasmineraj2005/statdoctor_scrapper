"""Build the VIC high-yield subset for LinkedIn outreach.

Heuristic (locked by spec):
  - registration_type contains "Specialist" (includes Specialist GP)
  - AND postcode_searched in top-50 VIC postcodes by row count
  - EXCLUDE registration_type in {Non Practising, Limited, Provisional}

Output: linkedin_outreach/data/vic_high_yield_subset.csv
Columns: practitioner_id, name, speciality, postcode_searched, location
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_CSV = REPO_ROOT / "db_ARPHA" / "vic_practitioners.csv"
OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_CSV = OUT_DIR / "vic_high_yield_subset.csv"

EXCLUDED_TYPES = {"Non Practising", "Limited", "Provisional"}
TOP_N_POSTCODES = 50


def build() -> pd.DataFrame:
    if not SRC_CSV.exists():
        sys.exit(f"source CSV not found: {SRC_CSV}")

    df = pd.read_csv(SRC_CSV, dtype=str).fillna("")
    total = len(df)

    excluded = df["registration_type"].isin(EXCLUDED_TYPES)
    df = df[~excluded]

    specialist_mask = df["registration_type"].str.contains(
        "Specialist", case=False, na=False
    )
    df = df[specialist_mask]

    top_postcodes = (
        df["postcode_searched"].value_counts().head(TOP_N_POSTCODES).index.tolist()
    )
    df = df[df["postcode_searched"].isin(top_postcodes)]

    subset = df[
        ["practitioner_id", "name", "speciality", "postcode_searched", "location"]
    ].copy()
    subset = subset.drop_duplicates(subset=["practitioner_id"]).reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    subset.to_csv(OUT_CSV, index=False)

    print(f"source rows:         {total}")
    print(f"after exclusions:    {total - int(excluded.sum())}")
    print(f"top-{TOP_N_POSTCODES} postcodes retained: {len(top_postcodes)}")
    print(f"subset rows written: {len(subset)}")
    print(f"wrote {OUT_CSV.relative_to(REPO_ROOT)}")
    return subset


if __name__ == "__main__":
    build()
