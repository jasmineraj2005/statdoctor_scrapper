"""
STEP 2 — Build postcode → candidate hospital-domains index.

Input:
  data/hospitals_vic.csv  (from resolve_domains.py)
  data/australian_postcodes.csv  (static centroid dataset)
  db_ARPHA/vic_practitioners.csv  (to know which postcodes to index)

Algorithm for each VIC postcode the practitioners live in:
  1. Compute postcode centroid as mean(lat, lon) over the suburbs on that postcode.
  2. Compute haversine distance from that centroid to every MX-valid hospital.
  3. Rank hospitals within 30 km, breaking ties by tier (tertiary > teaching > metro_public > rural_public > private > unknown).
  4. Keep top 8 per postcode.
  5. Group by tier so the caller can pick (e.g. for a cardiologist) tertiary before private.

Output:
  data/postcode_domains.json
    {
      "3004": {
        "centroid": [lat, lon],
        "candidates": [
          {"domain": "alfredhealth.org.au", "hospital": "The Alfred", "tier": "tertiary", "km": 0.3},
          ...
        ]
      }, ...
    }
"""
from __future__ import annotations
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import config
from common import read_csv

AU_POSTCODES_CSV = config.DATA_DIR / "australian_postcodes.csv"

TIER_ORDER = {
    "tertiary": 0,
    "metro_public": 1,
    "rural_public": 2,
    "private": 3,
    "unknown": 4,
}

MAX_CANDIDATES = 8
MAX_DISTANCE_KM = 30.0  # relax to 60 km for rural postcodes with no hospital nearby


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    a, b = math.radians(lat1), math.radians(lat2)
    dlat = b - a
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def load_vic_postcode_centroids() -> dict[str, tuple[float, float]]:
    """Mean lat/lon across all suburbs per VIC postcode."""
    lats, lons = defaultdict(list), defaultdict(list)
    with open(AU_POSTCODES_CSV, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if (row.get("state") or "").upper() != "VIC":
                continue
            pc = row.get("postcode", "").strip()
            if not pc or not pc.isdigit():
                continue
            try:
                lat = float(row["lat"])
                lon = float(row["long"])
            except (ValueError, KeyError):
                continue
            if lat == 0 or lon == 0:
                continue
            lats[pc].append(lat)
            lons[pc].append(lon)
    return {pc: (sum(lats[pc]) / len(lats[pc]), sum(lons[pc]) / len(lons[pc])) for pc in lats}


def load_practitioner_postcodes() -> set[str]:
    """All unique postcodes that appear in our VIC practitioner file.
    Prefers postcode_searched; falls back to parsing a 4-digit postcode from
    the location field (many rows have postcode_searched corrupted to 'AB' etc).
    """
    import re
    pc_re = re.compile(r"\b(\d{4})\b")
    pcs = set()
    with open(config.VIC_PRACTITIONERS_CSV, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            ps = (row.get("postcode_searched") or "").strip()
            if ps.isdigit() and len(ps) == 4:
                pcs.add(ps)
                continue
            loc = row.get("location") or ""
            m = pc_re.search(loc)
            if m:
                pcs.add(m.group(1))
    return pcs


def load_hospitals() -> list[dict]:
    hospitals = []
    for r in read_csv(config.HOSPITALS_CSV):
        if r["mx_ok"].lower() != "true":
            continue
        try:
            lat = float(r["latitude"])
            lon = float(r["longitude"])
        except (ValueError, KeyError):
            continue
        hospitals.append({
            "name": r["name"],
            "domain": r["domain"],
            "tier": r["tier"],
            "lat": lat,
            "lon": lon,
        })
    return hospitals


def build():
    centroids = load_vic_postcode_centroids()
    print(f"[postcode] VIC postcode centroids: {len(centroids)}")

    practitioner_pcs = load_practitioner_postcodes()
    print(f"[postcode] Practitioner postcodes to index: {len(practitioner_pcs)}")

    hospitals = load_hospitals()
    print(f"[postcode] MX-valid hospitals          : {len(hospitals)}")

    index = {}
    no_centroid = 0
    no_hospital_within_30 = 0
    for pc in sorted(practitioner_pcs):
        centroid = centroids.get(pc)
        if not centroid:
            no_centroid += 1
            continue
        cands = []
        for h in hospitals:
            km = haversine_km(centroid[0], centroid[1], h["lat"], h["lon"])
            cands.append((km, h))
        cands.sort(key=lambda t: (t[0], TIER_ORDER.get(t[1]["tier"], 99)))

        # Keep within MAX_DISTANCE_KM first; if none, relax to 60km for rural
        nearby = [c for c in cands if c[0] <= MAX_DISTANCE_KM]
        if not nearby:
            no_hospital_within_30 += 1
            nearby = [c for c in cands if c[0] <= 60.0]
        if not nearby:
            nearby = cands[:4]  # keep nearest-4 regardless of distance

        # Dedupe by domain (a health service can appear multiple times)
        seen = set()
        out_cands = []
        for km, h in nearby:
            if h["domain"] in seen:
                continue
            seen.add(h["domain"])
            out_cands.append({
                "domain": h["domain"],
                "hospital": h["name"],
                "tier": h["tier"],
                "km": round(km, 2),
            })
            if len(out_cands) >= MAX_CANDIDATES:
                break
        index[pc] = {
            "centroid": [round(centroid[0], 5), round(centroid[1], 5)],
            "candidates": out_cands,
        }

    print(f"[postcode] indexed {len(index)} postcodes")
    print(f"[postcode]   no-centroid (skipped): {no_centroid}")
    print(f"[postcode]   no-hospital-within-30km, relaxed to 60km: {no_hospital_within_30}")

    config.POSTCODE_DOMAINS_JSON.write_text(json.dumps(index, indent=2))
    print(f"[postcode] wrote {config.POSTCODE_DOMAINS_JSON}")
    return index


def sanity_spotcheck(index: dict):
    """Eyeball a few well-known postcodes."""
    print()
    print("SANITY SPOT-CHECK:")
    checks = [
        ("3004", "Alfred territory (Prahran)"),
        ("3050", "Royal Melbourne (Parkville)"),
        ("3052", "RCH / Royal Women's (Parkville)"),
        ("3000", "Melbourne CBD (multiple)"),
        ("3550", "Bendigo"),
        ("3690", "Albury-Wodonga"),
        ("3029", "Truganina / West (Werribee)"),
    ]
    for pc, label in checks:
        entry = index.get(pc)
        print(f"  {pc}  {label}")
        if not entry:
            print("    (no entry)")
            continue
        for c in entry["candidates"][:4]:
            print(f"    {c['km']:>6.2f}km  {c['tier']:<14} {c['domain']:<28}  {c['hospital']}")


if __name__ == "__main__":
    idx = build()
    sanity_spotcheck(idx)
