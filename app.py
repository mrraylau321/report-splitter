"""
FastAPI demo: upload a multi-page scanned test-report PDF, split it into one PDF
per page renamed to its Test Report No, and download the results (per-file or ZIP).

Processed files are kept in memory keyed by a job id. This is a demo; the store is
bounded (oldest jobs evicted) so a long-running free instance won't grow forever.
"""
from __future__ import annotations

import io
import uuid
import zipfile
from collections import OrderedDict

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pdfsplit import split_pdf

app = FastAPI(title="Test Report Splitter")

MAX_BYTES = 25 * 1024 * 1024     # reject uploads larger than 25 MB
MAX_JOBS = 20                    # keep at most this many jobs in memory

# job_id -> {"files": {name: bytes}, "results": [dict, ...]}
JOBS: "OrderedDict[str, dict]" = OrderedDict()


def _evict() -> None:
    while len(JOBS) > MAX_JOBS:
        JOBS.popitem(last=False)


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
        results, files = split_pdf(data)
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the UI
        raise HTTPException(422, f"Could not process PDF: {exc}") from exc

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "files": files,
        "results": [r.__dict__ for r in results],
    }
    _evict()

    unread = sum(1 for r in results if not (r.sample and r.received_date))
    return JSONResponse({
        "job_id": job_id,
        "source": file.filename,
        "pages": len(results),
        "unread": unread,
        "results": [
            {"page": r.page, "sample": r.sample,
             "received_date": r.received_date, "filename": r.filename}
            for r in results
        ],
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
