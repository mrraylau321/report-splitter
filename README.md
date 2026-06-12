# Test Report Splitter

Upload a multi-page scanned **test-report PDF** → get back one PDF per page, each
renamed **`<first sample no>_<sample received date>.pdf`**
(e.g. `26050178_27-05-2026.pdf`), both read automatically by OCR.

- **Backend:** FastAPI + [PyMuPDF](https://pymupdf.readthedocs.io/) (split) +
  [Tesseract](https://github.com/tesseract-ocr/tesseract) (OCR)
- **Frontend:** single static `index.html` (drag-drop upload, results table, ZIP download)
- **Cost:** free — OCR runs on the host, no paid API, no API keys, no data leaves the server.

## How it works

Each page is a scan with no text layer, so the app crops the left header/results
strip, binarizes it, OCRs it once, and parses two fields **by content** (not by
fixed position, since they shift with the length of the address / sample
description above):

* **Sample Received Date** — `Sample Received Date : 27/05/2026` → `27-05-2026`
* **First sample number** — the first table data row's *Sample Name* (a 6+ digit
  number immediately followed by the *Sample No* code), e.g. `26050178` or
  `2026022238` (length varies between reports).

Filename = `<sample>_<date>.pdf`. If a field can't be read the name falls back
gracefully (`<sample>.pdf`, `p<n>_<date>.pdf`, or `UNREAD_p<n>.pdf`) so no page
is ever dropped.

`TESSERACT_CMD` env var overrides the tesseract binary path (auto-detected on
Windows/Docker).

## Run locally

```bash
pip install -r requirements.txt
# Windows: install Tesseract (https://github.com/UB-Mannheim/tesseract/wiki)
# Linux/Mac: sudo apt install tesseract-ocr  /  brew install tesseract
uvicorn app:app --reload
# open http://localhost:8000
```

Test the core splitter on a sample without the web UI:

```bash
python pdfsplit.py path/to/report.pdf
```

## Deploy on Render (free)

1. Push this repo to GitHub.
2. On [Render](https://render.com): **New → Web Service** → connect the repo.
   Render reads `render.yaml` (or detects the `Dockerfile`) — runtime **Docker**,
   plan **Free**.
3. Deploy. The Dockerfile installs Tesseract, so no extra setup.

> Free instances sleep after inactivity; the first request after idle takes ~30–50s
> to wake. Processed files are held in memory only (not persisted).
