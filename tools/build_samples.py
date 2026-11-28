#!/usr/bin/env python3
"""
Build samples/manifest.json (the reference samples shown on the page) from the
approved selection summary.

The page shows only the images. CRR, WRR and report URLs in the selection
summary are not copied into the manifest, because everything in samples/ is
published with the site.

The selection summary is a CSV or JSON file with one row per selected page:

    domain,language,image_url,crr,wrr,report_url,sample_id,modality,cropped
    Printed Documents,Hindi,https://.../page_012.png,98.7,95.2,https://.../report.html,page_012,,
    Table Extraction,Telugu,https://.../tbl_3.jpg,96.1,90.4,https://.../report.html,tbl_3,printed,true

Required: domain, language, image_url. Everything else is optional; crr, wrr
and report_url are accepted and ignored.
Column names are matched loosely (case, spaces, "(%)" are ignored, and a few
common aliases are accepted; see COLUMN_ALIASES).

    python tools/build_samples.py selection.csv            # download images, write manifest
    python tools/build_samples.py selection.csv --check    # only check URLs and counts
    python tools/build_samples.py selection.csv --no-download   # link the original URLs

Images are downloaded to samples/<domain>/<lang>/NN.<ext> so the page loads
them from its own origin. A remote URL only works on the page if its server
sends CORS headers, because the page has to read the image to send it to OCR.

Nothing is invented: a URL that cannot be fetched, or a file that is not an
image, is reported and left out, and the script exits with status 1.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "samples" / "manifest.json"

DOMAINS = ("printed", "handwritten", "table")
EXPECTED_PER_LANGUAGE = 5
EXPECTED_LANGUAGES = 7

# Language codes used by index.html (LANGS) and the OCR servers.
LANGUAGE_ALIASES = {
    "hi": ["hindi", "hin"],
    "mr": ["marathi", "mar"],
    "te": ["telugu", "tel"],
    "ta": ["tamil", "tam"],
    "kn": ["kannada", "kan"],
    "ml": ["malayalam", "mal"],
    "gu": ["gujarati", "guj"],
    "pa": ["punjabi", "panjabi", "gurmukhi", "pan"],
    "bn": ["bengali", "bangla", "ben"],
    "asa": ["assamese", "as", "asm"],
    "ori": ["odia", "oriya", "or", "od", "ory"],
    "mni": ["manipuri", "meitei", "meetei"],
    "ur": ["urdu", "urd"],
    "en": ["english", "eng"],
}
LANG_CODE = {}
for code, names in LANGUAGE_ALIASES.items():
    LANG_CODE[code] = code
    for n in names:
        LANG_CODE[n] = code

COLUMN_ALIASES = {
    "domain": ["domain", "category", "task", "type", "dataset"],
    "language": ["language", "lang", "language_code", "lang_code"],
    "image_url": ["image_url", "image", "img_url", "img", "page_image", "image_path", "url"],
    "sample_id": ["sample_id", "page_id", "id", "page", "image_name", "name"],
    "modality": ["modality", "writing"],
    "cropped": ["cropped", "is_cropped", "table_cropped"],
}

IMAGE_TYPES = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/bmp": ".bmp", "image/tiff": ".tif", "image/gif": ".gif",
}
MAGIC = [
    (b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"RIFF", ".webp"),
    (b"BM", ".bmp"), (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"), (b"GIF8", ".gif"),
]
THUMB_WIDTH = 240


def norm_key(k: str) -> str:
    k = re.sub(r"\(.*?\)", "", str(k)).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", k).strip("_")


def norm_domain(v: str) -> str | None:
    s = str(v).strip().lower()
    if "table" in s:
        return "table"
    if "hand" in s or s in ("hw", "htr"):
        return "handwritten"
    if "print" in s or s in ("pt", "pr"):
        return "printed"
    return None


def norm_language(v: str) -> str | None:
    return LANG_CODE.get(re.sub(r"[^a-z]", "", str(v).strip().lower()))


def norm_bool(v) -> bool | None:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y"):
        return True
    if s in ("0", "false", "no", "n"):
        return False
    return None


def map_columns(row: dict) -> dict:
    keys = {norm_key(k): k for k in row}
    out = {}
    for field, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in keys:
                out[field] = row[keys[a]]
                break
    return out


def read_selection(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("samples"), list):
            data = data["samples"]
        if isinstance(data, dict):  # {domain: {language: [rows]}}
            rows = []
            for dom, langs in data.items():
                for lang, items in langs.items():
                    for it in items:
                        rows.append({"domain": dom, "language": lang, **it})
            data = rows
        if not isinstance(data, list):
            raise SystemExit(f"{path}: expected a list of samples")
        return [r for r in data if isinstance(r, dict)]
    return list(csv.DictReader(io.StringIO(text)))


def sniff_ext(data: bytes, content_type: str, url: str) -> str | None:
    for magic, ext in MAGIC:
        if data.startswith(magic):
            if ext == ".webp" and data[8:12] != b"WEBP":
                continue
            return ext
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in IMAGE_TYPES and not data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return IMAGE_TYPES[ct]
    return None


def fetch(url: str, timeout: float) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "ocr-sample-builder/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def image_size(data: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        raise ValueError("the file is not a readable image")


def write_thumb(data: bytes, dest: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if im.width > THUMB_WIDTH:
            im = im.resize((THUMB_WIDTH, max(1, round(im.height * THUMB_WIDTH / im.width))))
        im.save(dest, "JPEG", quality=82)
    return True


def build(args) -> int:
    sel_path = Path(args.selection)
    raw_rows = read_selection(sel_path)
    problems: list[str] = []   # rows left out
    warnings: list[str] = []   # counts that differ from 7 x 5

    groups: dict[str, dict[str, list[dict]]] = {d: defaultdict(list) for d in DOMAINS}
    seen_urls: dict[str, str] = {}
    for n, row in enumerate(raw_rows, start=2 if sel_path.suffix.lower() == ".csv" else 1):
        r = map_columns(row)
        where = f"row {n}"
        dom = norm_domain(r.get("domain", ""))
        lang = norm_language(r.get("language", ""))
        url = str(r.get("image_url", "") or "").strip()
        missing = [k for k, v in (("domain", dom), ("language", lang), ("image_url", url)) if v in (None, "")]
        if missing:
            problems.append(f"{where}: missing or unrecognised {', '.join(missing)} "
                            f"(domain={r.get('domain')!r}, language={r.get('language')!r})")
            continue
        if url in seen_urls:
            problems.append(f"{where}: duplicate image_url, already used at {seen_urls[url]}: {url}")
            continue
        seen_urls[url] = where
        modality = str(r.get("modality") or "").strip().lower()
        if dom == "table":
            modality = "handwritten" if modality.startswith("hand") else "printed"
        else:
            modality = dom
        groups[dom][lang].append({
            "row": where,
            "source_url": url,
            "modality": modality,
            "cropped": norm_bool(r.get("cropped")) if dom == "table" else None,
            "sample_id": str(r.get("sample_id") or "").strip() or None,
        })

    out_path = Path(args.out)
    samples_dir = out_path.parent
    manifest = {
        "version": 1,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": sel_path.name,
        "domains": {d: {} for d in DOMAINS},
    }
    hashes: dict[str, str] = {}
    fetched = 0
    for dom in DOMAINS:
        for lang in sorted(groups[dom]):
            kept = []
            for item in groups[dom][lang]:
                url = item.pop("source_url")
                row = item.pop("row")
                src = url
                entry = {"src": None, "thumb": None, "source_url": url, **item}
                is_remote = urlparse(url).scheme in ("http", "https")
                if args.check or args.download:
                    try:
                        if is_remote:
                            data, ctype = fetch(url, args.timeout)
                        else:
                            p = (sel_path.parent / url).resolve()
                            data, ctype = p.read_bytes(), ""
                        ext = sniff_ext(data, ctype, url)
                        if not ext:
                            raise ValueError("the URL did not return an image "
                                             f"(Content-Type {ctype or 'unknown'})")
                        size = image_size(data)
                    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
                        if isinstance(e, urllib.error.HTTPError):
                            reason = f"HTTP {e.code} {e.reason}"
                        else:
                            reason = getattr(e, "reason", None) or e
                        problems.append(f"{row}: {dom}/{lang}: cannot use {url}: {reason}")
                        continue
                    fetched += 1
                    digest = hashlib.sha256(data).hexdigest()
                    if digest in hashes:
                        problems.append(f"{row}: {dom}/{lang}: same image file as {hashes[digest]}: {url}")
                        continue
                    hashes[digest] = f"{dom}/{lang} ({url})"
                    entry["sha256"] = digest
                    if size:
                        entry["width"], entry["height"] = size
                    if args.download and not args.check:
                        k = len(kept) + 1
                        d = samples_dir / dom / lang
                        d.mkdir(parents=True, exist_ok=True)
                        name = f"{k:02d}{ext}"
                        (d / name).write_bytes(data)
                        src = f"samples/{dom}/{lang}/{name}"
                        if write_thumb(data, d / f"{k:02d}.thumb.jpg"):
                            entry["thumb"] = f"samples/{dom}/{lang}/{k:02d}.thumb.jpg"
                entry["src"] = src
                entry["id"] = f"{dom}-{lang}-{len(kept) + 1}"
                kept.append({k: v for k, v in entry.items() if v is not None})
            if kept:
                manifest["domains"][dom][lang] = kept

    for dom in DOMAINS:
        langs = manifest["domains"][dom]
        if len(langs) != EXPECTED_LANGUAGES:
            warnings.append(f"{dom}: {len(langs)} languages (expected {EXPECTED_LANGUAGES})")
        for lang, items in langs.items():
            if len(items) != EXPECTED_PER_LANGUAGE:
                warnings.append(f"{dom}/{lang}: {len(items)} samples (expected {EXPECTED_PER_LANGUAGE})")
    total = sum(len(v) for d in manifest["domains"].values() for v in d.values())

    print(f"Read {len(raw_rows)} rows from {sel_path}")
    if args.check or args.download:
        print(f"Fetched and checked {fetched} images")
    for dom in DOMAINS:
        counts = ", ".join(f"{l}={len(v)}" for l, v in manifest["domains"][dom].items()) or "none"
        print(f"  {dom:<12} {counts}")
    print(f"Usable samples: {total}")
    if problems:
        print(f"\nLeft out ({len(problems)}):")
        rownum = lambda t: int(m.group(1)) if (m := re.match(r"row (\d+)", t)) else 0
        for p in sorted(problems, key=rownum):
            print("  - " + p)
    if warnings:
        print(f"\nCounts ({len(warnings)}):")
        for w in warnings:
            print("  - " + w)

    if args.check:
        print("\n--check: manifest not written.")
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {out_path}")
    return 1 if problems or (warnings and args.strict) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("selection", help="selection summary, .csv or .json")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="manifest path (default samples/manifest.json)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--no-download", dest="download", action="store_false",
                   help="keep the original image URLs instead of copying the images")
    g.add_argument("--check", action="store_true", help="fetch and check every image, write nothing")
    ap.add_argument("--strict", action="store_true", help="also fail when counts are not 7 languages x 5 samples")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds per image download")
    ap.set_defaults(download=True)
    return build(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
