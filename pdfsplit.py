"""
Core logic: split a multi-page scanned test-report PDF into one single-page PDF
per page, each renamed to "<first sample no>_<sample received date>.pdf"
(e.g. 26050178_27-05-2026.pdf), read by OCR.

The scans have no text layer, so for each page we crop the left header/results
strip, binarize it, and OCR with Tesseract, then parse two fields by content
(not by fixed position, since their vertical place shifts with the length of the
address / sample description above them):

  * Sample Received Date  -> "Sample Received Date : 27/05/2026"  -> 27-05-2026
  * First sample number   -> first table data row's "Sample Name" column
                             (a 6+ digit number followed by the "Sample No" code),
                             e.g. 26050178 or 2026022238 (length varies per report).
"""
from __future__ import annotations

import io
import os
import re
import sys
from dataclasses import dataclass

# Keep memory/CPU low on small free-tier instances: one OCR thread.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

# ---- Tesseract binary location -------------------------------------------------
# On Render/Docker tesseract is on PATH. On Windows dev it usually isn't.
_TESS = os.environ.get("TESSERACT_CMD")
if not _TESS and sys.platform == "win32":
    for _p in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if os.path.exists(_p):
            _TESS = _p
            break
if _TESS:
    pytesseract.pytesseract.tesseract_cmd = _TESS

# ---- Tunables (match this lab's "typical" report layout) -----------------------
DPI = 300
# Left strip (fractions of page size) covering the received-date line and the
# results table. Stops at 0.52 width to exclude the "Test Completed Date" on the
# right of the same line.
STRIP = (0.0, 0.27, 0.52, 0.72)  # x0, y0, x1, y1
THRESH = 170  # binarization cutoff


@dataclass
class PageResult:
    page: int                  # 1-based page number
    sample: str | None         # first sample number
    received_date: str | None  # DD-MM-YYYY
    filename: str
    raw: str                   # raw OCR text, for transparency / debugging


def _ocr_strip(page: fitz.Page) -> str:
    r = page.rect
    clip = fitz.Rect(r.width * STRIP[0], r.height * STRIP[1],
                     r.width * STRIP[2], r.height * STRIP[3])
    # Grayscale pixmap straight from PyMuPDF (no PNG round-trip, less memory).
    pix = page.get_pixmap(dpi=DPI, clip=clip, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    pix = None  # release the pixmap buffer promptly
    img = img.point(lambda x: 0 if x < THRESH else 255)  # binarize
    return pytesseract.image_to_string(img, config="--psm 6")


def _parse(text: str) -> tuple[str | None, str | None]:
    """(sample number, received date DD-MM-YYYY) — either may be None."""
    md = re.search(
        r"Received\s*Date\s*[:.]?\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})",
        text, re.I)
    date = f"{md.group(1).zfill(2)}-{md.group(2).zfill(2)}-{md.group(3)}" if md else None

    # First data row: a run of 6+ digits (Sample Name) followed by the Sample No
    # code, which starts with a letter (e.g. "26050178 WT-2605-0700").
    ms = re.search(r"(\d{6,})\s+[A-Za-z]", text)
    sample = ms.group(1) if ms else None
    return sample, date


def _filename(sample: str | None, date: str | None, page: int) -> str:
    if sample and date:
        base = f"{sample}_{date}"
    elif sample:
        base = sample
    elif date:
        base = f"p{page}_{date}"
    else:
        base = f"UNREAD_p{page}"
    return f"{base}.pdf"


def page_count(data: bytes) -> int:
    """Number of pages in `data` (cheap; opens and closes the PDF)."""
    src = fitz.open(stream=data, filetype="pdf")
    try:
        return src.page_count
    finally:
        src.close()


def iter_pages(data: bytes):
    """
    Yield (PageResult, pdf_bytes) one page at a time, so callers can report
    progress / stream results instead of waiting for the whole document.

    Filenames are <sample>_<received-date>.pdf; missing fields fall back
    gracefully and collisions get a -2, -3 ... suffix so nothing is lost.
    """
    src = fitz.open(stream=data, filetype="pdf")
    used: dict[str, int] = {}
    try:
        for i in range(src.page_count):
            page = src[i]
            try:
                raw = _ocr_strip(page)
                sample, date = _parse(raw)
            except Exception as exc:  # noqa: BLE001 - never let one page kill the batch
                sample, date, raw = None, None, f"OCR error: {exc}"

            name = _filename(sample, date, i + 1)
            if name in used:
                used[name] += 1
                name = name[:-4] + f"-{used[name]}.pdf"
            else:
                used[name] = 1

            out = fitz.open()
            out.insert_pdf(src, from_page=i, to_page=i)
            pdf_bytes = out.tobytes()
            out.close()

            yield (PageResult(page=i + 1, sample=sample, received_date=date,
                             filename=name, raw=raw.strip()), pdf_bytes)
    finally:
        src.close()


def split_pdf(data: bytes) -> tuple[list[PageResult], dict[str, bytes]]:
    """Convenience wrapper that processes every page and returns them together."""
    results: list[PageResult] = []
    files: dict[str, bytes] = {}
    for res, pdf_bytes in iter_pages(data):
        results.append(res)
        files[res.filename] = pdf_bytes
    return results, files


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "SKM2226052917551.pdf"
    with open(path, "rb") as fh:
        res, files = split_pdf(fh.read())
    for r in res:
        flag = "" if (r.sample and r.received_date) else "  <-- check"
        print(f"page {r.page:2}: {r.filename:34}{flag}")
    print(f"\n{len(files)} files, {sum(len(b) for b in files.values())//1024} KB total")
