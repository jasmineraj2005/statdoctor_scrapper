#!/usr/bin/env python3
"""One-off: compact every sheet tab so non-empty rows are contiguous.

Background: gspread's ws.append_row writes past the worksheet's
pre-allocated row count when the table can't be detected. With sheets
created at rows=1000, fresh appends landed at row 1001+ leaving a 985-row
gap of blanks between manual + auto rows. Solution:
  1. Read all non-empty rows
  2. Clear the entire sheet
  3. Re-write rows contiguously starting at row 1
  4. Resize the worksheet to (data + 100 headroom)
"""
from __future__ import annotations

from gspread.utils import rowcol_to_a1

import config
from sheets_logger import SheetsLogger


def compact(ws, name: str):
    if ws is None:
        print(f"  [skip] {name}: not connected")
        return
    rows = ws.get_all_values()
    non_empty = [r for r in rows if any(c.strip() for c in r)]
    n = len(non_empty)
    if not n:
        print(f"  [skip] {name}: empty")
        return
    # Use the max row width across all non-empty rows (headers may be shorter
    # than data rows after schema migrations).
    cols = max(len(r) for r in non_empty)
    last_col_a1 = rowcol_to_a1(1, cols)[:-1]  # strip the row number → "A"/"M"/"AA"
    target_rows = n + 100
    print(f"  [compact] {name}: {n} non-empty × {cols} cols (was {len(rows)} total)")
    # Resize FIRST so we have room to write back without errors
    ws.resize(rows=max(target_rows, 200), cols=cols)
    # Pad each row to width=cols
    padded = [r + [""] * (cols - len(r)) for r in non_empty]
    range_to_write = f"A1:{last_col_a1}{n}"
    ws.update(values=padded, range_name=range_to_write,
              value_input_option="RAW")
    if target_rows > n:
        clear_range = f"A{n+1}:{last_col_a1}{target_rows}"
        ws.batch_clear([clear_range])
    print(f"           → {n} rows written {range_to_write}, "
          f"resized to {target_rows} rows")


def main():
    l = SheetsLogger()
    targets = [
        ("Influencers VIC",   l.ws_influencers),
        ("Reviewed Skipped",  l.ws_skipped),
        ("Processing Status", l.ws_status),
        ("Live Run Log",      l.ws_live),
        ("Connections Sent",  l.ws_connections),
    ]
    print("Compacting all sheet tabs:")
    for name, ws in targets:
        compact(ws, name)


if __name__ == "__main__":
    main()
