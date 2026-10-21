"""Per-set image composition for RD decks (METHODOLOGY: «Картинки: аспект и композиция»).

Rules encoded here:
- aspect is untouchable: every box is w = h * aspect of its own image;
- homogeneous sets (same-type frames) -> one even row;
- mixed sets -> anchor + satellites of different scale, light edge overlaps,
  vertical offsets, no tops/bottoms in one line, nothing over text;
- several candidates are generated, scored, the best returned.

compose(...) returns [(x, y, w, h)] in slide inches + back-to-front draw order.
"""
import random

GRID = 0.1  # raster step for coverage, inches


def _inter(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return w * h if w > 0 and h > 0 else 0.0


def _inflate(r, m):
    return (r[0] - m, r[1] - m, r[2] + 2 * m, r[3] + 2 * m)


def is_homogeneous(aspects, tol=0.12):
    """Same-shape frames (video stills, one shoot) read as a series -> even row."""
    return len(aspects) > 1 and (max(aspects) - min(aspects)) / max(aspects) <= tol


def row(aspects, zone, gutter=0.14):
    """Even row of one height, native aspects, centred in the zone."""
    x0, y0, W, H = zone
    h = min(H, (W - gutter * (len(aspects) - 1)) / sum(aspects))
    tw = h * sum(aspects) + gutter * (len(aspects) - 1)
    x = x0 + (W - tw) / 2
    y = y0 + (H - h) / 2
    out = []
    for a in aspects:
        out.append((x, y, h * a, h))
        x += h * a + gutter
    return out, list(range(len(aspects)))


def _score(boxes, zone, forbid):
    x0, y0, W, H = zone
    cols, rows_ = int(W / GRID), int(H / GRID)
    free = covered = 0
    for i in range(cols):
        cx = x0 + (i + 0.5) * GRID
        for j in range(rows_):
            cy = y0 + (j + 0.5) * GRID
            if any(f[0] <= cx <= f[0] + f[2] and f[1] <= cy <= f[1] + f[3] for f in forbid):
                continue
            free += 1
            if any(b[0] <= cx <= b[0] + b[2] and b[1] <= cy <= b[1] + b[3] for b in boxes):
                covered += 1
    cov = covered / max(1, free)
    # air matters: reward filling up to ~55% of the free zone, punish denser packing
    s = 1.6 * min(cov, 0.55) - 2.5 * max(0.0, cov - 0.55)
    n = len(boxes)
    for i in range(n):
        for k in range(i + 1, n):
            a, b = boxes[i], boxes[k]
            ov = _inter(a, b) / min(a[2] * a[3], b[2] * b[3])
            if 0.01 < ov <= 0.10:
                s += 0.02                      # deliberate edge overlap reads as composition
            for ea, eb in ((a[1], b[1]), (a[1] + a[3], b[1] + b[3]), (a[3], b[3])):
                if abs(ea - eb) < 0.06:
                    s -= 0.03                  # tops/bottoms/heights in one line = grid look
    # cohesion: images should form one group, not float apart
    bx0 = min(b[0] for b in boxes); by0 = min(b[1] for b in boxes)
    bx1 = max(b[0] + b[2] for b in boxes); by1 = max(b[1] + b[3] for b in boxes)
    area = sum(b[2] * b[3] for b in boxes)
    s += 0.15 * min(1.0, area / max(1e-6, (bx1 - bx0) * (by1 - by0)))
    return s


TIERS = [((0.70, 1.0), (0.45, 0.85)), ((0.55, 0.85), (0.35, 0.65)), ((0.45, 0.70), (0.30, 0.50))]


MAX_IMAGES = 4  # with text on the slide; more pictures -> split to another slide


def compose(aspects, zone, forbid=(), seed=0, **kw):
    """Mixed set -> anchor (index 0) + satellites; relaxes sizes tier by tier,
    last resort is an even row in the free strip below the text. Never crops.
    Density guard: more than MAX_IMAGES next to text is refused (ValueError) —
    pick the strongest or move the rest to their own slide."""
    if forbid and len(aspects) > MAX_IMAGES:
        raise ValueError(f"{len(aspects)} images next to text > {MAX_IMAGES}: choose the strongest, "
                         "split the rest to a separate slide")
    for anchor_h, sat_h in TIERS:
        res = _compose(aspects, zone, forbid, seed, anchor_h=anchor_h, sat_h=sat_h, **kw)
        if res:
            return res
    x0, y0, W, H = zone
    top = max([f[1] + f[3] for f in forbid] + [y0]) + 0.15
    return row(aspects, (x0, top, W, y0 + H - top))


def _compose(aspects, zone, forbid=(), seed=0, min_short=1.0, trials=800,
             anchor_h=(0.70, 1.0), sat_h=(0.45, 0.85), max_overlap=0.10, gap=0.10):
    """One tier of the random search. Returns (boxes, order) or None."""
    if len(aspects) == 1:
        a = aspects[0]
        x0, y0, W, H = zone
        h = min(H, W / a)
        return [(x0, y0 + (H - h) / 2, h * a, h)], [0]
    rng = random.Random(seed)
    x0, y0, W, H = zone
    forbid = [_inflate(f, gap) for f in forbid]
    best, best_s = None, -1e9

    def ok(box, placed):
        x, y, w, h = box
        if x < x0 - 1e-6 or y < y0 - 1e-6 or x + w > x0 + W + 1e-6 or y + h > y0 + H + 1e-6:
            return False
        if min(w, h) < min_short:
            return False
        if any(_inter(box, f) > 0 for f in forbid):
            return False
        for p in placed:
            ov = _inter(box, p)
            if ov > max_overlap * min(w * h, p[2] * p[3]):
                return False
        return True

    for _ in range(trials):
        a0 = aspects[0]
        ha = H * rng.uniform(*anchor_h)
        if ha * a0 > W * 0.62:
            ha = W * 0.62 / a0
        anchor = None
        for _t in range(30):
            cand = (x0 + rng.uniform(0, max(0.0, W - ha * a0)), y0 + rng.uniform(0, max(0.0, H - ha)), ha * a0, ha)
            if ok(cand, []):
                anchor = cand
                break
        if anchor is None:
            continue
        placed = [anchor]
        fail = False
        for a in aspects[1:]:
            h = ha * rng.uniform(*sat_h)
            w = h * a
            if w > W * 0.5:
                w = W * 0.5
                h = w / a
            if min(w, h) < min_short:  # grow to the minimum readable size, aspect kept
                k = min_short / min(w, h)
                w, h = w * k, h * k
            choice, cs = None, -1e9
            for _t in range(60):
                # satellites hug the group: start near an existing tile edge
                ref = rng.choice(placed)
                side = rng.random()
                if side < 0.5:   # right/left of ref
                    x = ref[0] + ref[2] - w * rng.uniform(0.0, 0.10) if rng.random() < 0.7 else ref[0] - w + w * rng.uniform(0.0, 0.10)
                    y = ref[1] + rng.uniform(-0.35, 0.75) * ref[3]
                else:            # below/above ref
                    y = ref[1] + ref[3] - h * rng.uniform(0.0, 0.10) if rng.random() < 0.7 else ref[1] - h + h * rng.uniform(0.0, 0.10)
                    x = ref[0] + rng.uniform(-0.3, 0.8) * ref[2]
                cand = (x, y, w, h)
                if not ok(cand, placed):
                    continue
                # cheap pick: stay close to the group so the set reads as one composition
                gx = sum(p[0] + p[2] / 2 for p in placed) / len(placed)
                gy = sum(p[1] + p[3] / 2 for p in placed) / len(placed)
                c = -0.15 * ((x + w / 2 - gx) ** 2 + (y + h / 2 - gy) ** 2) ** 0.5 + rng.uniform(0, 1.0)
                if c > cs:
                    choice, cs = cand, c
            if choice is None:
                fail = True
                break
            placed.append(choice)
        if fail:
            continue
        s = _score(placed, zone, forbid)
        if s > best_s:
            best, best_s = placed, s
    if best is None:
        return None
    order = sorted(range(len(best)), key=lambda i: -(best[i][2] * best[i][3]))
    return best, order


def fit(aspect, box, align="center"):
    """Contain one image in a box without cropping. align: center | tl | top."""
    x, y, w, h = box
    if w / h > aspect:
        nw, nh = h * aspect, h
    else:
        nw, nh = w, w / aspect
    if align == "tl":
        return (x, y, nw, nh)
    if align == "top":
        return (x + (w - nw) / 2, y, nw, nh)
    return (x + (w - nw) / 2, y + (h - nh) / 2, nw, nh)
