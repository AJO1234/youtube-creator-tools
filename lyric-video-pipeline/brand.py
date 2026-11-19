#!/usr/bin/env python
"""
Broken & Restored — channel art generator.

Every asset the channel needs, rendered to exact YouTube dimensions with
consistent typography. Design values live in brand.json; batch jobs live in
songs.json. Neither requires touching this file.

  python brand.py banner    --image assets/crack.jpg
  python brand.py thumbnail --image assets/fig01.jpg \
                            --setup "THE DEVIL WASN'T MY PROBLEM" \
                            --payoff "PEOPLE WERE" --out out/thumb_01.png
  python brand.py avatar    --image assets/slab.jpg
  python brand.py watermark --image assets/slab.jpg
  python brand.py batch                       <- every entry in songs.json

Add songs to songs.json and run `batch` to regenerate the whole set.
"""

import argparse
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")


# ---------------- tokens ----------------

def tokens():
    with open(os.path.join(ROOT, "brand.json"), encoding="utf-8") as f:
        t = json.load(f)
    t["colors"] = {k: tuple(v) + (255,) for k, v in t["colors"].items()}
    return t


T = tokens()
C = T["colors"]


def font(role, size):
    return ImageFont.truetype(T["fonts"][role], size)


# ---------------- text helpers ----------------
# PIL has no tracking and no auto-fit, so both are done by hand here.

def text_size(f, s):
    b = f.getbbox(s)
    return b[2] - b[0], b[3] - b[1]


def fit_size(role, strings, target_w, cap=400):
    """Largest font size where the widest string still fits target_w."""
    size = 8
    while size < cap:
        f = font(role, size + 2)
        if max(text_size(f, s)[0] for s in strings) > target_w:
            break
        size += 2
    return size


def tracked_width(text, f, tracking):
    w = 0
    for ch in text:
        w += 0 if ch == " " else text_size(f, ch)[0]
        w += tracking + (f.getlength(" ") if ch == " " else 0)
    return w - tracking


def draw_tracked(d, xy, text, f, fill, tracking):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += 0 if ch == " " else text_size(f, ch)[0]
        x += tracking + (f.getlength(" ") if ch == " " else 0)


def fit_tracking(text, f, target_w):
    """Binary-search the tracking value that lands text on target_w."""
    lo, hi = 0.0, 80.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if tracked_width(text, f, mid) < target_w:
            lo = mid
        else:
            hi = mid
    return lo


def wrap_best(role, text, box_w, box_h, max_lines, line_gap):
    """
    Pick the line-break arrangement that renders largest inside the box.
    Fewer, longer lines win on width; more, shorter lines win on height —
    so try every split and keep whichever yields the biggest type.
    """
    words = text.split()
    best = None
    for n in range(1, min(max_lines, len(words)) + 1):
        for split in _splits(words, n):
            size = fit_size(role, split, box_w)
            f = font(role, size)
            lh = max(text_size(f, s)[1] for s in split)
            total_h = lh * n + (lh * line_gap) * (n - 1)
            if total_h > box_h:
                # too tall: scale the size down to fit and re-measure
                size = max(8, int(size * box_h / total_h))
                f = font(role, size)
                lh = max(text_size(f, s)[1] for s in split)
            if best is None or size > best[0]:
                best = (size, split)
    return best


def wrap_to_width(text, f, tracking, box_w):
    """Greedy wrap at a fixed size — used for the setup line, which should stay
    legible and wrap rather than shrink to fit one row."""
    lines, cur = [], ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if not cur or tracked_width(trial, f, tracking) <= box_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _splits(words, n):
    """All contiguous ways to break `words` into exactly n lines."""
    if n == 1:
        yield [" ".join(words)]
        return
    for i in range(1, len(words) - n + 2):
        head = " ".join(words[:i])
        for rest in _splits(words[i:], n - 1):
            yield [head] + rest


# ---------------- image helpers ----------------

def cover(img, w, h):
    """Scale-and-crop so img fills w x h without distortion."""
    img = img.convert("RGBA")
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    return img.crop(((img.width - w) // 2, (img.height - h) // 2,
                     (img.width - w) // 2 + w, (img.height - h) // 2 + h))


def band_scrim(size, height, strength):
    """Horizontal dark band, feathered top and bottom so it reads as shadow."""
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    top = (h - height) // 2
    for i in range(height):
        t = i / max(1, height - 1)
        a = int(strength * (1 - abs(t - 0.5) * 2) ** 0.6)
        d.line([(0, top + i), (w, top + i)], fill=(0, 0, 0, a))
    return layer


def side_scrim(size, side, frac, strength):
    """Darken one side of the frame, fading toward the middle."""
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # ramp must be strongest at the outer edge and reach zero at the inner
    # boundary, or the scrim ends in a hard vertical seam
    span = int(w * frac)
    for i in range(span):
        t = i / max(1, span - 1)
        a = int(strength * ((1 - t) ** 0.7))
        x = w - 1 - i if side == "left" else i
        d.line([(x, 0), (x, h)], fill=(0, 0, 0, a))
    return layer


def base_canvas(size, image):
    img = Image.new("RGBA", size, C["ink"])
    if image:
        img.alpha_composite(cover(Image.open(image), *size))
    return img


def save(img, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.convert("RGB").save(path) if path.lower().endswith((".jpg", ".jpeg")) else img.save(path)
    print(f"  wrote {os.path.relpath(path, ROOT)}  ({img.width}x{img.height})")


# ---------------- assets ----------------

def make_banner(image, out):
    cfg = T["banner"]
    W, H = cfg["size"]
    SW, SH = cfg["safe"]
    st, sl = (H - SH) // 2, (W - SW) // 2

    img = base_canvas((W, H), image)
    if image:
        img.alpha_composite(band_scrim((W, H), cfg["scrim_height"], cfg["scrim_strength"]))
    d = ImageDraw.Draw(img)

    left, right = cfg["left_word"], cfg["right_word"]
    size = fit_size("display", [left + right], cfg["wordmark_width"] - cfg["crack_gap"])
    f = font("display", size)
    lb, rb = f.getbbox(left), f.getbbox(right)
    lw, rw = lb[2] - lb[0], rb[2] - rb[0]
    total = lw + cfg["crack_gap"] + rw

    wm_h = lb[3] - lb[1]
    block_h = wm_h + cfg["tagline_gap"] + cfg["tagline_size"]
    y = st + (SH - block_h) // 2
    x = W // 2 - total // 2

    d.text((x - lb[0], y - lb[1]), left, font=f, fill=C["bone"])
    d.text((x + lw + cfg["crack_gap"] - rb[0], y - rb[1]), right, font=f, fill=C["gold"])

    lf = font("light", cfg["tagline_size"])
    tag = cfg["tagline"]
    tr = fit_tracking(tag, lf, cfg["tagline_width"])
    tw = tracked_width(tag, lf, tr)
    draw_tracked(d, (W // 2 - tw / 2, y + wm_h + cfg["tagline_gap"]), tag, lf, C["muted"], tr)

    save(img, out)
    return img


def make_thumbnail(image, setup, payoff, out):
    cfg = T["thumbnail"]
    W, H = cfg["size"]
    m = cfg["margin"]
    side = cfg["figure_side"]

    img = base_canvas((W, H), image)
    if image:
        img.alpha_composite(side_scrim((W, H), side, cfg["text_area"], cfg["scrim_strength"]))
    d = ImageDraw.Draw(img)

    box_w = int(W * cfg["text_area"]) - m * 2
    tx = W - box_w - m if side == "left" else m

    # Wrap the setup rather than shrinking it — a long line squeezed onto one
    # row goes unreadable at feed size, which defeats the point of having it.
    sf = font("light", cfg["setup_size"])
    setup_lines, setup_h, setup_step = [], 0, 0
    if setup:
        while sf.size > 14:
            setup_lines = wrap_to_width(setup, sf, cfg["setup_tracking"], box_w)
            if len(setup_lines) <= cfg["setup_max_lines"]:
                break
            sf = font("light", sf.size - 2)
        lh = max(text_size(sf, s)[1] for s in setup_lines)
        setup_step = lh * 1.45
        setup_h = setup_step * (len(setup_lines) - 1) + lh + cfg["setup_gap"]

    avail_h = int(H * cfg["payoff_max_height"]) - setup_h
    size, lines = wrap_best("display", payoff, box_w, avail_h,
                            cfg["payoff_max_lines"], cfg["payoff_line_gap"])
    pf = font("display", size)
    lh = max(text_size(pf, s)[1] for s in lines)
    gap = lh * cfg["payoff_line_gap"]
    step = lh + gap
    block_h = setup_h + lh * len(lines) + gap * (len(lines) - 1)

    y = (H - block_h) // 2
    if setup_lines:
        sy = y
        for line in setup_lines:
            draw_tracked(d, (tx, sy), line, sf, C["bone"], cfg["setup_tracking"])
            sy += setup_step
        y += setup_h

    for line in lines:
        b = pf.getbbox(line)
        d.text((tx - b[0], y - b[1]), line, font=pf, fill=C["gold"])
        y += step

    save(img, out)
    return img


def make_avatar(image, out):
    W, H = T["avatar"]["size"]
    save(base_canvas((W, H), image), out)


def make_watermark(image, out):
    """Small circular mark for the video-player branding watermark."""
    W, H = T["watermark"]["size"]
    img = base_canvas((W * 4, H * 4), image).resize((W, H), Image.LANCZOS)
    mask = Image.new("L", (W * 4, H * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, W * 4 - 1, H * 4 - 1], fill=255)
    img.putalpha(mask.resize((W, H), Image.LANCZOS))
    save(img, out)


# ---------------- batch ----------------

def run_batch():
    path = os.path.join(ROOT, "songs.json")
    if not os.path.exists(path):
        sys.exit("songs.json not found — create it first (see README.md)")
    with open(path, encoding="utf-8") as f:
        jobs = json.load(f)

    for j in jobs:
        kind = j.get("type", "thumbnail")
        out = os.path.join(OUT, j["out"])
        img = j.get("image")
        if img and not os.path.isabs(img):
            img = os.path.join(ROOT, img)
        if img and not os.path.exists(img):
            print(f"  SKIP {j['out']} — missing image {j['image']}")
            continue
        print(f"{kind}: {j['out']}")
        if kind == "thumbnail":
            make_thumbnail(img, j.get("setup", ""), j["payoff"], out)
        elif kind == "banner":
            make_banner(img, out)
        elif kind == "avatar":
            make_avatar(img, out)
        elif kind == "watermark":
            make_watermark(img, out)
        else:
            print(f"  unknown type: {kind}")


# ---------------- cli ----------------

def main():
    p = argparse.ArgumentParser(description="Broken & Restored channel art generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("banner", "avatar", "watermark"):
        s = sub.add_parser(name)
        s.add_argument("--image", required=(name != "banner"))
        s.add_argument("--out", default=None)

    s = sub.add_parser("thumbnail")
    s.add_argument("--image")
    s.add_argument("--setup", default="")
    s.add_argument("--payoff", required=True)
    s.add_argument("--out", default=None)

    sub.add_parser("batch")

    a = p.parse_args()
    if a.cmd == "batch":
        return run_batch()

    default = {"banner": "banner.png", "avatar": "avatar.png",
               "watermark": "watermark.png", "thumbnail": "thumbnail.png"}[a.cmd]
    out = a.out or os.path.join(OUT, default)
    if not os.path.isabs(out):
        out = os.path.join(ROOT, out)

    print(f"{a.cmd}:")
    if a.cmd == "banner":
        make_banner(a.image, out)
    elif a.cmd == "avatar":
        make_avatar(a.image, out)
    elif a.cmd == "watermark":
        make_watermark(a.image, out)
    else:
        make_thumbnail(a.image, a.setup, a.payoff, out)


if __name__ == "__main__":
    main()
