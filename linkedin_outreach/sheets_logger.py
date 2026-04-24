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

# Classifications CSV columns — v2 schema (v1 + engagement_rate).
# Locked by ROADMAP §"File I/O contract".
CLASSIFICATIONS_HEADERS = [
    "practitioner_id", "linkedin_url", "classification", "soft_score",
    "hard_filters_passed", "follower_count", "post_count_90d", "last_post_date",
    "has_video_90d", "creator_mode", "bio_signals", "classifier_source",
    "classifier_confidence", "classified_at", "fail_reason", "engagement_rate",
]

# Processing-status CSV: per-practitioner pipeline stage (upsert). Step-7b
# adds a `detail` column — the last event's one-line reason/outcome so the
# client can see WHY a row is in that stage without opening Live Run Log.
STATUS_CSV_HEADERS = [
    "practitioner_id", "name", "pipeline_stage", "detail", "last_updated",
]

# Step-7b live event log — one row per pipeline event, appended in real time.
# Column count MUST match the user-facing schema locked in the Day-1 spec.
LIVE_LOG_HEADERS = [
    "timestamp", "practitioner_id", "name", "speciality", "linkedin_url",
    "event", "outcome", "detail", "daily_connect_count",
]

# Known event types. Kept as constants so mis-spellings in callers fail at
# lint time rather than silently corrupting the sheet.
EVENT_SEARCHED                = "searched"
EVENT_NOT_FOUND               = "not_found"
EVENT_PROFILED                = "profiled"
EVENT_CLASSIFIED              = "classified"
EVENT_CONNECT_SENT            = "connect_sent"
EVENT_CONNECT_FAILED          = "connect_failed"
EVENT_SKIPPED_HOT             = "skipped_hot"
EVENT_SKIPPED_NON_INFLUENCER  = "skipped_non_influencer"
EVENT_SKIPPED_CAP_REACHED     = "skipped_cap_reached"

OUTCOME_SUCCESS  = "success"
OUTCOME_FAIL     = "fail"
OUTCOME_SKIPPED  = "skipped"
OUTCOME_PENDING  = "pending"

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

# Connections Sent tab — clean feed of successful connect requests only.
# Populated from update_connect_status whenever status == STATUS_SENT.
CONNECTIONS_SENT_HEADERS = [
    "sent_at", "practitioner_id", "name", "speciality", "linkedin_url",
    "follower_count", "post_count_90d", "soft_score", "classifier_source",
]


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

        # Step-7b live-reporting state
        self.ws_live      = None
        self.ws_summary   = None
        self.ws_connections = None    # v2.1 — Connections Sent tab
        self._send_cap    = 0      # set by set_send_cap; used for "N/cap" column
        self._sends_today_session = 0  # count of connect_sent events this run

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

            # Step-7b tabs
            self.ws_live = self._get_or_create_tab(
                sheet, config.GSHEET_LIVE_TAB, LIVE_LOG_HEADERS)
            self.ws_summary = self._get_or_create_summary_tab(sheet)

            # v2.1 Connections Sent tab
            self.ws_connections = self._get_or_create_tab(
                sheet, config.GSHEET_CONNECTIONS_TAB, CONNECTIONS_SENT_HEADERS)

            self._sync_influencer_rows()
            self._sync_status_rows()

            print(f"[sheets] Connected to '{config.GSHEET_SPREADSHEET_NAME}' — "
                  f"Outreach={bool(self.ws)} Influencers={bool(self.ws_influencers)} "
                  f"Skipped={bool(self.ws_skipped)} Status={bool(self.ws_status)} "
                  f"Live={bool(self.ws_live)} Summary={bool(self.ws_summary)}")
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

    def _get_or_create_summary_tab(self, sheet):
        """Summary tab is 6 labelled cells — not a tabular header. Create
        with the label column + an empty value column when first provisioned."""
        title = config.GSHEET_SUMMARY_TAB
        try:
            ws = sheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            try:
                ws = sheet.add_worksheet(title=title, rows=8, cols=2)
                ws.update("A1:B6", [
                    ["Run date",                         ""],
                    ["Total processed today",            "0"],
                    ["Connects sent today / daily cap",  "0/0"],
                    ["Total influencers found (all time)", "0"],
                    ["Total connects sent (all time)",   "0"],
                    ["Last updated",                     ""],
                ], value_input_option="RAW")
                print(f"[sheets] Created missing tab: {title}")
            except Exception as e:
                print(f"[sheets] ERROR creating Summary tab: {e}")
                return None
        return ws

    def _ensure_local_log(self):
        if not os.path.exists(config.OUTPUT_LOG):
            with open(config.OUTPUT_LOG, "w", newline="") as f:
                csv.writer(f).writerow(HEADERS)

    def _ensure_classifications_csv(self):
        path = config.CLASSIFICATIONS_CSV
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Schema migration: if an existing file's header doesn't match the
        # current CLASSIFICATIONS_HEADERS (e.g. post v1→v2 upgrade), rename
        # it to <path>.v1.bak so we never mix schemas in-place. User can
        # diff or merge by hand after the run.
        if os.path.exists(path):
            try:
                with open(path, "r", newline="") as f:
                    first = next(csv.reader(f), [])
            except Exception:
                first = []
            if first and first != CLASSIFICATIONS_HEADERS:
                bak = f"{path}.v1.bak"
                # If bak already exists, append a numeric suffix so we never
                # clobber an earlier migration artifact.
                i = 1
                while os.path.exists(bak):
                    bak = f"{path}.v1.bak.{i}"
                    i += 1
                os.rename(path, bak)
                print(f"[sheets] classifications schema changed — migrated old "
                      f"CSV to {bak}; starting fresh at {path}")

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

        # Schema migration for step-7b `detail` column. Old 4-col files get
        # renamed to .v1.bak and we start fresh — the alternative of padding
        # in-place would silently mutate an existing ops artifact.
        if os.path.exists(path):
            try:
                with open(path, "r", newline="") as f:
                    first = next(csv.reader(f), [])
            except Exception:
                first = []
            if first and first != STATUS_CSV_HEADERS:
                bak = f"{path}.v1.bak"
                i = 1
                while os.path.exists(bak):
                    bak = f"{path}.v1.bak.{i}"
                    i += 1
                os.rename(path, bak)
                print(f"[sheets] processing_status schema changed — migrated "
                      f"old CSV to {bak}; starting fresh at {path}")

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

    # ── Step-7b — live event log + status + summary ───────────────────────────

    def set_send_cap(self, cap: int) -> None:
        """Caller registers the effective send cap for the current session so
        Live Run Log's `daily_connect_count` column can format as `N/cap`.
        """
        self._send_cap = max(int(cap or 0), 0)
        self._sends_today_session = 0

    def log_live_event(self, *,
                       practitioner: dict | None = None,
                       practitioner_id: str = "",
                       name: str = "",
                       speciality: str = "",
                       linkedin_url: str = "",
                       event: str = "",
                       outcome: str = "",
                       detail: str = "") -> None:
        """Append one row to the Live Run Log tab. Never raises — sheet
        failures log a warning and continue; one retry on transient failures.

        Pass either a `practitioner` dict OR the individual string fields
        (callers embedded in searcher/classifier/etc. usually have the
        dict; main.py's skipped paths have just the id).
        """
        if practitioner:
            practitioner_id = practitioner_id or practitioner.get("practitioner_id", "") or ""
            name            = name or practitioner.get("name", "") or ""
            speciality      = speciality or practitioner.get("speciality", "") or practitioner.get("specialities", "") or ""

        if event == EVENT_CONNECT_SENT:
            self._sends_today_session += 1
        cap_cell = f"{self._sends_today_session}/{self._send_cap}" if self._send_cap else str(self._sends_today_session)

        row = [
            datetime.now().isoformat(timespec="seconds"),
            practitioner_id,
            name,
            speciality,
            linkedin_url,
            event,
            outcome,
            detail[:300] if detail else "",
            cap_cell,
        ]

        if not self.ws_live:
            return
        # Single retry on transient gspread errors — covers momentary network
        # flaps without masking persistent misconfigurations.
        for attempt in (1, 2):
            try:
                self.ws_live.append_row(row, value_input_option="RAW")
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[sheets] WARNING: log_live_event failed (retry 1/1): {e}")
                    return

        # Refresh Summary on terminal events to keep the tab usable at a glance.
        if event in (EVENT_CONNECT_SENT, EVENT_CONNECT_FAILED):
            self._refresh_summary()

    def update_status(self, practitioner_id: str, stage: str,
                      detail: str = "", name: str = "") -> None:
        """Upsert the Processing Status row with stage + detail."""
        if not practitioner_id:
            return
        # If caller didn't supply a name but we already have a row, preserve it.
        if not name and practitioner_id in self._status_rows and self.ws_status:
            try:
                existing = self.ws_status.row_values(self._status_rows[practitioner_id])
                if len(existing) >= 2:
                    name = existing[1]
            except Exception:
                pass

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [practitioner_id, name, stage, (detail or "")[:300], now]
        self._status_stage_cache[practitioner_id] = stage
        self._upsert_status_csv(row)

        if not self.ws_status:
            return
        for attempt in (1, 2):
            try:
                if practitioner_id in self._status_rows:
                    rn = self._status_rows[practitioner_id]
                    self.ws_status.update(f"A{rn}:E{rn}", [row], value_input_option="RAW")
                else:
                    self.ws_status.append_row(row, value_input_option="RAW")
                    self._status_rows[practitioner_id] = len(self.ws_status.get_all_values())
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[sheets] WARNING: update_status failed for {practitioner_id}: {e}")
                    return

    def _refresh_summary(self) -> None:
        """Recompute + overwrite the 6-cell Summary tab.

        Sourced from the existing CSVs + session counter rather than querying
        sheets — cheaper and avoids a read/write round-trip per event.
        """
        if not self.ws_summary:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Count today's rows in Live Run Log (by timestamp prefix) — cheap
        # proxy for "total processed today" which covers every pipeline event.
        processed_today = self._sends_today_session  # at minimum this session
        influencers_all_time = 0
        connects_all_time    = 0

        # Influencers: classifications.csv rows with classification=influencer
        try:
            with open(config.CLASSIFICATIONS_CSV, "r", newline="") as f:
                for r in csv.DictReader(f):
                    if r.get("classification", "") == "influencer":
                        influencers_all_time += 1
        except Exception:
            pass

        # Connects: sum of sent rows in the legacy outreach_log.csv + this
        # session's sends. Legacy log receives nothing in the new pipeline,
        # so in practice this matches "sent-events-observed-this-session"
        # until we start running real-mode multi-day.
        try:
            if os.path.exists(config.OUTPUT_LOG):
                with open(config.OUTPUT_LOG, "r", newline="") as f:
                    for r in csv.DictReader(f):
                        if r.get("status", "") == STATUS_SENT:
                            connects_all_time += 1
        except Exception:
            pass
        # Plus this session (only relevant if caller hasn't also mirrored to
        # outreach_log.csv — the new pipeline doesn't, so we add explicitly).
        connects_all_time += self._sends_today_session

        # Processed today: live log rows whose timestamp starts with `today`.
        try:
            if self.ws_live:
                all_live = self.ws_live.get_all_values()
                processed_today = sum(
                    1 for r in all_live[1:]
                    if r and r[0].startswith(today)
                )
        except Exception:
            pass

        cap_txt = f"{self._sends_today_session}/{self._send_cap}" if self._send_cap else str(self._sends_today_session)
        rows = [
            ["Run date",                           today],
            ["Total processed today",              processed_today],
            ["Connects sent today / daily cap",    cap_txt],
            ["Total influencers found (all time)", influencers_all_time],
            ["Total connects sent (all time)",     connects_all_time],
            ["Last updated",                       now],
        ]
        try:
            self.ws_summary.update("A1:B6", rows, value_input_option="RAW")
        except Exception as e:
            print(f"[sheets] WARNING: _refresh_summary failed: {e}")

    def set_stage(self, practitioner: dict, stage: str, detail: str = "") -> None:
        """Upsert the Processing Status row for `practitioner` to `stage`.

        Thin wrapper over update_status — kept so existing callers
        (main.py, step-7-era tests) don't need to rewire to the dict-less
        signature. CSV is source of truth; sheet mirrors. Optional `detail`
        kwarg lets callers attach an explanation when they have one.
        """
        pid = (practitioner or {}).get("practitioner_id", "") or ""
        if not pid:
            return
        self.update_status(pid, stage,
                           detail=detail,
                           name=(practitioner or {}).get("name", ""))

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

        # Mirror successful connects into the Connections Sent tab. This gives
        # the client a clean, append-only feed of actual connect requests sent,
        # independent of Influencers VIC (which keeps every classification).
        if connect_status == STATUS_SENT and self.ws_connections is not None:
            self._append_connection_sent(practitioner_id, now)

    def _append_connection_sent(self, practitioner_id: str, sent_at: str) -> None:
        """Append one row to the Connections Sent tab. Pulls name/speciality/
        metrics from the Influencers VIC row we just updated. Fire-and-forget."""
        try:
            rn = self._influencer_rows.get(practitioner_id)
            if not rn:
                return
            src = self.ws_influencers.row_values(rn)
            # Influencers VIC cols: A=pid B=name C=speciality D=postcode E=url
            # F=follower_count G=post_count_90d H=has_video I=soft_score J=classifier_source
            pad = src + [""] * (10 - len(src))
            row = [
                sent_at,
                pad[0], pad[1], pad[2], pad[4],
                pad[5], pad[6], pad[8], pad[9],
            ]
            self.ws_connections.append_row(row, value_input_option="RAW")
        except Exception as e:
            print(f"[sheets] WARNING: Connections Sent append failed for "
                  f"{practitioner_id}: {e}")

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
            classification.get("engagement_rate", 0.0),
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
