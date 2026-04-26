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
    print(f"  [compact] {name}: {n} non-empty (was {len(rows)} total)")
    cols = len(rows[0])
    target_rows = n + 100  # leave headroom for ~100 future writes
    # Resize FIRST so we have room to write back without errors
    ws.resize(rows=max(target_rows, 200), cols=cols)
    # Pad each row to width=cols (clear() can leave ragged shapes)
    padded = [r + [""] * (cols - len(r)) for r in non_empty]
    # Write back contiguously starting at A1
    ws.update(values=padded, range_name=f"A1:{chr(ord('A')+cols-1)}{n}",
              value_input_option="RAW")
    # Now clear anything below the last written row
    if target_rows > n:
        clear_range = f"A{n+1}:{chr(ord('A')+cols-1)}{target_rows}"
        ws.batch_clear([clear_range])
    print(f"           → {n} rows written A1:{chr(ord('A')+cols-1)}{n}, "
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
