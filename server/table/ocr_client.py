# modules/table/ocr_client.py
# Cell OCR through the /ocr/infer endpoint only (the one in the Swagger docs).
import base64
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import requests
from PIL import Image

# Call the OCR endpoint. Leave the default unless the website server has a faster internal address.
ILOCR_URL = os.getenv("ILOCR_URL", "https://ilocr.iiit.ac.in/ocr/infer")

# Cells per request. Lower it if requests time out.
BATCH_SIZE = int(os.getenv("TABLE_OCR_BATCH_SIZE", "256"))
# Batches sent at the same time. Each request costs seconds of overhead, so
# sending them together instead of one by one is the main speed-up.
PARALLEL = int(os.getenv("TABLE_OCR_PARALLEL", "1"))
REQUEST_TIMEOUT = int(os.getenv("TABLE_OCR_TIMEOUT", "300"))

PRINTED_VERSION = os.getenv("TABLE_PRINTED_VERSION", "V-01.13.01.00")  # e.g. "shashiocr"
HANDWRITTEN_VERSION = "V-01.11.00.00"

# Languages that V-01.13.01.00 accepts for printed text (from helper.py verify_model)
PRINTED_MODEL_LANGS = {"asa", "bn", "hi", "kn", "ml", "mni", "ori", "mr", "pa", "ta", "te"}

# Languages that V-01.11.00.00 accepts for handwritten text (everything except English)
HANDWRITTEN_LANGS = {"hi", "mr", "te", "ta", "kn", "ml", "gu", "pa", "bn", "ori", "ur"}

# "line" keeps the spaces between words in a cell. "word" joined them together.
CELL_LEVEL = os.getenv("TABLE_OCR_LEVEL", "line")

# Optional second pass: cells whose text is mostly digits (prices, %, codes)
# are read again with this language's model, e.g. "en", which knows ₹ , % .
# Empty = off.
NUMERIC_LANG = os.getenv("TABLE_NUMERIC_LANG", "")

# The OCR model reads one word per image, so a cell with several words comes
# back joined. Split each cell into words (by the white gaps between them),
# read the words, and join them with spaces. Set TABLE_SPLIT_WORDS=0 to turn off.
SPLIT_WORDS = os.getenv("TABLE_SPLIT_WORDS", "1") == "1"

# A lone symbol right before a number in an invoice is almost always ₹, which
# the Indian-language models cannot read. Set TABLE_RUPEE_FIX=0 to turn off.
RUPEE_FIX = os.getenv("TABLE_RUPEE_FIX", "1") == "1"

_local = threading.local()


def _sess():
    """One HTTP session per thread (keeps connections open, thread-safe)."""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _to_pil(arr, min_height=48, max_side=2500):
    img = Image.fromarray(arr) if isinstance(arr, np.ndarray) else arr
    img = img.convert("RGB")
    if img.height < min_height:
        s = min_height / img.height
        img = img.resize((max(1, int(img.width * s)), min_height), Image.BICUBIC)
    if max(img.size) > max_side:
        s = max_side / max(img.size)
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    return img


def _png_b64(arr) -> str:
    buf = io.BytesIO()
    _to_pil(arr).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Set TABLE_OCR_ALLOW_FALLBACK=1 to let printed cells fall back to "v2" when
# V-01.13.01.00 is unavailable. Off by default so table text always comes
# from V-01.13.01.00 (same model as the page OCR) or fails loudly.
ALLOW_FALLBACK = os.getenv("TABLE_OCR_ALLOW_FALLBACK", "0") == "1"


def _versions(modality: str, lang: str):
    """Model version to try, in order, for this modality and language."""
    if modality == "handwritten":
        return [HANDWRITTEN_VERSION]
    if lang in PRINTED_MODEL_LANGS:
        return [PRINTED_VERSION, "v2"] if ALLOW_FALLBACK else [PRINTED_VERSION]
    return ["v1", "v2"] if lang == "en" else ["v2", "v1"]


def extract_text(payload) -> str:
    """The endpoint returns [{"text": "...", "meta": {}}]. Handles other shapes too."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return "\n".join(t for t in (extract_text(x) for x in payload) if t)
    if isinstance(payload, dict):
        if isinstance(payload.get("text"), str):
            return payload["text"]
        for key in ("lines", "words", "data", "result", "results", "output"):
            if key in payload:
                return extract_text(payload[key])
    return ""


def _post_batch(images_b64, lang, modality):
    """Send one batch. Tries each version until the server accepts one."""
    last = ""
    for version in _versions(modality, lang):
        body = {
            "imageContent": images_b64,
            "modality": modality,
            "level": CELL_LEVEL,
            "language": lang,
            "version": version,
            "modelid": "",
            "omit": True,
            "meta": {},
        }
        r = _sess().post(ILOCR_URL, json=body, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            payload = r.json()
            items = payload if isinstance(payload, list) else [payload]
            if len(items) != len(images_b64):
                raise RuntimeError(
                    f"OCR returned {len(items)} results for {len(images_b64)} cells"
                )
            return [extract_text(item).strip() for item in items]
        last = f"{r.status_code} {r.text[:200]}"
        if "No model available" not in r.text:
            break
    raise RuntimeError(f"OCR failed for {lang}/{modality}: {last}")


def _one_batch(chunk, lang, modality):
    if len(chunk) == 1:
        # A single image can come back truncated on V2 models.
        # Send a duplicate and keep only the first result.
        return _post_batch(chunk * 2, lang, modality)[:1]
    return _post_batch(chunk, lang, modality)


def _recognize(encoded, lang, modality):
    if not encoded:
        return []
    chunks = [encoded[i:i + BATCH_SIZE] for i in range(0, len(encoded), BATCH_SIZE)]
    if len(chunks) == 1 or PARALLEL <= 1:
        return [t for c in chunks for t in _one_batch(c, lang, modality)]
    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(chunks))) as pool:
        parts = list(pool.map(lambda c: _one_batch(c, lang, modality), chunks))
    return [t for part in parts for t in part]  # map keeps the order


def _looks_numeric(text: str) -> bool:
    """Mostly digits (Latin or Devanagari) and number punctuation."""
    t = text.replace(" ", "")
    if not t:
        return False
    digits = sum(c.isdigit() for c in t)  # isdigit() covers ० to ९ too
    return digits / len(t) >= 0.6


def split_words(arr, gap_frac=0.32, pad=2):
    """
    Cut a cell crop into word crops at wide vertical white gaps.
    Table border lines inside the crop are ignored. Returns [arr] if it is one word.
    """
    if arr is None or arr.size == 0:
        return [arr]
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 else arr
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = ink.shape
    # drop border lines: columns/rows that are ink almost end to end
    ink[:, ink.sum(axis=0) > 0.8 * h] = 0
    ink[ink.sum(axis=1) > 0.8 * w, :] = 0

    rows = np.where(ink.sum(axis=1) > 0)[0]
    cols = ink.sum(axis=0) > 0
    if rows.size == 0 or not cols.any():
        return [arr]
    text_h = rows[-1] - rows[0] + 1
    min_gap = max(4, int(gap_frac * text_h))  # word gaps ~0.4-0.5 x height, letter gaps smaller

    # runs of ink columns, merged when the gap between them is small
    pieces, start, last = [], None, None
    for x in range(w):
        if cols[x]:
            if start is None:
                start = x
            elif x - last - 1 >= min_gap:
                pieces.append((start, last))
                start = x
            last = x
    if start is not None:
        pieces.append((start, last))
    if len(pieces) <= 1:
        return [arr]

    y1, y2 = max(0, rows[0] - pad), min(h, rows[-1] + 1 + pad)
    return [arr[y1:y2, max(0, a - pad):min(w, b + 1 + pad)] for a, b in pieces]


def _is_number(t):
    t = t.replace(",", "").replace(".", "")
    return bool(t) and all(c.isdigit() for c in t)


def _join_words(texts):
    """Join word results; turn a lone junk symbol before a number into ₹."""
    out = []
    for k, t in enumerate(texts):
        if (RUPEE_FIX and len(t) <= 2 and not _is_number(t)
                and k + 1 < len(texts) and _is_number(texts[k + 1])):
            t = "₹"
        if t:
            out.append(t)
    return " ".join(out)


def recognize_cells(arrs, lang: str, modality: str = "printed"):
    """
    Recognize all cell crops through /ocr/infer, in batches.
    Returns one string per crop, in the same order.
    """
    if not arrs:
        return []
    if SPLIT_WORDS:
        words = [split_words(a) for a in arrs]
        flat = [w for ws in words for w in ws]
        flat_txt = _recognize([_png_b64(w) for w in flat], lang, modality)
        results, k = [], 0
        for ws in words:
            results.append(_join_words(flat_txt[k:k + len(ws)]))
            k += len(ws)
        encoded = None
    else:
        encoded = [_png_b64(a) for a in arrs]
        results = _recognize(encoded, lang, modality)

    if NUMERIC_LANG and NUMERIC_LANG != lang and modality == "printed":
        idx = [i for i, t in enumerate(results) if _looks_numeric(t)]
        if idx:
            src = encoded or [_png_b64(a) for a in arrs]
            again = _recognize([src[i] for i in idx], NUMERIC_LANG, modality)
            for i, t in zip(idx, again):
                if t:
                    results[i] = t
    return results
