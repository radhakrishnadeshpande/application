# modules/table/page_ocr.py
# Page OCR for images that have no table: runs the V-01.13.01.00 Docker model
# (same thing infer.sh does) and returns the parsed out.json.
import json
import os
import shutil
import subprocess
import tempfile

PAGE_VERSION = "V-01.13.01.00"
PAGE_OCR_IMAGE = os.getenv("PAGE_OCR_IMAGE", f"ocr:{PAGE_VERSION}")
PAGE_OCR_TIMEOUT = int(os.getenv("PAGE_OCR_TIMEOUT", "600"))
DOCKER_BIN = os.getenv("DOCKER_BIN", "docker")

# The Docker model takes the language as its only argument. Set
# PAGE_OCR_LANG_STYLE=name if it expects "hindi" instead of "hi".
LANG_STYLE = os.getenv("PAGE_OCR_LANG_STYLE", "code")
LANG_NAMES = {
    "en": "english", "hi": "hindi", "mr": "marathi", "te": "telugu",
    "ta": "tamil", "kn": "kannada", "ml": "malayalam", "gu": "gujarati",
    "pa": "punjabi", "bn": "bengali", "ori": "odia", "asa": "assamese",
    "mni": "manipuri", "ur": "urdu",
}


def _docker_lang(lang: str) -> str:
    return LANG_NAMES.get(lang, lang) if LANG_STYLE == "name" else lang


def run_page_ocr(image_path: str, lang: str):
    """Run V-01.13.01.00 page OCR on one image. Returns the contents of out.json."""
    with tempfile.TemporaryDirectory(prefix="page_ocr_") as data_dir:
        shutil.copy2(image_path, os.path.join(data_dir, os.path.basename(image_path)))
        cmd = [DOCKER_BIN, "run", "--rm", "-v", f"{data_dir}:/data",
               PAGE_OCR_IMAGE, _docker_lang(lang)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=PAGE_OCR_TIMEOUT)
        except FileNotFoundError:
            raise RuntimeError(f"'{DOCKER_BIN}' not found on the server.")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Page OCR timed out after {PAGE_OCR_TIMEOUT}s.")

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise RuntimeError(f"Page OCR failed ({PAGE_OCR_IMAGE}): {tail}")

        out_path = os.path.join(data_dir, "out.json")
        if not os.path.isfile(out_path):
            raise RuntimeError("Page OCR finished but out.json was not created.")
        with open(out_path, encoding="utf-8") as f:
            return json.load(f)
