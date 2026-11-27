# Table structure service (OhMyTable). Finds tables and cell boxes only.
# No text recognition here: the server crops the cells and sends them to /ocr/infer.
#
#   POST /structure   (multipart "file", optional "whole" = no | yes | auto)  ->  {"width", "height", "tables": [...]}
#   GET  /health
#
# Each table: {"bbox": [l, t, r, b], "html_tokens": [...], "indices": [...],
#              "cells": [[x1, y1, x2, y2], ...]}   (cells in full-image pixels,
#              one per entry in "indices", same order)
import logging
import os
import threading
import time

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="Table structure (OhMyTable)")

MAX_BYTES = int(os.getenv("TABLE_MAX_BYTES", str(15 * 1024 * 1024)))
_engine = None
_lock = threading.Lock()  # OhMyTable is heavy: one image at a time


def get_engine():
    global _engine
    if _engine is None:
        from ohmytable import OhMyTable
        device = os.getenv("TABLE_DEVICE", "auto")
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        log.info("OhMyTable loading on %s", device)
        _engine = OhMyTable(device=device)
    return _engine


# ---------------------------------------------------------------- detection

def ruled_table_boxes(bgr, min_area_frac=0.02, min_joints=6):
    """Bordered tables from crossing horizontal/vertical lines (fallback)."""
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, -2)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 30), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 30)))
    horiz = cv2.dilate(cv2.erode(bw, hk), hk)
    vert = cv2.dilate(cv2.erode(bw, vk), vk)
    grid = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))
    joints = cv2.dilate(cv2.bitwise_and(horiz, vert), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        x, y, bw_, bh_ = cv2.boundingRect(c)
        if bw_ * bh_ < min_area_frac * w * h:
            continue
        n, _ = cv2.connectedComponents(joints[y:y + bh_, x:x + bw_])
        if n - 1 >= min_joints:
            out.append((x, y, x + bw_, y + bh_))
    return out


def _box4(b):
    """Accept [l,t,r,b,...], numpy rows, or dicts with box/bbox."""
    if isinstance(b, dict):
        b = b.get("box") or b.get("bbox") or b.get("xyxy")
    a = np.asarray(b, dtype=float).reshape(-1)
    return a[:4]


def detect_tables(engine, bgr):
    h, w = bgr.shape[:2]
    det = getattr(engine, "table_detect", None)
    if det is None:
        return ruled_table_boxes(bgr)
    boxes = det(bgr)
    if boxes is None or len(boxes) == 0:
        return []
    out = []
    for b in boxes:
        l, t, r, bt = (int(round(v)) for v in _box4(b))
        l, t, r, bt = max(0, l), max(0, t), min(w, r), min(h, bt)
        if r > l and bt > t:
            out.append((l, t, r, bt))
    return out


def cell_xyxy(box):
    a = np.asarray(box, dtype=float).reshape(-1)
    if a.size == 4:
        x1, y1, x2, y2 = a
    else:
        pts = a.reshape(-1, 2)
        (x1, y1), (x2, y2) = pts.min(axis=0), pts.max(axis=0)
    return x1, y1, x2, y2


def analyse(bgr, whole="auto"):
    """
    whole="no":   only detected tables
    whole="yes":  skip detection, the whole image is one table
    whole="auto": detected tables; if none, try the whole image as a table
                  (for images that are already cropped to the table)
    """
    engine = get_engine()
    h, w = bgr.shape[:2]
    if whole == "yes":
        regions = [(0, 0, w, h)]
    else:
        t0 = time.time()
        regions = detect_tables(engine, bgr)
        log.info("structure: detect %.2fs, %d table(s)", time.time() - t0, len(regions))
        if not regions and whole == "auto":
            regions = [(0, 0, w, h)]
            log.info("structure: no table detected, trying whole image")

    tables = []
    for l, t, r, b in regions:
        rgb = cv2.cvtColor(bgr[t:b, l:r], cv2.COLOR_BGR2RGB)
        t1 = time.time()
        s = engine.table_structure.generate(rgb)
        log.info("structure: table %.2fs", time.time() - t1)
        tokens, indices = list(s.html_tokens), [int(i) for i in s.indices]
        if not tokens:
            continue
        if len(s.boxes) != len(indices):
            log.warning("skipping table: %d boxes vs %d cells", len(s.boxes), len(indices))
            continue
        cells = []
        for box in s.boxes:
            x1, y1, x2, y2 = cell_xyxy(box)
            # crop coords -> full image coords, clipped
            x1, x2 = max(l, int(x1) + l), min(r, int(x2) + l)
            y1, y2 = max(t, int(y1) + t), min(b, int(y2) + t)
            cells.append([x1, y1, x2, y2])
        tables.append({"bbox": [l, t, r, b], "html_tokens": tokens,
                       "indices": indices, "cells": cells})
    return {"width": w, "height": h, "tables": tables}


# ---------------------------------------------------------------- API

@app.on_event("startup")
def _load():
    get_engine()  # load model at start, not on first request


@app.get("/health")
def health():
    return {"ok": _engine is not None}


def _run(data, whole):
    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise HTTPException(400, "Could not read the image.")
    with _lock:
        return analyse(bgr, whole)


@app.post("/structure")
async def structure(file: UploadFile = File(...), whole: str = Form("no")):
    if whole not in ("no", "yes", "auto"):
        raise HTTPException(400, "whole must be 'no', 'yes' or 'auto'.")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image is too large.")
    return await run_in_threadpool(_run, data, whole)
