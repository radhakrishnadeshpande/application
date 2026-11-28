"""Tests for tools/build_samples.py (selection summary -> samples/manifest.json)."""

import json

import build_samples
from conftest import FIXTURE_LANGS, PER_LANG, fixture_png


def test_full_selection_builds_105_samples(fixture_site):
    site, _ = fixture_site
    m = json.loads((site / "samples" / "manifest.json").read_text(encoding="utf-8"))
    assert set(m["domains"]) == {"printed", "handwritten", "table"}
    total = 0
    for dom, langs in m["domains"].items():
        assert len(langs) == 7, dom
        for lang, items in langs.items():
            assert len(items) == PER_LANG, (dom, lang)
            for s in items:
                total += 1
                assert (site / s["src"]).is_file()
                assert (site / s["thumb"]).is_file()
                assert s["source_url"].startswith("http://127.0.0.1")
                # scores and report links stay in the selection summary, not on the site
                assert not {"crr", "wrr", "report_url"} & set(s)
                assert s["width"] > 0 and s["height"] > 0 and len(s["sha256"]) == 64
    assert total == 105
    # language names were mapped to the page's codes, "Odia" -> "ori"
    assert "ori" in m["domains"]["printed"] and "pa" in m["domains"]["table"]
    # table extras carried through
    tel = m["domains"]["table"]["te"]
    assert tel[0]["cropped"] is True and tel[1]["cropped"] is False and "cropped" not in tel[2]
    assert tel[1]["modality"] == "handwritten" and tel[0]["modality"] == "printed"
    assert m["domains"]["printed"]["hi"][0]["modality"] == "printed"


def test_bad_rows_are_reported_and_left_out(remote_images, tmp_path, capsys):
    d, base = remote_images
    (d / "img" / "good1.png").write_bytes(fixture_png("good1", 300, 400, 1))
    (d / "img" / "good2.png").write_bytes(fixture_png("good2", 310, 400, 2))
    (d / "img" / "copy_of_good1.png").write_bytes((d / "img" / "good1.png").read_bytes())
    (d / "img" / "page.html").write_text("<!doctype html><p>not an image</p>")
    csv = tmp_path / "sel.csv"
    csv.write_text(
        "Domain,Lang,Image URL,CRR,WRR,HTML Report\n"
        f"printed,hi,{base}/img/good1.png,98.5%,95,{base}/r.html\n"
        f"Printed Documents,Bangla,{base}/img/good2.png,0.97,0.91,\n"
        f"printed,hi,{base}/img/missing.png,99,96,\n"           # 404
        f"printed,hi,{base}/img/page.html,99,96,\n"             # not an image
        f"printed,hi,{base}/img/good1.png,99,96,\n"             # duplicate URL
        f"printed,hi,{base}/img/copy_of_good1.png,99,96,\n"     # same bytes
        f"printed,Klingon,{base}/img/good2.png,99,96,\n"        # unknown language
        f"printed,hi,,99,96,\n"                                 # missing image URL
        "http://127.0.0.1:9/x.png,printed,hi,1,1,\n",           # garbage row
        encoding="utf-8")
    out = tmp_path / "samples" / "manifest.json"
    code = build_samples.main([str(csv), "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 1
    m = json.loads(out.read_text(encoding="utf-8"))
    assert [s["source_url"] for s in m["domains"]["printed"]["hi"]] == [f"{base}/img/good1.png"]
    assert len(m["domains"]["printed"]["bn"]) == 1
    assert "98.5" not in out.read_text() and "r.html" not in out.read_text()
    assert "missing.png: HTTP 404" in text, text
    assert "did not return an image" in text
    assert "duplicate image_url" in text
    assert "same image file" in text
    assert "language='Klingon'" in text
    assert "missing or unrecognised image_url" in text
    assert "Usable samples: 2" in text


def test_check_mode_writes_nothing(fixture_selection, tmp_path, capsys):
    out = tmp_path / "samples" / "manifest.json"
    code = build_samples.main([str(fixture_selection), "--out", str(out), "--check", "--strict"])
    text = capsys.readouterr().out
    assert code == 0
    assert not out.exists() and not (tmp_path / "samples").exists()
    assert "Fetched and checked 105 images" in text and "Usable samples: 105" in text


def test_strict_flags_wrong_counts(remote_images, tmp_path, capsys):
    d, base = remote_images
    (d / "img" / "solo.png").write_bytes(fixture_png("solo", 320, 400, 3))
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps({"samples": [
        {"domain": "handwritten", "language": "te", "image_url": f"{base}/img/solo.png", "crr": 96, "wrr": 88}]}))
    out = tmp_path / "m.json"
    assert build_samples.main([str(sel), "--out", str(out), "--no-download"]) == 0
    assert build_samples.main([str(sel), "--out", str(out), "--no-download", "--strict"]) == 1
    text = capsys.readouterr().out
    assert "handwritten/te: 1 samples (expected 5)" in text
    m = json.loads(out.read_text())
    assert m["domains"]["handwritten"]["te"][0]["src"] == f"{base}/img/solo.png"


def test_fixture_shape_matches_real_task():
    assert len(FIXTURE_LANGS) == 3 and all(len(v) == 7 for v in FIXTURE_LANGS.values())
