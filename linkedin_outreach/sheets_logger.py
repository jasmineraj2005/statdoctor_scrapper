# ─────────────────────────────────────────────────────────────────────────────
# sheets_logger.py  –  Google Sheets live tracking
#
# Two generations:
#
#   1. LEGACY Outreach tab (HEADERS / add_pending / update / already_processed /
#      count_sent_today/week). Used by the plain pre-classifier pipeline.
#
#   2. STEP-7 three-tab pipeline (log_classification / set_stage /
#      update_connect_status / already_classified). Source of truth is three
#      local CSVs (classifications, processing_status, plus the existing
#      outreach log); Sheets is a mirror.
#
# ─────────────────────────────────────────────────────────────────────────────
import csv
import os
from datetime import datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials

import config


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "practitioner_id", "name", "suburb", "state", "specialty",
    "linkedin_url", "status", "timestamp", "notes",
]

# Classifications CSV columns — locked by ROADMAP §"File I/O contract".
CLASSIFICATIONS_HEADERS = [
    "practitioner_id", "linkedin_url", "classification", "soft_score",
    "hard_filters_passed", "follower_count", "post_count_90d", "last_post_date",
    "has_video_90d", "creator_mode", "bio_signals", "classifier_source",
    "classifier_confidence", "classified_at", "fail_reason",
]

# Processing-status CSV: per-practitioner pipeline stage (upsert).
STATUS_CSV_HEADERS = [
    "practitioner_id", "name", "pipeline_stage", "last_updated",
]

# Influencers-VIC sheet tab columns.
INFLUENCERS_SHEET_HEADERS = [
    "practitioner_id", "name", "speciality", "postcode", "linkedin_url",
    "follower_count", "post_count_90d", "has_video", "soft_score",
    "classifier_source", "connect_status", "connect_sent_at", "last_checked",
]

# Reviewed-skipped sheet tab columns.
SKIPPED_SHEET_HEADERS = [
    "practitioner_id", "name", "linkedin_url", "fail_reason",
    "follower_count", "last_post_date", "checked_date",
]

# Processing-status sheet tab columns.
STATUS_SHEET_HEADERS = list(STATUS_CSV_HEADERS)


# Status values — legacy (Outreach tab) + pipeline stages.
STATUS_PENDING           = "pending"
STATUS_SENT              = "sent"
STATUS_NOT_FOUND         = "not_found"
STATUS_NAME_MISMATCH     = "name_mismatch"
STATUS_LOCATION_MISMATCH = "location_mismatch"
STATUS_ALREADY_CONNECTED = "already_connected"
STATUS_CONNECT_UNAVAIL   = "connect_unavailable"
STATUS_SKIPPED           = "skipped"
STATUS_ERROR             = "error"

# Pipeline stages for Processing Status (per user spec).
STAGE_PENDING    = "pending"
STAGE_SEARCHED   = "searched"
STAGE_PROFILED   = "profiled"
STAGE_CLASSIFIED = "classified"
STAGE_CONNECTED  = "connected"
STAGE_SKIPPED    = "skipped"
STAGE_NOT_FOUND  = "not_found"
STAGE_ERROR      = "error"

TERMINAL_STAGES = frozenset({
    STAGE_CONNECTED, STAGE_SKIPPED, STAGE_NOT_FOUND, STAGE_ERROR,
})


class SheetsLogger:
    def __init__(self):
        # Legacy Outreach tab state
        self.ws            = None
        self._row_cache    = {}     # practitioner_id → Outreach-tab row number

        # Step-7 tabs + CSV indices
        self.ws_influencers      = None
        self.ws_skipped          = None
        self.ws_status           = None
        self._influencer_rows    = {}   # practitioner_id → Influencers-VIC row number
        self._status_rows        = {}   # practitioner_id → Status-tab row number
        self._classified_ids     = set()  # dedup — loaded from classifications.csv
        self._status_stage_cache = {}     # practitioner_id → last stage (CSV source-of-truth)

        self._connect_sheets()
        self._ensure_local_log()
        self._ensure_classifications_csv()
        self._ensure_status_csv()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _connect_sheets(self):
        if not os.path.exists(config.GSHEET_CREDENTIALS_FILE):
            print("[sheets] WARNING: credentials file not found — Google Sheets logging disabled.")
            print(f"[sheets] Expected at: {config.GSHEET_CREDENTIALS_FILE}")
            return
        try:
            creds  = Credentials.from_service_account_file(config.GSHEET_CREDENTIALS_FILE, scopes=SCOPES)
            client = gspread.authorize(creds)
            sheet  = client.open(config.GSHEET_SPREADSHEET_NAME)
            self._sheet = sheet

            # Legacy tab
            try:
                self.ws = sheet.worksheet(config.GSHEET_WORKSHEET_NAME)
                self._sync_row_cache()
            except gspread.exceptions.WorksheetNotFound:
                print(f"[sheets] Legacy Outreach tab missing — legacy logging disabled.")

            # New tabs — create if missing
            self.ws_influencers = self._get_or_create_tab(
                sheet, config.GSHEET_INFLUENCERS_TAB, INFLUENCERS_SHEET_HEADERS)
            self.ws_skipped = self._get_or_create_tab(
                sheet, config.GSHEET_SKIPPED_TAB, SKIPPED_SHEET_HEADERS)
            self.ws_status = self._get_or_create_tab(
                sheet, config.GSHEET_STATUS_TAB, STATUS_SHEET_HEADERS)

            self._sync_influencer_rows()
            self._sync_status_rows()

            print(f"[sheets] Connected to '{config.GSHEET_SPREADSHEET_NAME}' — "
                  f"Outreach={bool(self.ws)} Influencers={bool(self.ws_influencers)} "
                  f"Skipped={bool(self.ws_skipped)} Status={bool(self.ws_status)}")
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"[sheets] ERROR: Spreadsheet '{config.GSHEET_SPREADSHEET_NAME}' not found.")
            print("[sheets] Create it and share it with your service account email.")
        except Exception as e:
            print(f"[sheets] ERROR connecting: {e}")

    def _get_or_create_tab(self, sheet, title: str, headers: list[str]):
        """Return the worksheet for `title`; create it (with header row) if missing."""
        try:
            ws = sheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            try:
                ws = sheet.add_worksheet(title=title, rows=1000, cols=max(len(headers), 10))
                ws.append_row(headers, value_input_option="RAW")
                print(f"[sheets] Created missing tab: {title}")
            except Exception as e:
                print(f"[sheets] ERROR creating tab {title}: {e}")
                return None
        # Ensure header row is present even on pre-existing tabs.
        try:
            if not ws.row_values(1):
                ws.append_row(headers, value_input_option="RAW")
        except Exception:
            pass
        return ws

    def _ensure_local_log(self):
        if not os.path.exists(config.OUTPUT_LOG):
            with open(config.OUTPUT_LOG, "w", newline="") as f:
                csv.writer(f).writerow(HEADERS)

    def _ensure_classifications_csv(self):
        path = config.CLASSIFICATIONS_CSV
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(CLASSIFICATIONS_HEADERS)
            return
        # Prime dedup set from any pre-existing rows.
        with open(path, "r", newline="") as f:
            for row in csv.DictReader(f):
                pid = row.get("practitioner_id", "")
                if pid:
                    self._classified_ids.add(pid)

    def _ensure_status_csv(self):
        path = config.PROCESSING_STATUS_CSV
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(STATUS_CSV_HEADERS)
            return
        # Prime stage cache for resume semantics.
        with open(path, "r", newline="") as f:
            for row in csv.DictReader(f):
                pid = row.get("practitioner_id", "")
                stage = row.get("pipeline_stage", "")
                if pid:
                    self._status_stage_cache[pid] = stage

    def _sync_row_cache(self):
        if not self.ws:
            return
        try:
            records = self.ws.get_all_values()
            for i, row in enumerate(records[1:], start=2):
                if row and row[0]:
                    self._row_cache[row[0]] = i
        except Exception as e:
            print(f"[sheets] WARNING: could not sync Outreach row cache: {e}")

    def _sync_influencer_rows(self):
        if not self.ws_influencers:
            return
        try:
            records = self.ws_influencers.get_all_values()
            for i, row in enumerate(records[1:], start=2):
                if row and row[0]:
                    self._influencer_rows[row[0]] = i
        except Exception as e:
            print(f"[sheets] WARNING: could not sync Influencers row cache: {e}")

    def _sync_status_rows(self):
        if not self.ws_status:
            return
        try:
            records = self.ws_status.get_all_values()
            for i, row in enumerate(records[1:], start=2):
                if row and row[0]:
                    self._status_rows[row[0]] = i
        except Exception as e:
            print(f"[sheets] WARNING: could not sync Status row cache: {e}")

    def _ensure_header(self):
        if not self.ws:
            return
        try:
            existing = self.ws.row_values(1)
            if not existing:
                self.ws.append_row(HEADERS, value_input_option="RAW")
        except Exception:
            pass

    # ── LEGACY Outreach tab API ───────────────────────────────────────────────

    def add_pending(self, practitioner: dict):
        row = self._build_row(practitioner, STATUS_PENDING, "", "")
        self._write_row(practitioner["practitioner_id"], row)

    def update(self, practitioner_id: str, status: str, linkedin_url: str = "", notes: str = ""):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._update_local_csv(practitioner_id, status, linkedin_url, notes, timestamp)
        if not self.ws:
            return
        try:
            if practitioner_id in self._row_cache:
                row_num = self._row_cache[practitioner_id]
                self.ws.update(
                    f"G{row_num}:I{row_num}",
                    [[status, timestamp, notes]],
                    value_input_option="RAW",
                )
                if linkedin_url:
                    self.ws.update_cell(row_num, 7, linkedin_url)
        except Exception as e:
            print(f"[sheets] WARNING: could not update Outreach row for {practitioner_id}: {e}")

    # ── STEP-7 new-pipeline API ───────────────────────────────────────────────

    def set_stage(self, practitioner: dict, stage: str) -> None:
        """Upsert the Processing Status row for `practitioner` to `stage`.

        CSV is source of truth; sheet mirrors. Cheap to call many times — the
        per-practitioner row is overwritten in place rather than appended.
        """
        pid = practitioner.get("practitioner_id", "") or ""
        if not pid:
            return
        self._status_stage_cache[pid] = stage
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [pid, practitioner.get("name", ""), stage, now]

        self._upsert_status_csv(row)

        if not self.ws_status:
            return
        try:
            if pid in self._status_rows:
                rn = self._status_rows[pid]
                self.ws_status.update(
                    f"A{rn}:D{rn}", [row], value_input_option="RAW",
                )
            else:
                self.ws_status.append_row(row, value_input_option="RAW")
                self._status_rows[pid] = len(self.ws_status.get_all_values())
        except Exception as e:
            print(f"[sheets] WARNING: could not upsert Status row for {pid}: {e}")

    def log_classification(self,
                           practitioner: dict,
                           profile: dict,
                           classification: dict) -> None:
        """Append classifications.csv row + dispatch to Influencers VIC or
        Reviewed Skipped sheet tabs.

        - `practitioner` has AHPRA fields (practitioner_id, name, speciality,
          postcode_searched, location, …). Use this for sheet-side joins.
        - `profile` is the profile_profiler output (follower_count, etc.).
        - `classification` is the influencer_classifier output.
        """
        pid = practitioner.get("practitioner_id", "") or ""
        if not pid:
            return

        self._append_classifications_csv(classification)
        self._classified_ids.add(pid)

        verdict = classification.get("classification", "")
        if verdict == "influencer":
            self._write_influencer_row(practitioner, profile, classification)
        else:
            self._write_skipped_row(practitioner, profile, classification)

    def update_connect_status(self,
                              practitioner_id: str,
                              connect_status: str,
                              detail: str = "") -> None:
        """Patch an existing Influencers VIC row with connect outcome.

        detail is appended to connect_sent_at as a suffix when useful.
        """
        if not self.ws_influencers:
            return
        if practitioner_id not in self._influencer_rows:
            # Not an influencer, or Influencers VIC not yet written — no-op.
            return
        rn = self._influencer_rows[practitioner_id]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sent_at = now if connect_status == STATUS_SENT else (detail or "")
        try:
            # Columns: K=connect_status, L=connect_sent_at, M=last_checked
            self.ws_influencers.update(
                f"K{rn}:M{rn}",
                [[connect_status, sent_at, now]],
                value_input_option="RAW",
            )
        except Exception as e:
            print(f"[sheets] WARNING: could not update Influencers row for {practitioner_id}: {e}")

    def already_classified(self, practitioner_id: str) -> bool:
        """True iff this practitioner has a classification row already."""
        return practitioner_id in self._classified_ids

    def get_stage(self, practitioner_id: str) -> str:
        return self._status_stage_cache.get(practitioner_id, "")

    # ── Internal: row builders ────────────────────────────────────────────────

    def _build_row(self, practitioner: dict, status: str, linkedin_url: str, notes: str) -> list:
        return [
            practitioner.get("practitioner_id", ""),
            practitioner.get("name", ""),
            practitioner.get("suburb", ""),
            practitioner.get("state", ""),
            practitioner.get("specialities", ""),
            linkedin_url,
            status,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            notes,
        ]

    def _write_row(self, practitioner_id: str, row: list):
        """Append to legacy Outreach sheet and local CSV."""
        with open(config.OUTPUT_LOG, "a", newline="") as f:
            csv.writer(f).writerow(row)
        if not self.ws:
            return
        try:
            self._ensure_header()
            self.ws.append_row(row, value_input_option="RAW")
            total_rows = len(self.ws.get_all_values())
            self._row_cache[practitioner_id] = total_rows
        except Exception as e:
            print(f"[sheets] WARNING: could not append row: {e}")

    def _update_local_csv(self, practitioner_id, status, linkedin_url, notes, timestamp):
        if not os.path.exists(config.OUTPUT_LOG):
            return
        rows = []
        updated = False
        with open(config.OUTPUT_LOG, "r", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0] == practitioner_id:
                    row[5] = linkedin_url or row[5]
                    row[6] = status
                    row[7] = timestamp
                    row[8] = notes
                    updated = True
                rows.append(row)
        if updated:
            with open(config.OUTPUT_LOG, "w", newline="") as f:
                csv.writer(f).writerows(rows)

    # ── Internal: classifications CSV ─────────────────────────────────────────

    def _append_classifications_csv(self, classification: dict) -> None:
        row = [
            classification.get("practitioner_id", ""),
            classification.get("linkedin_url", ""),
            classification.get("classification", ""),
            classification.get("soft_score", 0),
            classification.get("hard_filters_passed", False),
            classification.get("follower_count", 0),
            classification.get("post_count_90d", 0),
            classification.get("last_post_date", "") or "",
            classification.get("has_video_90d", False),
            classification.get("creator_mode", False),
            "|".join(classification.get("bio_signals", []) or []),
            classification.get("classifier_source", ""),
            "" if classification.get("classifier_confidence") is None
               else classification.get("classifier_confidence"),
            classification.get("classified_at", ""),
            classification.get("fail_reason", "") or "",
        ]
        with open(config.CLASSIFICATIONS_CSV, "a", newline="") as f:
            csv.writer(f).writerow(row)

    # ── Internal: processing-status CSV ───────────────────────────────────────

    def _upsert_status_csv(self, row: list) -> None:
        """Rewrite status CSV with this row upserted by practitioner_id."""
        pid = row[0]
        path = config.PROCESSING_STATUS_CSV
        rows = []
        found = False
        if os.path.exists(path):
            with open(path, "r", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, STATUS_CSV_HEADERS)
                rows.append(header)
                for r in reader:
                    if r and r[0] == pid:
                        rows.append(row)
                        found = True
                    else:
                        rows.append(r)
        else:
            rows.append(STATUS_CSV_HEADERS)
        if not found:
            rows.append(row)
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)

    # ── Internal: Influencers VIC + Reviewed Skipped sheet writes ─────────────

    def _write_influencer_row(self,
                              practitioner: dict,
                              profile: dict,
                              classification: dict) -> None:
        if not self.ws_influencers:
            return
        pid = practitioner.get("practitioner_id", "")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            pid,
            practitioner.get("name", ""),
            practitioner.get("speciality", "") or practitioner.get("specialities", ""),
            practitioner.get("postcode_searched", "") or practitioner.get("postcode", ""),
            classification.get("linkedin_url", ""),
            classification.get("follower_count", 0),
            classification.get("post_count_90d", 0),
            classification.get("has_video_90d", False),
            classification.get("soft_score", 0),
            classification.get("classifier_source", ""),
            "",    # connect_status — filled by update_connect_status
            "",    # connect_sent_at
            now,   # last_checked
        ]
        try:
            if pid in self._influencer_rows:
                rn = self._influencer_rows[pid]
                self.ws_influencers.update(
                    f"A{rn}:M{rn}", [row], value_input_option="RAW",
                )
            else:
                self.ws_influencers.append_row(row, value_input_option="RAW")
                self._influencer_rows[pid] = len(self.ws_influencers.get_all_values())
        except Exception as e:
            print(f"[sheets] WARNING: could not write Influencers row for {pid}: {e}")

    def _write_skipped_row(self,
                           practitioner: dict,
                           profile: dict,
                           classification: dict) -> None:
        if not self.ws_skipped:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        row = [
            practitioner.get("practitioner_id", ""),
            practitioner.get("name", ""),
            classification.get("linkedin_url", ""),
            classification.get("fail_reason", "") or classification.get("classification", ""),
            classification.get("follower_count", 0),
            classification.get("last_post_date", "") or "",
            today,
        ]
        try:
            self.ws_skipped.append_row(row, value_input_option="RAW")
        except Exception as e:
            print(f"[sheets] WARNING: could not write Skipped row for "
                  f"{practitioner.get('practitioner_id','')}: {e}")

    # ── LEGACY analytics (outreach_log.csv) ───────────────────────────────────

    def count_sent_today(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        if not os.path.exists(config.OUTPUT_LOG):
            return 0
        with open(config.OUTPUT_LOG, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("status") == STATUS_SENT and row.get("timestamp", "").startswith(today):
                    count += 1
        return count

    def count_sent_this_week(self) -> int:
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        count = 0
        if not os.path.exists(config.OUTPUT_LOG):
            return 0
        with open(config.OUTPUT_LOG, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("status") == STATUS_SENT:
                    ts = row.get("timestamp", "")[:10]
                    if ts >= cutoff:
                        count += 1
        return count

    def already_processed(self, practitioner_id: str) -> bool:
        """Legacy dedup — true iff practitioner has a terminal Outreach row."""
        if not os.path.exists(config.OUTPUT_LOG):
            return False
        with open(config.OUTPUT_LOG, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("practitioner_id") == practitioner_id:
                    return row.get("status", "") != STATUS_PENDING
        return False
