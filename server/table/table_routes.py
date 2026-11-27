# modules/table/table_routes.py
import os
import tempfile
import traceback

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .ocr_client import HANDWRITTEN_VERSION
from .page_ocr import PAGE_VERSION, run_page_ocr
from .table_core import extract_table

router = APIRouter()

# Images without a table: "1" = run page OCR (needs a working page-OCR Docker
# image, see page_ocr.py), "0" = reply 422 "No table found".
PAGE_FALLBACK = os.getenv("TABLE_PAGE_FALLBACK", "0") == "1"

MAX_BYTES = int(os.getenv("TABLE_MAX_BYTES", str(15 * 1024 * 1024)))
SUPPORTED_LANGS = {"en", "hi", "mr", "te", "ta", "kn", "ml", "gu", "pa", "bn", "ori", "asa", "mni"}
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

# OhMyTable runs in its own Docker service (TABLE_STRUCTURE_URL); this module
# only crops cells and sends them to /ocr/infer.


@router.post("/layout/table", tags=["Table"])
async def ocr_table(
    file: UploadFile = File(...),
    lang: str = Form("hi"),
    modality: str = Form("printed"),
    mode: str = Form("auto"),  # auto | table | page
):
    if lang not in SUPPORTED_LANGS:
        raise HTTPException(400, f"Unsupported language '{lang}'.")
    if modality not in ("printed", "handwritten"):
        raise HTTPException(400, "modality must be 'printed' or 'handwritten'.")
    if mode not in ("auto", "table", "page"):
        raise HTTPException(400, "mode must be 'auto', 'table' or 'page'.")

    ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext}'.")

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image is too large.")

    # Heavy work runs in a worker thread. Running it directly in this async
    # function blocked the whole server, so the cell OCR call to /ocr/infer
    # (same server) could not be answered and waited until the timeout.
    return await run_in_threadpool(_process, data, ext, lang, modality, mode)


def _process(data, ext, lang, modality, mode):
    with tempfile.TemporaryDirectory(prefix="table_") as tmp:
        src = os.path.join(tmp, "input" + ext)
        with open(src, "wb") as f:
            f.write(data)
        try:
            if mode != "page":
                try:
                    html = extract_table(src, lang=lang, modality=modality,
                                         allow_no_table=(mode == "auto"))
                except ValueError as exc:
                    if mode == "table":
                        raise HTTPException(422, str(exc))
                    html = None  # looked like a table, no structure: page OCR
                if html is not None:
                    version = PAGE_VERSION if modality == "printed" else HANDWRITTEN_VERSION
                    return {"type": "table", "version": version, "html": html}

            if not PAGE_FALLBACK:
                raise HTTPException(
                    422, "No table found in this image. If the image is already "
                         "cropped to the table, send mode=table."
                )
            if modality != "printed":
                raise HTTPException(
                    422, "No table found. Page OCR fallback supports printed text only."
                )
            result = run_page_ocr(src, lang)
            return {"type": "page", "version": PAGE_VERSION, "result": result}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, f"OCR failed: {exc}")
