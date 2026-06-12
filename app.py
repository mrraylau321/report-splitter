"""
FastAPI demo: upload a multi-page scanned test-report PDF, split it into one PDF
per page renamed "<sample no>_<received date>.pdf", and download the results.

Processing runs in a background thread, one page at a time, so every HTTP request
returns quickly — the slow OCR never blocks a request long enough to hit a proxy
timeout / 502 on small free-tier instances. The frontend polls /api/status to
show page-by-page progress and stream results in as they finish.

Jobs are held in memory keyed by id; the store is bounded (oldest finished jobs
evicted) so a long-running free instance won't grow forever.
"""
from __future__ import annotations

import io
import threading
import uuid
import zipfile
from collections import OrderedDict

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pdfsplit import page_count, iter_pages

app = FastAPI(title="Test Report Splitter")

MAX_BYTES = 25 * 1024 * 1024     # reject uploads larger than 25 MB
MAX_JOBS = 20                    # keep at most this many jobs in memory
# Don't run too many heavy OCR jobs at once on a tiny instance.
_SLOTS = threading.Semaphore(2)

# job_id -> dict (see _new_job). Guarded by _LOCK for structural changes.
JOBS: "OrderedDict[str, dict]" = OrderedDict()
_LOCK = threading.Lock()


def _evict() -> None:
    with _LOCK:
        finished = [k for k, j in JOBS.items() if j["status"] in ("done", "error")]
        while len(JOBS) > MAX_JOBS and finished:
            JOBS.pop(finished.pop(0), None)


def _run_job(job_id: str, data: bytes) -> None:
    job = JOBS[job_id]
    with _SLOTS:
        try:
            for res, pdf_bytes in iter_pages(data):
                job["files"][res.filename] = pdf_bytes
                job["results"].append({
                    "page": res.page, "sample": res.sample,
                    "received_date": res.received_date, "filename": res.filename,
                })
                job["done"] = res.page
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - surface to the UI via /status
            job["status"] = "error"
            job["error"] = str(exc)
    _evict()


@app.post("/api/process")
async def process(file: UploadFile = File(...)) -> JSONResponse:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (max 25 MB).")
    try:
        total = page_count(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Not a readable PDF: {exc}") from exc

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "processing", "total": total, "done": 0,
        "results": [], "files": {}, "error": None, "source": file.filename,
    }
    # Daemon thread: OCR is mostly an external tesseract subprocess, so it does
    # not block the event loop from answering /status polls.
    threading.Thread(target=_run_job, args=(job_id, data), daemon=True).start()
    return JSONResponse({"job_id": job_id, "total": total})


@app.get("/api/status/{job_id}")
def status(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job expired or not found.")
    return JSONResponse({
        "status": job["status"],
        "total": job["total"],
        "done": job["done"],
        "error": job["error"],
        "results": job["results"],
    })


@app.get("/api/download/{job_id}/zip")
def download_zip(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job expired or not found.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in job["files"].items():
            zf.writestr(name, content)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="reports.zip"'},
    )


@app.get("/api/download/{job_id}/{filename}")
def download_one(job_id: str, filename: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job expired or not found.")
    content = job["files"].get(filename)
    if content is None:
        raise HTTPException(404, "File not found.")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


# Serve the frontend (index.html) at "/". Mounted last so /api/* wins.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
