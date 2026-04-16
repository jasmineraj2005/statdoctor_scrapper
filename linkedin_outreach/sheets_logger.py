# ─────────────────────────────────────────────────────────────────────────────
# sheets_logger.py  –  Google Sheets live tracking
# ─────────────────────────────────────────────────────────────────────────────
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import csv, os
import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "practitioner_id", "name", "suburb", "state", "specialty",
    "linkedin_url", "status", "timestamp", "notes",
]

# Status values used across the codebase
STATUS_PENDING           = "pending"
STATUS_SENT              = "sent"
STATUS_NOT_FOUND         = "not_found"
STATUS_NAME_MISMATCH     = "name_mismatch"
STATUS_LOCATION_MISMATCH = "location_mismatch"
STATUS_ALREADY_CONNECTED = "already_connected"
STATUS_CONNECT_UNAVAIL   = "connect_unavailable"
STATUS_SKIPPED           = "skipped"
STATUS_ERROR             = "error"


class SheetsLogger:
    def __init__(self):
        self.ws = None           # Google Sheets worksheet
        self._row_cache = {}     # practitioner_id → sheet row number (1-indexed)
        self._connect_sheets()
        self._ensure_local_log()

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
            self.ws = sheet.worksheet(config.GSHEET_WORKSHEET_NAME)
            self._sync_row_cache()
            print(f"[sheets] Connected to '{config.GSHEET_SPREADSHEET_NAME}' → '{config.GSHEET_WORKSHEET_NAME}'")
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"[sheets] ERROR: Spreadsheet '{config.GSHEET_SPREADSHEET_NAME}' not found.")
            print("[sheets] Create it and share it with your service account email.")
        except Exception as e:
            print(f"[sheets] ERROR connecting: {e}")

    def _ensure_local_log(self):
        """Create local outreach_log.csv with headers if it doesn't exist."""
        if not os.path.exists(config.OUTPUT_LOG):
            with open(config.OUTPUT_LOG, "w", newline="") as f:
                csv.writer(f).writerow(HEADERS)

    def _sync_row_cache(self):
        """Build practitioner_id → row number map from existing sheet data."""
        if not self.ws:
            return
        try:
            records = self.ws.get_all_values()
            for i, row in enumerate(records[1:], start=2):   # skip header row
                if row and row[0]:
                    self._row_cache[row[0]] = i
        except Exception as e:
            print(f"[sheets] WARNING: could not sync row cache: {e}")

    def _ensure_header(self):
        """Write header row if the sheet is empty."""
        if not self.ws:
            return
        try:
            existing = self.ws.row_values(1)
            if not existing:
                self.ws.append_row(HEADERS, value_input_option="RAW")
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────────────────

    def add_pending(self, practitioner: dict):
        """Add a practitioner row with status=pending before processing starts."""
        row = self._build_row(practitioner, STATUS_PENDING, "", "")
        self._write_row(practitioner["practitioner_id"], row)

    def update(self, practitioner_id: str, status: str, linkedin_url: str = "", notes: str = ""):
        """Update status of an existing row (or append if missing)."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update local CSV
        self._update_local_csv(practitioner_id, status, linkedin_url, notes, timestamp)

        # Update Google Sheet
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
                    self.ws.update_cell(row_num, 7, linkedin_url)  # column F = linkedin_url
            # If not in cache, the row was never added — just log locally
        except Exception as e:
            print(f"[sheets] WARNING: could not update row for {practitioner_id}: {e}")

    # ── Internal helpers ──────────────────────────────────────────────────────

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
        """Append to Google Sheet and write to local CSV."""
        # Local CSV
        with open(config.OUTPUT_LOG, "a", newline="") as f:
            csv.writer(f).writerow(row)

        # Google Sheet
        if not self.ws:
            return
        try:
            self._ensure_header()
            self.ws.append_row(row, value_input_option="RAW")
            # Update row cache with new row number
            total_rows = len(self.ws.get_all_values())
            self._row_cache[practitioner_id] = total_rows
        except Exception as e:
            print(f"[sheets] WARNING: could not append row: {e}")

    def _update_local_csv(self, practitioner_id, status, linkedin_url, notes, timestamp):
        """Rewrite local CSV updating the matching row."""
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

    # ── Analytics helpers ─────────────────────────────────────────────────────

    def count_sent_today(self) -> int:
        """Count how many 'sent' rows have today's date in outreach_log.csv."""
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
        """Count 'sent' rows in the last 7 days."""
        from datetime import timedelta
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
        """Return True if this practitioner_id already has a non-pending status."""
        if not os.path.exists(config.OUTPUT_LOG):
            return False
        with open(config.OUTPUT_LOG, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("practitioner_id") == practitioner_id:
                    return row.get("status", "") != STATUS_PENDING
        return False
