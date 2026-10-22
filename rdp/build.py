"""RD deck builder — designer-template canon of Radugadesign.

Reads the style spec (dna.yaml) and a content manifest (archetype + text +
media per slide) and produces a *plan*: the full list of Google Slides API
requests plus every picture baked locally (crop to slot, rounded corners).
The plan is executed later by either path in ``rdp/apply.py`` — the agent's
Google integration or a direct OAuth token. No Google calls happen here, so a
build is reproducible and testable without keys.

Honest limits: the Slides API cannot round corners, so rounding is baked into
the PNG; GIFs are flattened to their first frame; real video stays square.
"""
import os
import zlib
import random
import json
import yaml
from .composer import compose, row as comp_row, is_homogeneous, fit as comp_fit
from .media import LocalMedia, aspect as media_aspect, PLACEHOLDER

BASE = os.path.dirname(os.path.abspath(__file__))
EMU = 914400  # per inch
DNA = yaml.safe_load(open(os.path.join(BASE, "dna.yaml"), encoding="utf-8"))

# ---- style constants pulled from DNA ---------------------------------------
MARGIN_IN = DNA["grid"]["left_margin_in"]
CARD_X = DNA["grid"]["card_x_in"]
CARD_W = DNA["grid"]["card_width_in"]
TS = DNA["type_scale"]
COL = DNA["colors"]
BG_DARK = DNA["canvas"]["background"]
DOT = "●"  # ● header/inline marker
DINGBATS = "➊➋➌➍➎➏"  # ➊➋➌➍➎➏

BAKER = None
RADIUS_IN = DNA["media"]["corner_radius_in"]


def rounded(url, w, h):
    """Baked (cropped+rounded) picture for tile/hero/cluster media —
    NOT for full-bleed backgrounds."""
    return BAKER.bake(url, w, h, RADIUS_IN)


def fullbleed_url(url):
    """Cover-crop a bg to 16:9 (no rounding) so it fills the slide without
    distortion."""
    return BAKER.bake(url, 10.0, 5.625, 0.0)


def emu(inch):
    return int(round(inch * EMU))


def hex_rgb(h):
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def text_w(text, size_pt):
    """Rough Inter text width in inches (for pill sizing)."""
    return len(text) * size_pt * 0.0082


class Builder:
    def __init__(self):
        self.reqs = []
        self.n = 0

    def _id(self, pfx):
        self.n += 1
        return f"{pfx}_{self.n:04d}"

    def slide(self):
        sid = self._id("s")
        self.reqs.append({"createSlide": {"objectId": sid,
            "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
        return sid

    def _elem(self, oid, page, x, y, w, h, shear=False):
        tr = {"scaleX": 1, "scaleY": 1, "translateX": emu(x), "translateY": emu(y), "unit": "EMU"}
        return {"pageObjectId": page,
                "size": {"width": {"magnitude": emu(w), "unit": "EMU"},
                         "height": {"magnitude": emu(h), "unit": "EMU"}},
                "transform": tr}

    def image(self, page, url, x, y, w, h):
        oid = self._id("img")
        if not url.startswith(PLACEHOLDER):
            url = BAKER.raw(url)          # logos etc.: go in as-is, but via the plan
        self.reqs.append({"createImage": {"objectId": oid, "url": url,
            "elementProperties": self._elem(oid, page, x, y, w, h)}})
        return oid

    def _style_range(self, oid, size, color, weight, start=None, end=None):
        rng = {"type": "ALL"} if start is None else {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end}
        self.reqs.append({"updateTextStyle": {"objectId": oid,
            "style": {"fontSize": {"magnitude": size, "unit": "PT"},
                      "foregroundColor": {"opaqueColor": {"rgbColor": hex_rgb(color)}},
                      "weightedFontFamily": {"fontFamily": "Inter", "weight": weight}},
            "textRange": rng, "fields": "fontSize,foregroundColor,weightedFontFamily"}})

    def textbox(self, page, text, x, y, w, h, size, color, weight=400,
                align="START", valign="TOP", line_spacing=None):
        oid = self._id("tb")
        self.reqs.append({"createShape": {"objectId": oid, "shapeType": "TEXT_BOX",
            "elementProperties": self._elem(oid, page, x, y, w, h)}})
        self.reqs.append({"insertText": {"objectId": oid, "insertionIndex": 0, "text": text}})
        self._style_range(oid, size, color, weight)
        if align != "START" or line_spacing is not None:
            style, fields = {}, []
            if align != "START":
                style["alignment"] = align
                fields.append("alignment")
            if line_spacing is not None:
                style["lineSpacing"] = line_spacing
                fields.append("lineSpacing")
            self.reqs.append({"updateParagraphStyle": {"objectId": oid, "style": style,
                "textRange": {"type": "ALL"}, "fields": ",".join(fields)}})
        if valign != "TOP":
            self.reqs.append({"updateShapeProperties": {"objectId": oid,
                "shapeProperties": {"contentAlignment": valign}, "fields": "contentAlignment"}})
        return oid

    def dot_title(self, page, text, x, y, w, h):
        """Slide header, one format for every archetype: «Название ● раздел».
        Name and ● are white, everything after the first ● is the grey
        descriptor. A leading ● is never rendered (RULES §3)."""
        text = text.strip()
        while text.startswith(DOT):
            text = text[len(DOT):].strip()
        oid = self.textbox(page, text, x, y, w, h,
                           TS["title"]["size_pt"], COL["title"], weight=500)
        first = text.find(DOT)
        if first > 0 and text[:first].strip():
            self._style_range(oid, TS["title"]["size_pt"], COL["secondary"], 500,
                              start=first + 1, end=len(text))
        i = 0
        while True:
            j = text.find(DOT, i)
            if j < 0:
                break
            self._style_range(oid, DNA["components"]["dot"]["size_pt_header"],
                              COL["accent"], 500, start=j, end=j + 1)
            i = j + 1
        return oid

    def pill(self, page, text, x, y, solid=True, fill_hex=None, text_hex=None):
        """Pill; returns its width so callers can chain horizontally. Solid pills
        (default, or fill_hex for the dark 'N step' diagram pill) render as a true
        capsule via primitives to match the template's strong rounding. Outline
        pills fall back to ROUND_RECTANGLE (a clean capsule outline isn't
        expressible via the Slides API without seam artifacts)."""
        size = TS["chip"]["size_pt"]
        w = max(0.7, text_w(text, size) + 0.34)
        h = 0.26
        if fill_hex is not None or solid:
            fill = fill_hex if fill_hex is not None else COL["chip_fill"]
            txtc = (text_hex or COL["chip_text"]) if fill_hex is not None else COL["chip_text"]
            self.capsule(page, x, y, w, h, fill)
            self.textbox(page, text, x, y, w, h, size, txtc, 400,
                         align="CENTER", valign="MIDDLE")
            return w
        oid = self._id("pill")
        self.reqs.append({"createShape": {"objectId": oid, "shapeType": "ROUND_RECTANGLE",
            "elementProperties": self._elem(oid, page, x, y, w, h)}})
        self.reqs.append({"insertText": {"objectId": oid, "insertionIndex": 0, "text": text}})
        self._style_range(oid, size, COL["title"], 400)
        self.reqs.append({"updateShapeProperties": {"objectId": oid,
            "shapeProperties": {"shapeBackgroundFill": {"propertyState": "NOT_RENDERED"},
                                "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": hex_rgb(COL["title"])}}},
                                            "weight": {"magnitude": 9525, "unit": "EMU"}, "dashStyle": "SOLID"}},
            "fields": "shapeBackgroundFill,outline"}})
        self.reqs.append({"updateParagraphStyle": {"objectId": oid,
            "style": {"alignment": "CENTER"}, "textRange": {"type": "ALL"}, "fields": "alignment"}})
        self.reqs.append({"updateShapeProperties": {"objectId": oid,
            "shapeProperties": {"contentAlignment": "MIDDLE"}, "fields": "contentAlignment"}})
        return w

    def set_bg(self, page, fill_hex):
        self.reqs.append({"updatePageProperties": {"objectId": page,
            "pageProperties": {"pageBackgroundFill": {"solidFill": {"color": {"rgbColor": hex_rgb(fill_hex)}}}},
            "fields": "pageBackgroundFill.solidFill.color"}})

    def vtext(self, page, text, right_x, top_y, length, thick, size, color):
        oid = self._id("vt")
        self.reqs.append({"createShape": {"objectId": oid, "shapeType": "TEXT_BOX",
            "elementProperties": {"pageObjectId": page,
                "size": {"width": {"magnitude": emu(length), "unit": "EMU"},
                         "height": {"magnitude": emu(thick), "unit": "EMU"}},
                "transform": {"scaleX": 0, "shearX": -1, "shearY": 1, "scaleY": 0,
                              "translateX": emu(right_x), "translateY": emu(top_y), "unit": "EMU"}}}})
        self.reqs.append({"insertText": {"objectId": oid, "insertionIndex": 0, "text": text}})
        self._style_range(oid, size, color, 300)
        return oid

    def ellipse(self, page, x, y, d, fill_hex):
        oid = self._id("el")
        self.reqs.append({"createShape": {"objectId": oid, "shapeType": "ELLIPSE",
            "elementProperties": self._elem(oid, page, x, y, d, d)}})
        self.reqs.append({"updateShapeProperties": {"objectId": oid,
            "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": hex_rgb(fill_hex)}}},
                                "outline": {"propertyState": "NOT_RENDERED"}},
            "fields": "shapeBackgroundFill,outline"}})
        return oid

    def rect(self, page, x, y, w, h, fill_hex):
        """Filled rectangle with no outline — used for the diagram axis line
        and its square nodes."""
        oid = self._id("rect")
        self.reqs.append({"createShape": {"objectId": oid, "shapeType": "RECTANGLE",
            "elementProperties": self._elem(oid, page, x, y, w, h)}})
        self.reqs.append({"updateShapeProperties": {"objectId": oid,
            "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": hex_rgb(fill_hex)}}},
                                "outline": {"propertyState": "NOT_RENDERED"}},
            "fields": "shapeBackgroundFill,outline"}})
        return oid

    def capsule(self, page, x, y, w, h, fill_hex):
        """Perfect stadium/capsule (corner radius = h/2) as a filled rect + two
        end circles of the same colour. The Slides API can't set ROUND_RECTANGLE
        radius, so solid pills are drawn from primitives to match the template's
        strong rounding."""
        r = h / 2
        self.rect(page, x + r, y, max(0.0, w - h), h, fill_hex)
        self.ellipse(page, x, y, h, fill_hex)
        self.ellipse(page, x + w - h, y, h, fill_hex)

    def circle_outline(self, page, cx, cy, d, stroke_hex, weight_emu=9525):
        """Unfilled circle with a thin stroke (venn set) — (cx, cy) is center."""
        oid = self._id("circ")
        self.reqs.append({"createShape": {"objectId": oid, "shapeType": "ELLIPSE",
            "elementProperties": self._elem(oid, page, cx - d / 2, cy - d / 2, d, d)}})
        self.reqs.append({"updateShapeProperties": {"objectId": oid,
            "shapeProperties": {"shapeBackgroundFill": {"propertyState": "NOT_RENDERED"},
                                "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": hex_rgb(stroke_hex)}}},
                                            "weight": {"magnitude": weight_emu, "unit": "EMU"}, "dashStyle": "SOLID"}},
            "fields": "shapeBackgroundFill,outline"}})
        return oid


# ---- chrome ----------------------------------------------------------------
def add_page_number(b, page, page_no):
    pn = DNA["chrome"]["page_number"]
    d = pn["circle_diameter_in"]
    cx, cy = pn["center_in"]["x"], pn["center_in"]["y"]
    b.ellipse(page, cx - d / 2, cy - d / 2, d, "#ffffff")
    # box wider than the circle so two-digit numbers don't wrap inside it
    b.textbox(page, str(page_no), cx - 1.5 * d, cy - d / 2, 3 * d, d,
              pn["size_pt"], pn["color"], align="CENTER", valign="MIDDLE")


def add_copyright(b, page):
    """Vertical legal line hugging the right edge. Drawn on the background
    (called right after the bg in each builder, before content) so it sits
    behind everything. Font auto-sized so the single line fills the full
    slide height without wrapping."""
    cp = DNA["chrome"]["copyright"]
    text = cp["text"]
    margin = 0.03
    length = DNA["canvas"]["height_in"] - 2 * margin   # span the slide height
    # Calibrated Inter width for this vertical legal line (in per pt per char,
    # measured from renders). Size the font so the single line fills the slide
    # height edge-to-edge without wrapping (tiny slack to avoid a 2nd line).
    size = 0.985 * length / (len(text) * 0.00708)
    b.vtext(page, text, cp["anchor_in"]["x"], margin, length, 0.2, size, COL["copyright"])


def add_chrome(b, page, page_no):
    add_page_number(b, page, page_no)


def add_title(b, page, text):
    """Every content header goes through dot_title, so «Название ● раздел»
    renders the same on all archetypes (descriptor after ● in grey)."""
    box = DNA["chrome"]["title"]["box_in"]
    b.dot_title(page, text, box["x"], box["y"], box["w"], box["h"])


# ---- archetype builders ----------------------------------------------------
def build_cover(b, s):
    p = b.slide()
    if s.get("bg"):
        b.image(p, fullbleed_url(s["bg"]), 0, 0, 10.0, 5.625)
    add_title(b, p, s["title"])
    if s.get("subtitle"):
        # подпись-пояснение (что это за документ) — всегда внизу над датой,
        # мельче и серым — так обложки правят руками в студии
        b.textbox(p, s["subtitle"], 0.214, 4.55, 7.3, 0.46,
                  11.2, "#999999", weight=400)
    footer = s.get("footer_pills", [])
    xs = [0.214, 4.28]
    for i, txt in enumerate(footer[:2]):
        b.pill(p, txt, xs[i], 5.10, solid=True)
    if s.get("logo"):
        b.image(p, s["logo"], 7.76, 5.153, 2.02, 0.291)


def build_section(b, s, page_no):
    p = b.slide()
    if s.get("bg"):
        b.image(p, fullbleed_url(s["bg"]), 0, 0, 10.0, 5.625)
    else:
        b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s["title"])
    if s.get("mark"):
        b.image(p, s["mark"], 9.187, 4.962, 0.604, 0.46)
    add_chrome(b, p, page_no)


def build_statement(b, s, page_no):
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    b.textbox(p, s["text"], 0.214, 0.160, 9.3, 4.5,
              TS["title"]["size_pt"], COL["title"], weight=500, line_spacing=112)
    add_chrome(b, p, page_no)


def build_text_block(b, s, page_no):
    """Heading + single body paragraph (the «чистый контент» text case the DNA
    describes but the archetype set lacked). Left text column; right zone left
    free for a hero image the owner places himself."""
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s["title"])
    img = s.get("image")
    b.textbox(p, s["body"], 0.214, 1.15, s.get("body_w", 6.30 if img else 6.7), 3.8,
              TS["body"]["size_pt"], COL["body"], line_spacing=140)
    if img:
        # правая свободная зона: картинка по высоте до линии страничного номера,
        # ширина считается от аспекта — портрет и квадрат садятся одинаково
        bw, bh = 2.83, 3.48
        a = _img_aspect(img)
        w, h = (bw, bw / a) if bw / a <= bh else (bh * a, bh)
        b.image(p, rounded(img, w, h), 6.95 + (bw - w) / 2, 1.15, w, h)
        if s.get("caption"):
            b.textbox(p, s["caption"], 6.95, 1.15 + h + 0.14, bw, 0.9,
                      TS["small"]["size_pt"], COL["body"])
    add_chrome(b, p, page_no)


def build_card_row(b, s, page_no):
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s["title"])
    cards = s["cards"]
    for i, c in enumerate(cards[:4]):
        x = CARD_X[i]
        if c.get("image"):
            ix, iy, iw, ih = comp_fit(_img_aspect(c["image"]), (x, 0.59, CARD_W, 1.53), "tl")
            b.image(p, rounded(c["image"], iw, ih), ix, iy, iw, ih)
        head = f"{DINGBATS[i]}\n{c.get('label','')}"
        b.textbox(p, head, x, 2.13, CARD_W, 0.85, TS["label"]["size_pt"], COL["label"])
        b.textbox(p, c.get("body", ""), x, 3.19, CARD_W, 1.6, TS["body"]["size_pt"], COL["body"])
    add_chrome(b, p, page_no)


def build_stages(b, s, page_no):
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s["title"])
    # intro — вводный абзац над колонками, чтобы не держать под него отдельный слайд
    y_head, y_body = (1.50, 2.565)
    if s.get("intro"):
        b.textbox(p, s["intro"], 0.213, 0.95, 9.35, 1.10,
                  TS["body"]["size_pt"], COL["body"])
        y_head, y_body = 2.30, 3.37
    stages = s["stages"]
    for i, c in enumerate(stages[:4]):
        x = CARD_X[i]
        head = f"{DINGBATS[i]}\n{c.get('label','')}"
        b.textbox(p, head, x, y_head, CARD_W, 1.0, TS["label"]["size_pt"], COL["label"])
        b.textbox(p, c.get("body", ""), x, y_body, CARD_W, 1.8, TS["body"]["size_pt"], COL["body"])
    add_chrome(b, p, page_no)


def build_case_hero(b, s, page_no):
    p = b.slide()
    if s.get("bg"):
        b.image(p, fullbleed_url(s["bg"]), 0, 0, 10.0, 5.625)
    add_copyright(b, p)
    name = s.get("name", "")
    desc = s.get("descriptor", "")
    b.dot_title(p, f"{name} {DOT} {desc}", 0.214, 0.160, 5.2, 0.75)
    x = 0.214
    for txt in s.get("pills", []):
        w = b.pill(p, txt, x, 1.20, solid=True)
        x += w + 0.10
    x += 0.20
    for txt in s.get("meta_pills", []):
        w = b.pill(p, txt, x, 1.20, solid=False)
        x += w + 0.10
    if s.get("right_meta"):
        b.textbox(p, s["right_meta"], 5.951, 0.280, 3.85, 1.2,
                  TS["body"]["size_pt"], COL["body"])
    add_chrome(b, p, page_no)


# case_study left image clusters (exact template geometry, by image count).
CASE_CLUSTERS = {
    1: [(0.214, 0.900, 3.900, 3.550)],
    2: [(-0.123, -2.038, 5.368, 7.375), (-0.410, 2.027, 5.705, 4.077)],
    3: [(-0.945, -1.958, 5.368, 7.375), (1.839, 0.635, 3.889, 2.187), (-0.195, 2.921, 3.061, 2.187)],
}


def build_case_study(b, s, page_no):
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    b.dot_title(p, s.get('name', ''), 0.214, 0.160, 5.0, 0.5)
    imgs = s.get("images", [])
    if imgs:
        # native aspect, per-set composition in the left zone (METHODOLOGY: аспект и композиция)
        asp = [_img_aspect(u) for u in imgs]
        boxes, order = compose(asp, (0.214, 0.75, 5.25, 4.40),
                               seed=zlib.crc32(s.get("name", "").encode()))
        for i in order:
            x, y, w, h = boxes[i]
            b.image(p, rounded(imgs[i], w, h), x, y, w, h)
    if s.get("body"):
        b.textbox(p, s["body"], DNA["grid"]["case_body_x_in"], 2.774,
                  DNA["grid"]["case_body_w_in"], 2.6, TS["body"]["size_pt"], COL["body"])
    add_chrome(b, p, page_no)


# moodboard layouts (inches). "template" from designer p19; app/lifestyle legacy.
MOOD_LAYOUTS = {
    "template": [
        (0.202, 1.784, 6.333, 3.229),
        (2.625, 1.010, 2.668, 1.306),
        (5.103, 0.399, 4.688, 2.021),
        (6.523, 2.254, 1.648, 2.063),
        (8.144, 2.297, 1.667, 0.885),
    ],
    "app": [
        (0.256, 0.958, 4.925, 2.770), (5.502, 0.958, 1.489, 1.934),
        (5.502, 3.090, 1.489, 0.836), (7.178, 0.958, 2.341, 4.000),
        (7.178, 0.958, 2.341, 1.317),
    ],
    "lifestyle": [
        (0.256, 0.951, 3.693, 2.770), (4.773, 0.951, 2.042, 1.532),
        (7.142, 0.854, 1.708, 2.135), (4.301, 2.770, 2.586, 1.453),
        (7.063, 3.189, 2.227, 1.967), (6.278, 2.275, 1.219, 0.914),
    ],
}


def _img_aspect(url):
    """Native width/height of a source image (no crop) — tiles keep their own
    proportions, never distorted."""
    return media_aspect(url, BAKER.base_dir)


def _justified_rows(aspects, W, gutter, target_rows):
    """Flickr-style justified rows: each row scaled to fill width W while every
    tile keeps its native aspect. Returns [(x, y, w, h)...] and total height."""
    target_h = W / max(1, len(aspects)) if target_rows == 1 else W / (2.4 * target_rows)
    rows, cur = [], []
    for i, a in enumerate(aspects):
        cur.append((i, a))
        rw = target_h * sum(x[1] for x in cur) + gutter * (len(cur) - 1)
        if rw >= W:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    out = [None] * len(aspects)
    y = 0.0
    for row in rows:
        sa = sum(a for _, a in row)
        h = (W - gutter * (len(row) - 1)) / sa
        x = 0.0
        for idx, a in row:
            w = h * a
            out[idx] = (x, y, w, h)
            x += w + gutter
        y += h + gutter
    return out, (y - gutter if rows else 0.0)


def _collage(aspects, seed):
    """Curated overlapping collage (Elementor-style), aspect preserved. Tiles
    vary in size, deliberately overlap edges, one hero anchor is enlarged, and
    z-order draws big tiles behind so smaller ones 'land on' them. Returns
    normalized boxes [(x,y,w,h) in 0..1 units] + draw order (back-to-front).
    Deterministic per seed so rebuilds are stable."""
    rng = random.Random(seed)
    n = len(aspects)
    rows = [list(range(n))] if n <= 5 else [list(range(0, (n + 1) // 2)),
                                            list(range((n + 1) // 2, n))]
    REF = 10.0                                       # common row width to fill
    boxes = [None] * n
    y = 0.0
    for row in rows:
        x, hs = 0.0, {}
        for j, idx in enumerate(row):
            h = 0.86 + rng.random() * 0.28           # mild size variation
            w = h * aspects[idx]
            if j > 0:
                x -= (0.03 + rng.random() * 0.06) * h  # small edge overlap only
            vj = (rng.random() - 0.5) * 0.16 * h       # mild vertical jitter
            boxes[idx] = [x, vj, w, h]
            hs[idx] = h
            x += w
        rs = REF / x if x > 0 else 1.0               # scale row to fill REF width
        rh = 0.0
        for idx in row:
            bx = boxes[idx]
            boxes[idx] = [bx[0] * rs, y + bx[1] * rs, bx[2] * rs, bx[3] * rs]
            rh = max(rh, boxes[idx][3])
        y += rh * 0.9                                # rows kiss slightly
    if n >= 4:                                        # one clear enlarged anchor
        hero = rng.randrange(n)
        hx, hy, hw, hh = boxes[hero]
        f = 1.15
        boxes[hero] = [hx + hw / 2 - hw * f / 2, hy + hh / 2 - hh * f / 2, hw * f, hh * f]
    order = sorted(range(n), key=lambda i: -(boxes[i][2] * boxes[i][3]))
    minx = min(bx[0] for bx in boxes)
    miny = min(bx[1] for bx in boxes)
    maxx = max(bx[0] + bx[2] for bx in boxes)
    maxy = max(bx[1] + bx[3] for bx in boxes)
    return boxes, order, (minx, miny, maxx - minx, maxy - miny)


def build_moodboard(b, s, page_no):
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    name = s.get("name", "Мудборд")
    sub = s.get("descriptor", "")
    if sub:
        b.dot_title(p, f"{name} {DOT} {sub}", 0.214, 0.160, 5.0, 0.5)
    else:
        add_title(b, p, s.get("title", name))
    imgs = s.get("images", [])
    caption = s.get("caption")
    if imgs:
        X0, Y0, W = 0.214, 0.95, 9.57
        H = 3.05 if caption else 4.0
        aspects = [_img_aspect(u) for u in imgs]
        if s.get("layout") == "rows":
            # ровные ряды одинаковой высоты
            # до 4 плиток — один ряд, больше — два ряда поровну
            n = len(imgs)
            split = [list(range(n))] if n <= 4 else [list(range((n + 1) // 2)),
                                                     list(range((n + 1) // 2, n))]
            rboxes, rh = [None] * n, 0.0
            for r in split:
                h = (W - 0.14 * (len(r) - 1)) / sum(aspects[i] for i in r)
                x = 0.0
                for i in r:
                    rboxes[i] = (x, rh, h * aspects[i], h)
                    x += h * aspects[i] + 0.14
                rh += h + 0.14
            rh -= 0.14
            sc = min(1.0, H / rh) if rh > 0 else 1.0
            ox = X0 + (W - W * sc) / 2
            oy = Y0 + (H - rh * sc) / 2
            for u, (x, y, w, h) in zip(imgs, rboxes):
                ww, hh = w * sc, h * sc
                b.image(p, rounded(u, ww, hh), ox + x * sc, oy + y * sc, ww, hh)
            imgs = []
    if imgs:
        seed = zlib.crc32(s.get("name", s.get("title", "")).encode())
        boxes, order, (bx, by, bw, bh) = _collage(aspects, seed)
        scale = min(W / bw, H / bh) if bw > 0 and bh > 0 else 1.0
        off_x = X0 + (W - bw * scale) / 2 - bx * scale
        off_y = Y0 + (H - bh * scale) / 2 - by * scale
        for idx in order:                       # back-to-front z-order
            x, y, w, h = boxes[idx]
            xx, yy, ww, hh = off_x + x * scale, off_y + y * scale, w * scale, h * scale
            b.image(p, rounded(imgs[idx], ww, hh), xx, yy, ww, hh)
    if caption:
        b.textbox(p, caption, 0.214, 4.20, 4.6, 1.0,
                  TS["small"]["size_pt"], COL["body"])
    add_chrome(b, p, page_no)


# ---- diagram builders (pipeline p06 / venn p07) ----------------------------
def _diagram_body(b, page, main, tools, x, y, w, h, align="START"):
    """Step caption: main body text (light) + trailing ' ● tool' chips (muted
    grey from the first ● onward)."""
    text = main or ""
    for t in tools or []:
        text += f" {DOT} {t}"  # nbsp keeps ● glued to its tool name on wrap
    oid = b.textbox(page, text, x, y, w, h, TS["body"]["size_pt"], COL["body"], align=align)
    d = text.find(DOT)
    if d >= 0:
        b._style_range(oid, TS["body"]["size_pt"], COL["secondary"], 400, start=d, end=len(text))
    return oid


def build_pipeline(b, s, page_no):
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s.get("title", "Pipeline"))
    D = DNA["diagrams"]
    PL = D["pipeline"]
    cyan = COL["diagram_accent"]
    line_y, t, ns = D["line_y_in"], D["line_thick_in"], D["node_size_in"]
    x0, x1 = D["x_start_in"], D["x_end_in"]
    steps = s["steps"]
    seg = (x1 - x0) / len(steps)
    b.rect(p, x0, line_y - t / 2, x1 - x0, t, cyan)
    for i, st in enumerate(steps):
        cx = x0 + i * seg
        b.rect(p, cx - ns / 2, line_y - ns / 2, ns, ns, cyan)
        label = st.get("label", f"{i + 1} step")
        b.pill(p, label, cx - ns / 2, PL["pill_y_in"],
               fill_hex=COL["diagram_pill_fill"], text_hex=COL["diagram_pill_text"])
        _diagram_body(b, p, st.get("body", ""), st.get("tools", []),
                      cx - ns / 2, PL["body_y_in"], seg - 0.13, 1.9)
    b.rect(p, x1 - ns / 2, line_y - ns / 2, ns, ns, cyan)
    add_chrome(b, p, page_no)


def build_venn(b, s, page_no):
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s.get("title", "Pipeline"))
    D = DNA["diagrams"]
    V = D["venn"]
    cyan = COL["diagram_accent"]
    line_y, t, ns = D["line_y_in"], D["line_thick_in"], D["node_size_in"]
    x0, x1 = D["x_start_in"], D["x_end_in"]
    d, centers = V["diameter_in"], V["centers_x_in"]
    circles = s["circles"]
    if len(circles) == 2:          # two circles: centre the pair on the axis
        mid = (x0 + x1) / 2
        gap = (centers[1] - centers[0]) / 2
        centers = [mid - gap, mid + gap]
    b.rect(p, x0, line_y - t / 2, x1 - x0, t, cyan)
    b.rect(p, x0 - ns / 2, line_y - ns / 2, ns, ns, cyan)
    b.rect(p, x1 - ns / 2, line_y - ns / 2, ns, ns, cyan)
    for i in range(min(len(circles), len(centers))):
        b.circle_outline(p, centers[i], line_y, d, cyan)
    for i, c in enumerate(circles[:len(centers)]):
        cx = centers[i]
        label = c.get("label", f"{i + 1} step")
        pw = max(0.7, text_w(label, TS["chip"]["size_pt"]) + 0.34)
        b.pill(p, label, cx - pw / 2, line_y + V["pill_dy_in"],
               fill_hex=COL["diagram_pill_fill"], text_hex=COL["diagram_pill_text"])
        bw = d - 0.30
        _diagram_body(b, p, c.get("body", ""), c.get("tools", []),
                      cx - bw / 2, line_y + V["body_dy_in"], bw, 1.4, align="CENTER")
    add_chrome(b, p, page_no)


def build_spec(b, s, page_no):
    """Per-installation detail slide: title + small left thumbnail (baked rounded)
    + wide column of labelled blocks (Idea / Mechanics / Content) at 11pt, plus a
    muted size footnote. Blocks sit on a fixed 3-slot grid so bodies stay short.
    The image is a reference thumb; the text carries the slide."""
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s["title"])
    img = s.get("image")
    if img:
        ix, iy, iw, ih = comp_fit(_img_aspect(img), (0.334, 1.194, 2.198, 1.693), "tl")
        b.image(p, rounded(img, iw, ih), ix, iy, iw, ih)
        tx, tw = 2.831, 4.90
    else:
        tx, tw = 0.214, 9.3
    labels = (list(s.get("labels", [])) + ["Идея", "Механика", "Контент"][len(s.get("labels", [])):])[:3]
    blocks = [(labels[0], s.get("idea", "")), (labels[1], s.get("mechanics", "")),
              (labels[2], s.get("content", ""))]
    ys = [1.12, 2.42, 3.72]
    for (lab, body), y in zip(blocks, ys):
        if not body:
            continue
        b.textbox(p, lab, tx, y, tw, 0.28, TS["body"]["size_pt"], COL["title"], weight=600)
        b.textbox(p, body, tx, y + 0.27, tw, 1.05, TS["body"]["size_pt"], COL["body"],
                  line_spacing=126)
    if s.get("size"):
        b.textbox(p, f"{DOT} {s['size']}", tx - 0.037, 4.601, tw, 0.4,
                  TS["small"]["size_pt"], COL["secondary"])
    add_chrome(b, p, page_no)


def build_outro(b, s):
    p = b.slide()
    if s.get("bg"):
        b.image(p, fullbleed_url(s["bg"]), 0, 0, 10.0, 5.625)
    b.textbox(p, s["title"], 0.214, 0.160, 5.0, 0.75,
              TS["title"]["size_pt"], COL["title"], weight=500, line_spacing=112)
    footer = s.get("footer_pills", s.get("contacts", []))
    xs = [0.214, 4.28]
    for i, txt in enumerate(footer[:2]):
        b.pill(p, txt, xs[i], 5.10, solid=True)
    if s.get("logo"):
        b.image(p, s["logo"], 9.187, 4.962, 0.604, 0.46)


def build_setup(b, s, page_no):
    """Вариант решения целиком на одном слайде: описание справа сверху, эскиз и
    референсы — одна композиция в свободной зоне (composer.compose): родной аспект,
    якорь + спутники. Однотипные кадры или layout: row — ровный ряд под текстом."""
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    b.dot_title(p, s.get('name', ''), 0.214, 0.160, 5.0, 0.5)

    if s.get("body"):
        b.textbox(p, s["body"], 5.58, 0.85, 4.20, 2.20,
                  TS["body"]["size_pt"], COL["body"])
    # sketch + refs as one set: native aspect always; mixed set -> composition,
    # same-type frames (or layout: row) -> even row under the text
    imgs = ([s["sketch"]] if s.get("sketch") else []) + list(s.get("refs", []))
    if imgs:
        asp = [_img_aspect(u) for u in imgs]
        zone = (0.214, 0.75, 9.57, 4.40)
        text = (5.58, 0.85, 4.20, 2.20) if s.get("body") else None
        if s.get("layout") == "row" or is_homogeneous(asp):
            boxes, order = comp_row(asp, (0.214, 3.05, 9.57, 2.10))
        else:
            boxes, order = compose(asp, zone, [text] if text else [],
                                   seed=s.get("seed", zlib.crc32(s.get("name", "").encode())))
        for i in order:
            x, y, w, h = boxes[i]
            b.image(p, rounded(imgs[i], w, h), x, y, w, h)
    add_chrome(b, p, page_no)


def build_storyboard(b, s, page_no):
    """Раскадровка блока для сверхширокого экрана: слева колонка лейблов
    (музыка / экран / материал), справа столбик кадров в пропорции экрана
    с подписью под каждым. Пропорция кадра берётся из манифеста (`aspect`,
    по умолчанию 6:1 — например, LED 15×2,5 м)."""
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    b.dot_title(p, s.get('name', '') + (f" {DOT} {s['time']}" if s.get("time") else ""), 0.214, 0.160, 6.0, 0.5)
    y = 0.85
    for lab, body in s.get("blocks", []):
        # высота блока по числу строк (~30 знаков в строке колонки 3.15″ при 11pt, проверено рендером),
        # иначе длинное «Экран» наезжает на «Материал»
        lines = sum(max(1, -(-len(par) // 30)) for par in body.split("\n"))
        bh = lines * 0.195 + 0.08
        b.textbox(p, lab, 0.214, y, 3.15, 0.28, TS["body"]["size_pt"], COL["title"], weight=600)
        b.textbox(p, body, 0.214, y + 0.27, 3.15, bh, TS["body"]["size_pt"], COL["body"],
                  line_spacing=126)
        y += 0.27 + bh + 0.18
    frames = s.get("frames", [])
    asp = s.get("aspect", 6.0)
    fx, fw = 3.70, 6.08
    fh = fw / asp
    n = max(1, len(frames))
    step = min(1.38, 4.10 / n)
    for i, fr in enumerate(frames):
        fy = 0.80 + i * step
        b.image(p, rounded(fr["image"], fw, fh), fx, fy, fw, fh)
        if fr.get("caption"):
            b.textbox(p, fr["caption"], fx, fy + fh + 0.03, fw, 0.3,
                      TS["small"]["size_pt"], COL["body"])
    add_chrome(b, p, page_no)


def build_wide_image(b, s, page_no):
    """Заголовок + одна широкая картинка во всю ширину + подпись снизу
    (таймлайны, схемы). Картинка вписывается по аспекту, не кропается."""
    p = b.slide()
    b.set_bg(p, BG_DARK)
    add_copyright(b, p)
    add_title(b, p, s["title"])
    img = s["image"]
    a = _img_aspect(img)
    W, Hmax = 9.57, 3.2
    w, h = (W, W / a) if W / a <= Hmax else (Hmax * a, Hmax)
    url = BAKER.bake(img, w, h, 0.0)
    b.image(p, url, 0.214, 0.85, w, h)
    if s.get("body"):
        b.textbox(p, s["body"], 0.214, 0.85 + h + 0.15, 9.3, 4.85 - (0.85 + h + 0.15),
                  TS["body"]["size_pt"], COL["body"], line_spacing=126)
    add_chrome(b, p, page_no)


DISPATCH = {
    "storyboard": build_storyboard,
    "wide_image": build_wide_image,
    "setup": build_setup,
    "cover": lambda b, s, n: build_cover(b, s),
    "section_divider": build_section,
    "statement": build_statement,
    "card_row": build_card_row,
    "stages": build_stages,
    "case_hero": build_case_hero,
    "case_study": build_case_study,
    "moodboard": build_moodboard,
    "text_block": build_text_block,
    "spec": build_spec,
    "pipeline": build_pipeline,
    "venn": build_venn,
    "outro": lambda b, s, n: build_outro(b, s),
    # legacy aliases
    "clean_content": build_card_row,
    "options_row": build_stages,
}


def build_plan(manifest_path, out_dir):
    """Build the plan folder: requests.json, media/, media.json, plan.json."""
    global BAKER
    manifest_path = os.path.abspath(manifest_path)
    with open(manifest_path, encoding="utf-8") as f:
        m = yaml.safe_load(f)
    os.makedirs(out_dir, exist_ok=True)
    # a rebuild must not leave stale media, batches or media_map from the old plan
    import glob
    import shutil
    shutil.rmtree(os.path.join(out_dir, "media"), ignore_errors=True)
    for old in glob.glob(os.path.join(out_dir, "batch_*.json")) + [os.path.join(out_dir, "media_map.json")]:
        if os.path.exists(old):
            os.remove(old)
    BAKER = LocalMedia(out_dir, base_dir=os.path.dirname(manifest_path))

    b = Builder()
    page_no = 1
    for sl in m["slides"]:
        a = sl["archetype"]
        fn = DISPATCH.get(a)
        if fn is None:
            raise SystemExit(f"unknown archetype: {a} (see MANIFEST.md)")
        try:
            fn(b, sl, page_no)
        except (ValueError, KeyError, IndexError) as e:
            raise SystemExit(f"слайд {page_no} ({a}): {e}") from e
        page_no += 1

    plan = {"title": m["title"], "slides": len(m["slides"]),
            "requests": len(b.reqs), "media": len(BAKER.items),
            "share": m.get("share", [])}
    with open(os.path.join(out_dir, "requests.json"), "w", encoding="utf-8") as f:
        json.dump(b.reqs, f, ensure_ascii=False)
    with open(os.path.join(out_dir, "media.json"), "w", encoding="utf-8") as f:
        json.dump(BAKER.items, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    return plan
