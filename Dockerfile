FROM python:3.12-slim

# Tesseract OCR engine (the only system dependency).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT; default to 10000 for local `docker run`.
ENV PORT=10000
# Keep Tesseract single-threaded so it stays within small free-tier memory.
ENV OMP_THREAD_LIMIT=1
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
