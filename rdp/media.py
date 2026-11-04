"""Media for the deck: every picture is prepared locally before it goes to Google.

The Slides API cannot round image corners and can only insert pictures that
Google can download by URL. So the builder never talks to Google about media:
each picture is baked here into a PNG (cover-crop to the slot, anti-aliased
rounded corners at 400 ppi) and saved into the plan folder. The builder puts a
placeholder ``rdp-media://<key>`` into the requests; after the files are
uploaded to Drive, ``finalize`` swaps placeholders for public Drive URLs.

Sources accepted in a manifest: ``https://…``, ``file:///abs/path``, an
absolute path, a path relative to the manifest, or ``assets/…`` (relative to
the repo).
"""
import io
import os
import urllib.request
from urllib.parse import urlparse, unquote

from PIL import Image, ImageDraw

PX_PER_IN = 400          # bake resolution — crisp on full-screen and in PDF
SUPERSAMPLE = 8          # corner mask drawn 8x and downsampled (no pixel staircase)
PLACEHOLDER = "rdp-media://"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(src, base_dir=None):
    """Return a local path or an http(s) URL for a manifest media reference."""
    if src.startswith(("http://", "https://")):
        return src
    if src.startswith("file://"):
        return unquote(urlparse(src).path)
    if os.path.isabs(src):
        return src
    if src.startswith("assets/"):
        return os.path.join(REPO, src)
    return os.path.join(base_dir or os.getcwd(), src)


def read_bytes(src, base_dir=None):
    loc = resolve(src, base_dir)
    if loc.startswith(("http://", "https://")):
        req = urllib.request.Request(loc, headers={"User-Agent": "rd-presentations"})
        return urllib.request.urlopen(req, timeout=60).read()
    with open(loc, "rb") as f:
        return f.read()


def aspect(src, base_dir=None):
    with Image.open(io.BytesIO(read_bytes(src, base_dir))) as im:
        return im.width / im.height


def bake_bytes(raw, w_in, h_in, radius_in):
    """Cover-crop to w×h inches and round the corners (radius 0 = square)."""
    img = Image.open(io.BytesIO(raw))
    img.seek(0)                      # gif → first frame (animation is not kept)
    img = img.convert("RGBA")
    tw = max(1, int(round(w_in * PX_PER_IN)))
    th = max(1, int(round(h_in * PX_PER_IN)))
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    img = img.resize((max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))), Image.LANCZOS)
    rw, rh = img.size
    left, top = (rw - tw) // 2, (rh - th) // 2
    img = img.crop((left, top, left + tw, top + th))
    rad = int(round(radius_in * PX_PER_IN))
    if rad > 0:
        ss = SUPERSAMPLE
        big = Image.new("L", (tw * ss, th * ss), 0)
        ImageDraw.Draw(big).rounded_rectangle([0, 0, tw * ss - 1, th * ss - 1], radius=rad * ss, fill=255)
        img.putalpha(big.resize((tw, th), Image.LANCZOS))
    out = io.BytesIO()
    if rad > 0 or img.getextrema()[3][0] < 255:
        img.save(out, format="PNG", optimize=True)
    else:
        img.convert("RGB").save(out, format="JPEG", quality=92)
    return out.getvalue()


# Овал номера страницы — контур из канона дизайнера (Keynote → PDF, 41×50 pt):
# 12 кубических сегментов в долях бокса. Это не эллипс: бока прямее.
PAGE_OVAL = [((0.5, 0.0), (0.5583, 0.0), (0.6162, 0.0084), (0.671, 0.0247)), ((0.671, 0.0247), (0.8099, 0.0662), (0.9193, 0.1559), (0.9698, 0.2698)), ((0.9698, 0.2698), (0.994, 0.3152), (1.0, 0.4242), (1.0, 0.5)), ((1.0, 0.5), (1.0, 0.5758), (0.994, 0.6848), (0.9698, 0.7302)), ((0.9698, 0.7302), (0.9193, 0.8441), (0.8099, 0.9338), (0.671, 0.9753)), ((0.671, 0.9753), (0.6162, 0.9916), (0.5583, 1.0), (0.5, 1.0)), ((0.5, 1.0), (0.4417, 1.0), (0.3838, 0.9916), (0.329, 0.9753)), ((0.329, 0.9753), (0.1901, 0.9338), (0.0807, 0.8441), (0.0302, 0.7302)), ((0.0302, 0.7302), (0.006, 0.6848), (0.0, 0.5758), (0.0, 0.5)), ((0.0, 0.5), (0.0, 0.4242), (0.006, 0.3152), (0.0302, 0.2698)), ((0.0302, 0.2698), (0.0807, 0.1559), (0.1901, 0.0662), (0.329, 0.0247)), ((0.329, 0.0247), (0.3838, 0.0084), (0.4417, 0.0), (0.5, 0.0))]


def _oval_polygon(w, h, steps=24):
    pts = []
    for p0, p1, p2, p3 in PAGE_OVAL:
        for i in range(steps):
            t = i / steps
            a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
            pts.append(((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0]) * (w - 1),
                        (a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]) * (h - 1)))
    return pts


class LocalMedia:
    """Collects baked media into ``<plan>/media`` and hands out placeholders."""

    def __init__(self, out_dir, base_dir=None):
        self.dir = os.path.join(out_dir, "media")
        os.makedirs(self.dir, exist_ok=True)
        self.base_dir = base_dir
        self.cache = {}
        self.items = []   # [{"key", "file", "source"}]

    def _store(self, cache_key, data, source):
        if cache_key in self.cache:
            return self.cache[cache_key]
        n = len(self.items) + 1
        ext = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
        key = f"m{n:03d}"
        path = os.path.join(self.dir, f"{key}.{ext}")
        with open(path, "wb") as f:
            f.write(data)
        self.items.append({"key": key, "file": os.path.relpath(path, os.path.dirname(self.dir)),
                           "source": source})
        ph = PLACEHOLDER + key
        self.cache[cache_key] = ph
        return ph

    def bake(self, src, w_in, h_in, radius_in):
        key = ("bake", src, round(w_in, 3), round(h_in, 3), round(radius_in, 3))
        if key in self.cache:
            return self.cache[key]
        return self._store(key, bake_bytes(read_bytes(src, self.base_dir), w_in, h_in, radius_in), src)

    def shape(self, kind, w_in, h_in, fill_hex):
        """Solid capsule / circle baked to PNG at PX_PER_IN with the same
        supersampled mask as image corners: a smooth edge at any zoom. Vector
        ELLIPSE + RECTANGLE pieces render with a stepped, seamed edge in Slides."""
        key = ("shape", kind, round(w_in, 3), round(h_in, 3), fill_hex)
        if key in self.cache:
            return self.cache[key]
        tw = max(1, int(round(w_in * PX_PER_IN)))
        th = max(1, int(round(h_in * PX_PER_IN)))
        ss = SUPERSAMPLE
        big = Image.new("L", (tw * ss, th * ss), 0)
        if kind == "oval":
            ImageDraw.Draw(big).polygon(_oval_polygon(tw * ss, th * ss), fill=255)
        else:
            r = th * ss / 2 if kind == "capsule" else min(tw, th) * ss / 2
            ImageDraw.Draw(big).rounded_rectangle([0, 0, tw * ss - 1, th * ss - 1], radius=r, fill=255)
        h = fill_hex.lstrip("#")
        img = Image.new("RGBA", (tw, th), tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,))
        img.putalpha(big.resize((tw, th), Image.LANCZOS))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return self._store(key, buf.getvalue(), f"{kind}:{fill_hex}")

    def raw(self, src):
        """Logos and other pictures that go in as they are (still re-encoded,
        so Google gets one predictable file per picture)."""
        if src.startswith(PLACEHOLDER):
            return src
        key = ("raw", src)
        if key in self.cache:
            return self.cache[key]
        raw = read_bytes(src, self.base_dir)
        with Image.open(io.BytesIO(raw)) as im:
            im.seek(0)
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, format="PNG", optimize=True)
        return self._store(key, buf.getvalue(), src)
