"""Smoke: the example manifest builds into a valid plan without any Google keys."""
import io
import json
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rdp.build import build_plan, DISPATCH  # noqa: E402
from rdp import apply  # noqa: E402
from rdp.media import bake_bytes, PLACEHOLDER  # noqa: E402


def test_example_builds(tmp_path):
    plan = build_plan(os.path.join(ROOT, "examples", "example.yaml"), str(tmp_path))
    assert plan["slides"] == 10
    reqs = json.load(open(tmp_path / "requests.json", encoding="utf-8"))
    media = json.load(open(tmp_path / "media.json", encoding="utf-8"))
    assert plan["requests"] == len(reqs) > 0
    assert len(media) == plan["media"] > 0
    for it in media:
        assert (tmp_path / it["file"]).exists()
    slides = [r for r in reqs if "createSlide" in r]
    assert len(slides) == plan["slides"]
    images = [r["createImage"]["url"] for r in reqs if "createImage" in r]
    assert images and all(u.startswith(PLACEHOLDER) for u in images)


def test_finalize_substitutes_every_image(tmp_path):
    build_plan(os.path.join(ROOT, "examples", "example.yaml"), str(tmp_path))
    media = json.load(open(tmp_path / "media.json", encoding="utf-8"))
    mm = {it["key"]: f"id_{it['key']}" for it in media}
    json.dump(mm, open(tmp_path / "media_map.json", "w"))
    paths = apply.finalize(str(tmp_path))
    urls = []
    for p in paths:
        body = json.load(open(p, encoding="utf-8"))
        assert 0 < len(body["requests"]) <= apply.BATCH
        urls += [r["createImage"]["url"] for r in body["requests"] if "createImage" in r]
    assert urls and all(u.startswith("https://lh3.googleusercontent.com/d/id_m") and u.endswith("=s0") for u in urls)


def test_rounded_corners_are_transparent():
    src = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 30, 30)).save(src, format="JPEG")
    out = Image.open(io.BytesIO(bake_bytes(src.getvalue(), 2.0, 1.5, 0.109)))
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0                      # corner cut
    assert out.getpixel((out.width // 2, out.height // 2))[3] == 255


def test_every_archetype_in_manifest_doc():
    doc = open(os.path.join(ROOT, "MANIFEST.md"), encoding="utf-8").read()
    for name in DISPATCH:
        assert f"`{name}`" in doc or f"**{name}**" in doc, name
