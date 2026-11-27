# modules/table/table_core.py
# Table structure comes from the OhMyTable Docker service (cell boxes only).
# Cell text comes from /ocr/infer (V-01.13.01.00) via ocr_client.
import html
import logging
import os
import time

import cv2
import numpy as np
import requests

from .ocr_client import recognize_cells

log = logging.getLogger("table")

# Address of the table-structure container
STRUCTURE_URL = os.getenv("TABLE_STRUCTURE_URL", "http://127.0.0.1:8890/structure")
STRUCTURE_TIMEOUT = int(os.getenv("TABLE_STRUCTURE_TIMEOUT", "300"))

_session = requests.Session()


# mode=auto and the detector finds no table:
#   no   (default) = page OCR. Safe: plain pages are never turned into tables.
#   auto = read the whole image as a table instead. Only use this if users
#          mostly upload images already cropped to the table, because the
#          structure model will also make a (wrong) table out of a plain page.
# mode=table always reads the whole image as a table when none is detected.
TABLE_WHOLE_IMAGE = os.getenv("TABLE_WHOLE_IMAGE", "no")


def get_structure(image_path: str, whole: str = "no") -> dict:
    """Ask the Docker service for tables and cell boxes."""
    with open(image_path, "rb") as f:
        try:
            r = _session.post(STRUCTURE_URL,
                              files={"file": (os.path.basename(image_path), f)},
                              data={"whole": whole},
                              timeout=STRUCTURE_TIMEOUT)
        except requests.ConnectionError:
            raise RuntimeError(
                f"Table structure service not reachable at {STRUCTURE_URL}. "
                "Is the table-structure container running?"
            )
    if r.status_code != 200:
        raise RuntimeError(f"Table structure service error {r.status_code}: {r.text[:200]}")
    return r.json()


def is_blank(crop, dark_frac=0.005):
    if crop.size == 0:
        return True
    g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return float((g < 128).mean()) < dark_frac


def build_html(tokens, indices, cell_texts):
    """Same token logic as OhMyTable's get_full_html_output, with our cell text."""
    out, start = [], 0
    for cell_idx, end in enumerate(indices):
        out.extend(tokens[start:end + 1])
        text = cell_texts[cell_idx] if cell_idx < len(cell_texts) else ""
        if text:
            out.append(html.escape(text).replace("\n", "<br>"))
        start = end + 1
    if indices and indices[-1] < len(tokens) - 1:
        out.extend(tokens[indices[-1] + 1:])
    return "<table>" + "".join(out) + "</table>"


def extract_table(image_path: str, lang: str, modality: str = "printed",
                  allow_no_table: bool = False):
    """
    Returns the table(s) as HTML.
    No table found: returns None if allow_no_table, else raises ValueError.
    """
    bgr = cv2.imread(image_path)
    if bgr is None:
        raise ValueError("Could not read the image.")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]

    t0 = time.time()
    # mode=table (allow_no_table False): always try the whole image if nothing is detected
    whole = "auto" if (not allow_no_table or TABLE_WHOLE_IMAGE == "auto") else "no"
    result = get_structure(image_path, whole)
    tables = result.get("tables", [])
    log.warning("table: structure service %.1fs, %d table(s)", time.time() - t0, len(tables))

    if not tables:
        if allow_no_table:
            return None
        raise ValueError("No table found in this image. Crop to the table or use a sharper image.")

    outputs = []
    for tb in tables:
        crops = []
        for x1, y1, x2, y2 in tb["cells"]:
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(w, int(x2)), min(h, int(y2))
            crops.append(rgb[y1:y2, x1:x2] if x2 > x1 and y2 > y1
                         else np.zeros((0, 0, 3), np.uint8))

        todo = [i for i, c in enumerate(crops) if not is_blank(c)]
        texts = [""] * len(crops)
        if todo:
            t1 = time.time()
            results = recognize_cells([crops[i] for i in todo], lang, modality)
            log.warning("table: cell OCR %.1fs for %d cells", time.time() - t1, len(todo))
            for i, text in zip(todo, results):
                texts[i] = text

        outputs.append(build_html(tb["html_tokens"], tb["indices"], texts))

    return "\n".join(outputs)
