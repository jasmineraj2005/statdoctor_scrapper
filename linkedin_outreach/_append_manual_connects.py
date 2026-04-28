#!/usr/bin/env python3
"""One-off: append the 11 manually-sent connections to the 'Connections Sent'
Google Sheet tab.

The automated connector didn't fire these — they were sent by the user
manually outside the pipeline:
  - 4 from the 2026-04-24 reprofile_approved run (already 1st-degree by
    the time the script hit them, meaning the user had connected manually
    between the Day-1 scrape and re-profile).
  - 7 from the 2026-04-25 Day-2 run that the connector failed to send
    before the fixes landed; user sent manually after getting the URL list.

This is safe to re-run: we dedup by practitioner_id against whatever is
already in the tab.
"""
from __future__ import annotations

import csv
from datetime import datetime

import config
from sheets_logger import SheetsLogger, CONNECTIONS_SENT_HEADERS


MANUAL_SENDS = [
    # (practitioner_id, url_slug, sent_at_iso, note)
    ("MED0001936758", "dr-amireh-fakhouri",                  "2026-04-24 22:00:00", "reprofile_approved run — already connected"),
    ("MED0001175408", "dralanpaul",                          "2026-04-24 22:05:00", "reprofile_approved run — already connected"),
    ("MED0001623675", "alice-bergin-1939931b8",              "2026-04-24 22:10:00", "reprofile_approved run — already connected"),
    ("MED0001159524", "amanda-j-osborne",                    "2026-04-24 22:15:00", "reprofile_approved run — already connected (soft=3, Ollama non but user-approved)"),
    ("MED0002306438", "andrew-white-69253041",               "2026-04-25 12:00:00", "Day-2 manual — connector bug pre-fix"),
    ("MED0001857961", "anthony-zheng-ab9730b3",              "2026-04-25 12:00:00", "Day-2 manual — connector bug pre-fix"),
    ("MED0001783566", "adam-west-a8446ba",                   "2026-04-25 12:00:00", "Day-2 manual — connector bug pre-fix"),
    ("MED0000963125", "andrewcarter5",                       "2026-04-25 12:00:00", "Day-2 manual — connector bug pre-fix"),
    ("MED0002254411", "dr-abhilash-balakrishnan-1aa8b111",   "2026-04-25 12:00:00", "Day-2 manual — connector bug pre-fix"),
    ("MED0001154842", "adam-bystrzycki-9462ba1b",            "2026-04-25 12:00:00", "Day-2 manual — connector bug pre-fix"),
    ("MED0001181512", "andrew-davidson-5848b1224",           "2026-04-25 12:00:00", "Day-2 manual — connector bug pre-fix"),
    # Day-3 batch (2026-04-25 afternoon) — manual connects after URL list
    ("MED0001181811", "avi-charlton",                         "2026-04-25 16:00:00", "Day-3 manual — soft=11 strongest signal yet"),
    ("MED0001185743", "bradley-smith-4665441a9",              "2026-04-25 16:00:00", "Day-3 manual"),
    ("MED0001176335", "belinda-campbell-887a5242",            "2026-04-25 16:00:00", "Day-3 manual"),
    # NOT sent: MED0001204669 (Belinda Zhou) — user reviewed, not in medical field
    # Day-4 batch #3 (2026-04-26 evening) — connector errored "no send button found"
    # in modal; user confirmed both are 1st-degree on follow-up. Likely modal DOM
    # shift that caused our SEND_WITHOUT_NOTE_BUTTON selector to miss.
    ("MED0002163343", "dr-evrard-harris-980930218",            "2026-04-26 21:33:09", "Day-4 batch #3 — modal-Send selector miss; already 1st-degree on check"),
    ("MED0000977011", "ganeshnaidoo",                          "2026-04-26 21:33:36", "Day-4 batch #3 — modal-Send selector miss; already 1st-degree on check"),
]


def _load_classifications() -> dict[str, dict]:
    out = {}
    with open(config.CLASSIFICATIONS_CSV) as f:
        for r in csv.DictReader(f):
            out[r["practitioner_id"]] = r
    return out


def _load_subset_meta() -> dict[str, dict]:
    out = {}
    with open(config.INPUT_SUBSET_CSV) as f:
        for r in csv.DictReader(f):
            out[r["practitioner_id"]] = r
    return out


def run():
    logger = SheetsLogger()
    if logger.ws_connections is None:
        print("ERROR: Connections Sent tab not available.")
        return

    classifications = _load_classifications()
    subset_meta     = _load_subset_meta()

    # Dedup: read existing rows and skip any pid already present.
    try:
        existing_rows = logger.ws_connections.get_all_values()[1:]  # skip header
    except Exception:
        existing_rows = []
    # practitioner_id is column B (index 1) per CONNECTIONS_SENT_HEADERS.
    existing_pids = {row[1] for row in existing_rows if len(row) > 1}
    print(f"Existing rows in Connections Sent: {len(existing_rows)} "
          f"({len(existing_pids)} unique pids)")

    appended = 0
    skipped = 0
    for pid, slug, sent_at, _note in MANUAL_SENDS:
        if pid in existing_pids:
            print(f"  [skip] {pid} — already in sheet")
            skipped += 1
            continue
        c = classifications.get(pid, {})
        m = subset_meta.get(pid, {})
        row = [
            sent_at,
            pid,
            m.get("name", ""),
            m.get("speciality", ""),
            c.get("linkedin_url", f"https://www.linkedin.com/in/{slug}"),
            c.get("follower_count", ""),
            c.get("post_count_90d", ""),
            c.get("soft_score", ""),
            c.get("classifier_source", ""),
        ]
        try:
            logger.ws_connections.append_row(row, value_input_option="RAW")
            print(f"  [append] {pid} {m.get('name','')} — sent_at {sent_at}")
            appended += 1
        except Exception as e:
            print(f"  [error] {pid}: {e}")

    print(f"\nDone. Appended: {appended}  Skipped: {skipped}  "
          f"Total manual sends: {len(MANUAL_SENDS)}")


if __name__ == "__main__":
    run()
