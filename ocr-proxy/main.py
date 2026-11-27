"""
Small CORS proxy for the IIIT OCR APIs.

The browser calls this service; this service calls ilocr.iiit.ac.in server-side,
where CORS does not apply. Request and response bodies are passed through
unchanged. Document contents are not logged.

  POST /ocr/infer     -> OCR_URL    (text OCR: printed shashiocr, handwritten V-01.11.00.00)
  POST /layout/table  -> TABLE_URL  (table extraction, multipart upload)
"""

import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

OCR_URL = os.getenv("UPSTREAM_URL", "https://ilocr.iiit.ac.in/ocr/infer")
TABLE_URL = os.getenv("TABLE_URL", "https://ilocr.iiit.ac.in/layout/table")

# Comma-separated list of origins allowed to call this proxy, for example:
# ALLOWED_ORIGINS=https://yourname.github.io,http://localhost:8000
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
    if o.strip()
]

# Reject oversized uploads before forwarding (default 25 MB).
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(25 * 1024 * 1024)))

# OCR of large pages and tables can be slow, so allow a generous upstream timeout.
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "300"))

app = FastAPI(title="OCR CORS proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
async def health():
    return {"ok": True, "ocr": OCR_URL, "table": TABLE_URL}


async def _forward(request: Request, url: str) -> Response:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")

    # Keep the original Content-Type: JSON for /ocr/infer, multipart (with its
    # boundary) for /layout/table.
    headers = {"Content-Type": request.headers.get("content-type", "application/json")}
    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
            upstream = await client.post(url, content=raw, headers=headers)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OCR service timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"OCR service unreachable: {exc}")

    # Pass the upstream status and body straight through, so error messages
    # such as "No model available ..." still reach the page.
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


@app.post("/ocr/infer")
async def infer(request: Request):
    return await _forward(request, OCR_URL)


@app.post("/layout/table")
async def table(request: Request):
    return await _forward(request, TABLE_URL)
