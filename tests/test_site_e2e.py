"""
Browser tests for index.html (headless Chromium via Playwright).

The OCR and table servers are mocked, so these tests check what the page sends
and how it shows the answer, not OCR quality. Set OCR_LIVE=1 to let one sample
per domain go to the real servers configured in index.html instead.
"""

import json
import os
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

from conftest import ROOT

LIVE = os.environ.get("OCR_LIVE") == "1"
DOMAINS = ("printed", "handwritten", "table")
VERSIONS = {"printed": "shashiocr", "handwritten": "V-01.11.00.00"}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


class Api:
    """Records what the page sends to /ocr/infer and /layout/table and answers with mock data."""

    def __init__(self):
        self.calls = []

    def text(self, route):
        body = json.loads(route.request.post_data)
        self.calls.append({"api": "text", **{k: body[k] for k in ("language", "modality", "version", "level")},
                           "bytes": len(body["imageContent"][0])})
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps([{"text": f"MOCK {body['language']} {body['modality']} {body['version']}", "meta": {}}]))

    def table(self, route):
        raw = route.request.post_data_buffer or b""
        f = {k: (re.search(rb'name="%s"\r\n\r\n(.*?)\r\n' % k.encode(), raw) or [None, b""])[1].decode()
             for k in ("lang", "modality", "mode")}
        self.calls.append({"api": "table", **f, "bytes": len(raw)})
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"type": "table", "html": f"<table><tr><td>MOCK</td><td>{f['lang']}</td></tr>"
                                                                f"<tr><td>{f['modality']}</td><td>{f['mode']}</td></tr></table>"}))


def open_page(browser, url, api=None, live=False):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("dialog", lambda d: d.accept())
    if not live:
        page.route("**/health", lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok":true}'))
        if api:
            page.route("**/ocr/infer", api.text)
            page.route("**/layout/table", api.table)
    # fonts are not needed for these checks and slow the run down
    page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.goto(url + "/index.html")
    page.wait_for_function("() => typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined'")
    return ctx, page, errors


def wait_idle(page):
    page.wait_for_function("() => !state.busy", timeout=310_000 if LIVE else 20_000)


def manifest_of(page):
    return page.evaluate("() => samples.data")


# ----------------------------------------------------------------------------
# All 21 domain-language combinations, all 105 samples
# ----------------------------------------------------------------------------
def test_all_21_combinations(browser, fixture_site, output_dir):
    site, base = fixture_site
    api = Api()
    ctx, page, errors = open_page(browser, base, api)
    page.wait_for_function("() => samples.data !== null")
    data = manifest_of(page)
    report = []
    for d in DOMAINS:
        page.click(f'#domainSeg button[data-domain="{d}"]')
        assert errors == [], errors
        expect(page.locator(f'#domainSeg button[data-domain="{d}"]')).to_have_attribute("aria-pressed", "true")
        langs = list(data[d])
        assert len(langs) == 7, (d, langs)
        expect(page.locator("#sampleLangs .script")).to_have_count(7)
        expect(page.locator("#runSample")).to_have_text("Extract this table" if d == "table" else "Read this sample")
        for lang in langs:
            page.click(f'#sampleLangs .script[data-lang="{lang}"]')
            expect(page.locator(f'#sampleLangs .script[data-lang="{lang}"]')).to_have_attribute("aria-checked", "true")
            # the main language picker and writing type follow the sample
            assert page.evaluate("() => state.lang") == lang
            expect(page.locator("#sampleGrid .thumb")).to_have_count(5)
            page.wait_for_function("() => [...document.querySelectorAll('#sampleGrid .thumb img')]"
                                   ".every(i => i.complete && i.naturalWidth > 0)")
            assert page.locator("#sampleGrid .thumb.broken").count() == 0
            for k, s in enumerate(data[d][lang]):
                page.click(f'#sampleGrid .thumb[data-k="{k}"]')
                page.wait_for_function("id => state.pages[state.cur] && state.pages[state.cur].sampleId === id", arg=s["id"])
                cur = page.evaluate("() => { const p = state.pages[state.cur]; return {w: p.w, h: p.h, name: p.name, mode: p.tableMode || null}; }")
                assert (cur["w"], cur["h"]) == (s["width"], s["height"]), "viewer opened a different image"
                expect(page.locator("#pageImg")).to_be_visible()
                info = page.locator("#sampleInfo").inner_text()
                assert f"Sample {k + 1} of 5" in info
                # only the images are shown: no scores or report links anywhere on the page
                rail = page.locator("#samplesStep").inner_html()
                assert not re.search(r"CRR|WRR|Report|%", rail), rail
                expect(page.locator("#runSample")).to_be_enabled()
                n_before = len(api.calls)
                page.click("#runSample")
                wait_idle(page)
                assert len(api.calls) == n_before + 1
                call = api.calls[-1]
                status = page.evaluate("() => state.pages[state.cur].status")
                assert status == "done", page.evaluate("() => state.pages[state.cur].error")
                if d == "table":
                    want_mod = s["modality"]
                    want_mode = {True: "table", False: "auto"}.get(s.get("cropped"), "auto")
                    assert call == {**call, "api": "table", "lang": lang, "modality": want_mod, "mode": want_mode}
                    expect(page.locator("#paneTable table td").first).to_have_text("MOCK")
                    expect(page.locator('.tab[data-tab="table"]')).to_have_attribute("aria-selected", "true")
                else:
                    assert call == {**call, "api": "text", "language": lang, "modality": d, "version": VERSIONS[d], "level": "word"}
                    page.click('.tab[data-tab="doc"]')
                    expect(page.locator("#doc")).to_contain_text(f"MOCK {lang} {d} {VERSIONS[d]}")
                report.append({"domain": d, "language": lang, "sample": k + 1, "src": s["src"],
                               "request": call, "status": status})
            if lang == langs[0]:
                page.screenshot(path=str(output_dir / f"e2e_{d}_{lang}.png"))
            # start the next language with an empty page list (also exercises "Remove all")
            page.click("#clearPages")
    assert len(report) == 105
    assert len({(r["domain"], r["language"]) for r in report}) == 21
    (output_dir / "e2e_21_combinations.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert errors == [], errors
    ctx.close()


def test_sample_opened_once_and_reused(browser, fixture_site):
    _, base = fixture_site
    api = Api()
    ctx, page, errors = open_page(browser, base, api)
    page.wait_for_function("() => samples.data !== null")
    page.click('#domainSeg button[data-domain="printed"]')
    page.dblclick('#sampleGrid .thumb[data-k="2"]')
    page.click('#sampleGrid .thumb[data-k="2"]')
    page.wait_for_function("() => state.pages.length === 1")
    page.click("#runSample")
    wait_idle(page)
    page.click("#runSample")
    wait_idle(page)
    assert page.evaluate("() => state.pages.length") == 1
    assert len(api.calls) == 2
    assert errors == []
    ctx.close()


def test_controls_locked_while_running(browser, fixture_site):
    _, base = fixture_site
    ctx, page, errors = open_page(browser, base)
    gate = {"route": None}
    page.route("**/ocr/infer", lambda r: gate.__setitem__("route", r))  # hold the request
    page.wait_for_function("() => samples.data !== null")
    page.click('#domainSeg button[data-domain="handwritten"]')
    page.click('#sampleGrid .thumb[data-k="0"]')
    page.wait_for_function("() => state.pages.length === 1")
    page.click("#runSample")
    page.wait_for_function("() => state.busy")
    for sel in ("#runSample", '#domainSeg button[data-domain="printed"]', "#sampleLangs .script >> nth=0",
                "#sampleGrid .thumb >> nth=1", "#scripts .script >> nth=0"):
        expect(page.locator(sel)).to_be_disabled()
    page.click("#cancelRun")
    wait_idle(page)
    expect(page.locator("#runSample")).to_be_enabled()
    ctx.close()


def test_missing_image_and_bad_manifest_entries(browser, fixture_site, tmp_path):
    site, base = fixture_site
    manifest = json.loads((site / "samples" / "manifest.json").read_text(encoding="utf-8"))
    hi = manifest["domains"]["printed"]["hi"]
    broken = dict(hi[0], src="samples/printed/hi/does-not-exist.png", thumb="samples/printed/hi/does-not-exist.jpg", id="broken-1")
    bad = {"version": 1, "domains": {"printed": {"hi": [broken, *hi[1:]], "xx": hi[:1], "te": "not a list"},
                                     "handwritten": {}, "table": None}}
    ctx, page, errors = open_page(browser, base)
    page.route("**/samples/manifest.json", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(bad)))
    page.reload()
    page.wait_for_function("() => samples.data !== null")
    page.click('#domainSeg button[data-domain="printed"]')
    expect(page.locator("#sampleLangs .script")).to_have_count(1)        # only hi survives
    expect(page.locator("#sampleGrid .thumb.broken")).to_have_count(1)
    page.click('#sampleGrid .thumb[data-k="0"]')
    expect(page.locator(".toast.bad")).to_contain_text("could not be opened: the image is missing (HTTP 404)")
    assert page.evaluate("() => state.pages.length") == 0
    page.click('#domainSeg button[data-domain="table"]')
    expect(page.locator("#sampleInfo")).to_have_text("No reference samples have been added for table extraction yet.")
    expect(page.locator("#runSample")).to_be_disabled()
    ctx.close()


# ----------------------------------------------------------------------------
# The repository as it is (real samples/manifest.json) and the existing workflow
# ----------------------------------------------------------------------------
def test_repo_page_and_existing_upload_workflow(browser, repo_site, tmp_path, output_dir):
    api = Api()
    ctx, page, errors = open_page(browser, repo_site, api)
    page.wait_for_function("() => samples.data !== null || samples.error !== null")
    assert page.evaluate("() => samples.error") is None
    real = manifest_of(page)
    for d in DOMAINS:
        page.click(f'#domainSeg button[data-domain="{d}"]')
        n = len(real[d])
        if n:
            expect(page.locator("#sampleLangs .script")).to_have_count(n)
        else:
            expect(page.locator("#sampleInfo")).to_contain_text("No reference samples have been added")
    # existing upload -> convert -> extract table flow, unchanged
    from conftest import fixture_png
    img = tmp_path / "upload.png"
    img.write_bytes(fixture_png("upload", 640, 880, 5))
    page.click("#mPrinted")
    page.click('#scripts .script:has-text("Telugu")')
    page.set_input_files("#fileInput", str(img))
    page.wait_for_function("() => state.pages.length === 1")
    page.click("#convertPage")
    wait_idle(page)
    assert api.calls[-1]["api"] == "text" and api.calls[-1]["language"] == "te" and api.calls[-1]["modality"] == "printed"
    expect(page.locator("#doc")).to_contain_text("MOCK te printed shashiocr")
    page.check("#cropped")
    page.click("#extractTable")
    wait_idle(page)
    assert api.calls[-1] == {**api.calls[-1], "api": "table", "lang": "te", "modality": "printed", "mode": "table"}
    page.click("#mHand")
    expect(page.locator("#mHand")).to_have_attribute("aria-pressed", "true")
    # every language tile in the main picker is still enabled/disabled by its own index
    states = page.evaluate("() => [...document.querySelectorAll('#scripts .script')].map(b => b.disabled)")
    support = page.evaluate("() => LANGS.map(l => !SUPPORT.handwritten.has(l.code))")
    assert states == support
    page.screenshot(path=str(output_dir / "e2e_repo_page.png"))
    assert errors == [], errors
    ctx.close()


def test_large_page_is_read_in_bands_after_gateway_timeout(browser, repo_site):
    """The real OCR gateway gives up after ~60 s (504). A page that hits that is cut into
    bands at blank rows and each band is read; the text comes back joined, in page order."""
    import base64, io
    from PIL import Image

    seen = []

    def text(route):
        body = json.loads(route.request.post_data)
        h = Image.open(io.BytesIO(base64.b64decode(body["imageContent"][0]))).height
        seen.append(h)
        if h > 1300:
            route.fulfill(status=504, content_type="text/html", body="<html>504 Gateway Time-out</html>")
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps([{"text": f"BAND {len(seen)} h={h}"}]))

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/health", lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok":true}'))
    page.route("**/ocr/infer", text)
    page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.goto(repo_site + "/index.html")
    page.wait_for_function("() => samples.data !== null")
    if not manifest_of(page)["printed"]:
        pytest.skip("no printed samples in samples/manifest.json")
    page.click('#domainSeg button[data-domain="printed"]')
    page.click('#sampleGrid .thumb[data-k="0"]')
    page.wait_for_function("() => state.pages.length > 0")
    page.click("#runSample")
    wait_idle(page)
    p = page.evaluate("() => { const p = state.pages[state.cur]; return {status: p.status, text: p.text, error: p.error}; }")
    assert p["status"] == "done", p
    full = seen[0]
    assert full > 1300 and len(seen) == 3, seen          # one full try (no 504 retries), then two bands
    assert abs(seen[1] + seen[2] - full) <= 1, seen       # the bands cover the page exactly once
    assert p["text"].splitlines() == [f"BAND 2 h={seen[1]}", f"BAND 3 h={seen[2]}"], p["text"]
    assert not errors, errors
    ctx.close()


@pytest.mark.skipif(not LIVE, reason="set OCR_LIVE=1 to call the real OCR servers")
def test_live_one_sample_per_domain(browser, repo_site, output_dir):
    ctx, page, errors = open_page(browser, repo_site, live=True)
    page.wait_for_function("() => samples.data !== null")
    data = manifest_of(page)
    results = []
    for d in DOMAINS:
        if not data[d]:
            continue  # domain not filled yet (e.g. tables); the others are still checked
        page.click(f'#domainSeg button[data-domain="{d}"]')
        page.click('#sampleGrid .thumb[data-k="0"]')
        page.wait_for_function("() => state.pages.length > 0")
        page.click("#runSample")
        wait_idle(page)
        p = page.evaluate("() => { const p = state.pages[state.cur]; return {name: p.name, status: p.status, error: p.error, text: p.text, html: p.html}; }")
        p["domain"] = d
        results.append(p)
    if not results:
        pytest.skip("samples/manifest.json has no samples yet")
    (output_dir / "live_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    assert all(r["status"] == "done" for r in results), results
    ctx.close()
