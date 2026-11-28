"""
Shared test fixtures.

All images made here are synthetic and stamped "TEST FIXTURE". They live in
pytest's temp directory and are never written to samples/.
"""

import functools
import http.server
import io
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

OUTPUT = Path(os.environ.get("TEST_OUTPUT_DIR", ROOT / "test-output"))

# 3 domains x 7 languages, the same shape as the real selection (5 samples each).
FIXTURE_LANGS = {
    "Printed Documents": ["Hindi", "Marathi", "Telugu", "Tamil", "Kannada", "Malayalam", "Odia"],
    "Handwritten Documents": ["Hindi", "Telugu", "Tamil", "Kannada", "Malayalam", "Gujarati", "Bengali"],
    "Table Extraction": ["Hindi", "Marathi", "Telugu", "Tamil", "Kannada", "Gujarati", "Punjabi"],
}
PER_LANG = 5


def fixture_png(label: str, width: int, height: int, seed: int) -> bytes:
    """A synthetic page: a few text-like bars and the label. Unique per call."""
    im = Image.new("RGB", (width, height), (255, 255, 255))
    g = ImageDraw.Draw(im)
    g.rectangle([0, 0, width - 1, height - 1], outline=(180, 180, 180))
    g.text((12, 10), "TEST FIXTURE", fill=(200, 0, 0))
    g.text((12, 26), label, fill=(0, 0, 0))
    for i in range(3 + seed % 9):
        y = 50 + i * 22
        g.rectangle([12, y, 12 + (seed * 37 + i * 53) % (width - 40) + 20, y + 9], fill=(40, 40, 90))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory: Path):
    handler = functools.partial(QuietHandler, directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(scope="session")
def output_dir():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    return OUTPUT


@pytest.fixture(scope="session")
def remote_images(tmp_path_factory):
    """A stand-in for the report server: serves the fixture page images."""
    d = tmp_path_factory.mktemp("remote")
    (d / "img").mkdir()
    srv, base = serve(d)
    yield d, base
    srv.shutdown()


@pytest.fixture(scope="session")
def fixture_selection(remote_images):
    """A 105-row selection summary CSV pointing at the fixture image server."""
    d, base = remote_images
    rows = ["domain,language,image_url,CRR (%),WRR (%),report_url,sample_id,modality,cropped"]
    n = 0
    for dom, langs in FIXTURE_LANGS.items():
        for lang in langs:
            for k in range(PER_LANG):
                n += 1
                w, h = 600 + n, 800 + (n % 7) * 40   # unique width identifies the image
                if dom.startswith("Table"):
                    w, h = 900 + n, 500 + (n % 5) * 30
                name = f"{dom.split()[0].lower()}_{lang.lower()}_{k + 1}.png"
                (d / "img" / name).write_bytes(fixture_png(f"{dom} / {lang} / {k + 1}", w, h, n))
                crr = f"{97 + (k % 3) * 0.5:.1f}"
                wrr = f"{90 + k:.1f}"
                modality = "handwritten" if (dom.startswith("Table") and lang == "Telugu" and k == 1) else ""
                cropped = {0: "true", 1: "false"}.get(k, "") if dom.startswith("Table") else ""
                rows.append(f"{dom},{lang},{base}/img/{name},{crr},{wrr},{base}/report.html,p{k + 1},{modality},{cropped}")
    csv_path = d / "selection.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


@pytest.fixture(scope="session")
def fixture_site(tmp_path_factory, fixture_selection):
    """A copy of the site whose samples/ was built by tools/build_samples.py."""
    import build_samples

    site = tmp_path_factory.mktemp("site")
    shutil.copy(ROOT / "index.html", site / "index.html")
    code = build_samples.main([str(fixture_selection), "--out", str(site / "samples" / "manifest.json"), "--strict"])
    assert code == 0, "builder failed on the fixture selection"
    srv, base = serve(site)
    yield site, base
    srv.shutdown()


@pytest.fixture(scope="session")
def repo_site():
    """The repository itself, with its real samples/manifest.json."""
    srv, base = serve(ROOT)
    yield base
    srv.shutdown()
