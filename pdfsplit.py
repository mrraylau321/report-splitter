"""
Core logic: split a multi-page scanned test-report PDF into one PDF per page,
each renamed to its Test Report No. (read by OCR from the top-right corner).

No text layer exists in these scans, so we crop the header region of each page,
binarize it, and OCR with Tesseract. The report number format for this lab is a
fixed prefix letter + N digits (e.g. T0152457). Tesseract reliably reads the
digits but often misreads the leading "T" as "1"/"7", so we normalize to
PREFIX + last CORE_LEN digits.
"""
from __future__ import annotations

import io
import os
import re
import sys
from dataclasses import dataclass

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
PREFIX = os.environ.get("REPORT_PREFIX", "T")   # leading letter of report numbers
CORE_LEN = int(os.environ.get("REPORT_DIGITS", "7"))  # digits after the prefix
DPI = 400
# Header crop as fractions of page size: top-right block that holds "Test Report No".
CLIP = (0.55, 0.10, 0.99, 0.22)  # x0, y0, x1, y1


@dataclass
class PageResult:
    page: int          # 1-based page number
    report_no: str | None
    filename: str
    raw: str           # raw OCR line, for debugging / transparency in the UI


def _ocr_header(page: fitz.Page) -> str:
    r = page.rect
    clip = fitz.Rect(r.width * CLIP[0], r.height * CLIP[1],
                     r.width * CLIP[2], r.height * CLIP[3])
    pix = page.get_pixmap(dpi=DPI, clip=clip)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    img = img.point(lambda x: 0 if x < 160 else 255)  # binarize
    return pytesseract.image_to_string(img, config="--psm 6")


def _extract(text: str) -> tuple[str | None, str]:
    """Return (normalized report_no, raw_line). None if not found."""
    for line in text.splitlines():
        if re.search(r"Report\s*No", line, re.I) and "Folder" not in line:
            m = re.search(r"No\s*[:.]?\s*([A-Za-z0-9]{6,10})", line)
            if m:
                digits = re.sub(r"\D", "", m.group(1))
                if len(digits) >= CORE_LEN:
                    return f"{PREFIX}{digits[-CORE_LEN:]}", line.strip()
            return None, line.strip()
    return None, text.replace("\n", " ").strip()


def split_pdf(data: bytes) -> tuple[list[PageResult], dict[str, bytes]]:
    """
    Split `data` (a PDF) into one single-page PDF per page.

    Returns (results, files) where files maps filename -> pdf bytes.
    Filenames are <report_no>.pdf; unread pages become UNREAD_p<n>.pdf;
    collisions get a -2, -3 ... suffix so nothing is lost.
    """
    src = fitz.open(stream=data, filetype="pdf")
    results: list[PageResult] = []
    files: dict[str, bytes] = {}
    used: dict[str, int] = {}

    for i in range(src.page_count):
        page = src[i]
        report_no, raw = _extract(_ocr_header(page))

        base = report_no if report_no else f"UNREAD_p{i + 1}"
        name = f"{base}.pdf"
        if name in used:
            used[name] += 1
            name = f"{base}-{used[name]}.pdf"
        else:
            used[name] = 1

        out = fitz.open()
        out.insert_pdf(src, from_page=i, to_page=i)
        files[name] = out.tobytes()
        out.close()

        results.append(PageResult(page=i + 1, report_no=report_no,
                                  filename=name, raw=raw))

    src.close()
    return results, files


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "SKM2226052917551.pdf"
    with open(path, "rb") as fh:
        res, files = split_pdf(fh.read())
    for r in res:
        flag = "" if r.report_no else "  <-- UNREAD"
        print(f"page {r.page:2}: {r.filename:20}{flag}  (ocr: {r.raw!r})")
    print(f"\n{len(files)} files, {sum(len(b) for b in files.values())//1024} KB total")
