# Test Report Splitter

Upload a multi-page scanned **test-report PDF** → get back one PDF per page, each
renamed to its **Test Report No.** (e.g. `T0152457.pdf`), read automatically from
the page header by OCR.

- **Backend:** FastAPI + [PyMuPDF](https://pymupdf.readthedocs.io/) (split) +
  [Tesseract](https://github.com/tesseract-ocr/tesseract) (OCR)
- **Frontend:** single static `index.html` (drag-drop upload, results table, ZIP download)
- **Cost:** free — OCR runs on the host, no paid API, no API keys, no data leaves the server.

## How it works

Each page is a scan with no text layer, so the app crops the top-right header,
binarizes it, and OCRs the `Test Report No.` line. Tesseract reads the digits
reliably but tends to misread the leading `T`, so the result is normalized to
`PREFIX + last N digits`.

Tunable via environment variables:

| Var | Default | Meaning |
|-----|---------|---------|
| `REPORT_PREFIX` | `T` | Leading letter of report numbers |
| `REPORT_DIGITS` | `7` | Digit count after the prefix |
| `TESSERACT_CMD` | (auto) | Path to the tesseract binary (auto-detected on Win/Docker) |

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
