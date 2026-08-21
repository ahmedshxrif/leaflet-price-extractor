"""
Optional input decryption for NASCA/DRM-protected files — WINDOWS ONLY.

On the entitled work Windows laptop, Microsoft Excel is authorised to open a
NASCA-protected .xlsx (the DRM agent decrypts it in memory for the whitelisted
app). We automate that via COM: open in Excel, read the decrypted cell values,
re-save through openpyxl into a clean workbook with no protection wrapper.

Design rule: this is a NO-OP on macOS/Linux (returns the path unchanged) so the
SAME repo runs on the Mac (dev) and Windows (work). Nothing here imports
win32com unless actually on Windows.

Scope: handles .xlsx / .xlsm (e.g. the Model List). PDFs are NOT decryptable by
this Excel-COM trick — see decrypt_if_needed().
"""

from __future__ import annotations

import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")


def prep_excel(src_path: str) -> None:
    """Strip DRM from a .xlsx by round-tripping through the entitled Excel.
    Overwrites src_path in place. Windows-only (raises if win32com missing)."""
    import os
    import time
    import datetime
    import win32com.client          # noqa: import here — Windows-only
    import openpyxl

    chunk_size = 1000
    dst_path = src_path.replace(".xlsx", "-temp.xlsx")

    def strip_tz(dt):
        if isinstance(dt, (datetime.datetime, datetime.time)) and getattr(dt, "tzinfo", None):
            return dt.replace(tzinfo=None)
        return dt

    def normalize_excel_data(data_chunk, rows_count, n_cols):
        if data_chunk is None:
            return tuple()
        if not isinstance(data_chunk, tuple):
            return ((data_chunk,),)
        if rows_count == 1:
            return (data_chunk,) if not isinstance(data_chunk[0], tuple) else data_chunk
        if n_cols == 1:
            return tuple((v,) for v in data_chunk)
        return data_chunk

    print(f"[decrypt] Excel round-trip: {src_path}")
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    if os.path.exists(dst_path):
        os.remove(dst_path)
        while os.path.exists(dst_path):
            time.sleep(1)

    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        wb_interop = excel.Workbooks.Open(os.path.abspath(src_path))
        ws_interop = wb_interop.ActiveSheet
        used_range = ws_interop.UsedRange
        n_rows = used_range.Rows.Count
        n_cols = used_range.Columns.Count
        print(f"[decrypt] used range {n_rows} x {n_cols}")

        wb_xlsx = openpyxl.Workbook()
        ws_xlsx = wb_xlsx.active

        for row_start in range(1, n_rows + 1, chunk_size):
            row_end = min(row_start + chunk_size - 1, n_rows)
            top_left = ws_interop.Cells(row_start, 1)
            bottom_right = ws_interop.Cells(row_end, n_cols)
            data_chunk = ws_interop.Range(top_left, bottom_right).Value
            data_chunk = normalize_excel_data(data_chunk, row_end - row_start + 1, n_cols)
            for r_off, row in enumerate(data_chunk):
                for c_off, value in enumerate(row):
                    ws_xlsx.cell(row=row_start + r_off, column=1 + c_off, value=strip_tz(value))

        wb_xlsx.save(dst_path)
        wb_interop.Close(False)
    finally:
        excel.Quit()

    os.remove(src_path)
    os.rename(dst_path, src_path)
    print("[decrypt] done")


def decrypt_if_needed(path) -> str:
    """Decrypt an input file in place if we're on Windows and it's a supported
    type. NO-OP (returns path unchanged) on non-Windows or unsupported types.

    - .xlsx / .xlsm  -> prep_excel (entitled-Excel round-trip)
    - .pdf           -> prep_pdf via the external NASCA_PDF_DECRYPT_CMD command
                        (the DRM engine lives on the entitled Windows machine).
                        If that env var is unset, the PDF is left unchanged
                        (assumed already print-to-PDF'd / clean).
    """
    p = Path(path)
    if not IS_WINDOWS:
        return str(p)                      # dev on Mac: never touches files
    ext = p.suffix.lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            prep_excel(str(p))
        elif ext == ".pdf":
            prep_pdf(str(p))
    except Exception as e:                 # never let a decrypt failure kill the run
        print(f"[decrypt] skipped {p.name}: {e}")
    return str(p)


# ---------------------------------------------------------------------------
# PDF decryption (NASCA) — runs an EXTERNAL command you configure, because the
# DRM engine lives on the entitled Windows machine, not in Python.
#
# Set the environment variable NASCA_PDF_DECRYPT_CMD to a command template with
# {src} and {dst} placeholders, e.g.:
#     set NASCA_PDF_DECRYPT_CMD=nasca-decrypt.exe --in "{src}" --out "{dst}"
# The app fills {src}/{dst} and runs it; the decrypted clean PDF replaces the
# original. If the variable is unset, PDF decryption is skipped (the file is
# assumed already-clean, e.g. you print-to-PDF manually first).
# ---------------------------------------------------------------------------

import os as _os
import shlex as _shlex
import subprocess as _subprocess


def _pdf_looks_encrypted(path: str) -> bool:
    """True if PyMuPDF cannot open it as a normal PDF (NASCA-wrapped files fail)."""
    try:
        import fitz
        doc = fitz.open(path)
        n = doc.page_count
        doc.close()
        return n == 0
    except Exception:
        return True


def prep_pdf(src_path: str) -> None:
    """Decrypt a NASCA PDF in place via the configured NASCA_PDF_DECRYPT_CMD."""
    cmd_tpl = _os.environ.get("NASCA_PDF_DECRYPT_CMD", "").strip()
    if not cmd_tpl:
        print(f"[decrypt] NASCA_PDF_DECRYPT_CMD not set — leaving {Path(src_path).name} "
              f"as-is (must be pre-decrypted / print-to-PDF).")
        return
    dst = src_path.replace(".pdf", "-dec.pdf")
    cmd = cmd_tpl.format(src=src_path, dst=dst)
    print(f"[decrypt] PDF: {cmd}")
    _subprocess.run(_shlex.split(cmd), check=True)
    if _os.path.exists(dst):
        _os.replace(dst, src_path)     # clean file replaces original
    print("[decrypt] PDF done")
