"""
STEP 1a — Ingest AIHW MyHospitals reporting units.

The AIHW API (https://myhospitalsapi.aihw.gov.au/api/v1/reporting-units) returns
~1,427 reporting units. We filter to Type=Hospital, State=VIC and persist to
`data/hospitals_vic_raw.csv`.

AIHW does NOT include postcode directly — only lat/lon. We carry lat/lon through
and resolve postcodes in build_postcode_index.py using a nearest-centroid match.

Fields persisted:
  reporting_unit_code, name, private, closed, latitude, longitude,
  lhn_code, lhn_name, phn_code, phn_name
"""
from __future__ import annotations
import json
import sys

import requests

import config
from common import append_csv

AIHW_URL = "https://myhospitalsapi.aihw.gov.au/api/v1/reporting-units"
FIELDS = [
    "reporting_unit_code", "name", "private", "closed",
    "latitude", "longitude",
    "lhn_code", "lhn_name", "phn_code", "phn_name",
]


def fetch() -> list[dict]:
    r = requests.get(AIHW_URL, headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"}, timeout=config.HTTP_TIMEOUT_S)
    r.raise_for_status()
    payload = r.json()
    units = payload.get("result", [])
    return units


def extract_mapping(unit: dict, target_type_code: str) -> tuple[str, str]:
    """Return (code, name) for the first mapped unit matching target_type_code, else ('', '')."""
    for m in unit.get("mapped_reporting_units", []) or []:
        mapped = m.get("mapped_reporting_unit", {}) or {}
        t = (mapped.get("reporting_unit_type", {}) or {}).get("reporting_unit_type_code")
        if t == target_type_code:
            return mapped.get("reporting_unit_code", "") or "", mapped.get("reporting_unit_name", "") or ""
    return "", ""


def filter_vic_hospitals(units: list[dict]) -> list[dict]:
    rows = []
    for u in units:
        rut = (u.get("reporting_unit_type") or {}).get("reporting_unit_type_code")
        if rut != "H":
            continue
        state_code, _ = extract_mapping(u, "S")
        if state_code.lower() != "vic":
            continue
        if u.get("closed"):
            continue
        lhn_code, lhn_name = extract_mapping(u, "LHN")
        phn_code, phn_name = extract_mapping(u, "PHN")
        rows.append({
            "reporting_unit_code": u.get("reporting_unit_code", ""),
            "name": u.get("reporting_unit_name", ""),
            "private": bool(u.get("private")),
            "closed": bool(u.get("closed")),
            "latitude": u.get("latitude"),
            "longitude": u.get("longitude"),
            "lhn_code": lhn_code,
            "lhn_name": lhn_name,
            "phn_code": phn_code,
            "phn_name": phn_name,
        })
    return rows


def main():
    print(f"[aihw] fetching {AIHW_URL}")
    units = fetch()
    print(f"[aihw] got {len(units)} reporting units (all types, all states)")
    vic = filter_vic_hospitals(units)
    print(f"[aihw] filtered to {len(vic)} VIC hospitals (type=H, closed=False)")
    # Split public/private breakdown for sanity
    pub = sum(1 for r in vic if not r["private"])
    pri = sum(1 for r in vic if r["private"])
    print(f"[aihw]   public : {pub}")
    print(f"[aihw]   private: {pri}")
    # Overwrite (not append) — fresh snapshot each run
    if config.HOSPITALS_RAW_CSV.exists():
        config.HOSPITALS_RAW_CSV.unlink()
    append_csv(vic, config.HOSPITALS_RAW_CSV, fieldnames=FIELDS)
    print(f"[aihw] wrote {config.HOSPITALS_RAW_CSV}")

    # Sanity print: 5 well-known VIC tertiaries should be present
    names = {r["name"].lower() for r in vic}
    for probe in ["alfred", "royal melbourne", "monash medical centre", "royal children", "st vincent"]:
        hit = any(probe in n for n in names)
        print(f"[aihw] sanity: '{probe}' present? {'YES' if hit else 'no'}")


if __name__ == "__main__":
    main()
