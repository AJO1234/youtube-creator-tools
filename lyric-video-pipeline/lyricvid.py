#!/usr/bin/env python
"""
Broken & Restored — lyric video generator.

Turns a song + a lyrics file into a finished 1080p lyric video with brand
typography. Timings live in a plain JSON file you can nudge by hand.

  1. python lyricvid.py init  --audio song.wav --lyrics lyrics.txt
       -> writes timing.json with a first guess at every line's start time
          (weighted by line length, so long lines get more room)

  2. open timing.json, adjust any line that drifts

  3. python lyricvid.py preview     -> fast 480p render, check sync
     python lyricvid.py render      -> final 1080p

Lines wrapped in [brackets] are treated as section markers: they are never
shown on screen, but any section whose name contains "hook" or "chorus" is
rendered in gold instead of bone.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from PIL import Image, ImageEnhance

ROOT = os.path.dirname(os.path.abspath(__file__))
TIMING = os.path.join(ROOT, "timing.json")
ASS = os.path.join(ROOT, "lyrics.ass")

# ASS colours are &HAABBGGRR — byte order is reversed from hex.
BONE = "&H00E1E6E8"
GOLD = "&H002E96C9"

# Italic serif reads as authored rather than default, and it's the one look
# nobody else in this niche uses — the meta is bold marker caps with fat
# outlines. Gold needs a dark outline or it blends into warm-toned scenes.
FONT = "Cambria"
FONT_SIZE = 72
ITALIC = 1
OUTLINE_COL = "&H00201408"
BONE_OUTLINE = 1
GOLD_OUTLINE = 2
LEAD_IN = 0.6      # gap before the first line
TAIL = 3.0         # how long the final line holds
MIN_DUR = 1.1      # never flash a line faster than this


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def parse_lyrics(path):
    """Plain text -> [(text, is_hook)], dropping [section] markers."""
    lines = []
    hook = False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("[") and s.endswith("]"):
                name = s[1:-1].lower()
                hook = "hook" in name or "chorus" in name
                continue
            lines.append((s, hook))
    return lines


def cmd_init(args):
    lines = parse_lyrics(args.lyrics)
    if not lines:
        sys.exit("no lyric lines found")
    total = duration(args.audio)
    span = total - LEAD_IN - TAIL

    # weight by character count so a long bar isn't given the same beat as "So no."
    weights = [max(len(t), 12) for t, _ in lines]
    scale = span / sum(weights)

    out, t = [], LEAD_IN
    for (text, hook), w in zip(lines, weights):
        d = max(MIN_DUR, w * scale)
        out.append({"t": round(t, 2), "text": text, "gold": hook})
        t += d

    data = {"audio": os.path.relpath(args.audio, ROOT).replace("\\", "/"),
            "background": args.background,
            "duration": round(total, 2),
            "lines": out}
    with open(TIMING, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"wrote timing.json — {len(out)} lines across {total:.1f}s")
    print("open it and nudge any line that drifts, then: python lyricvid.py preview")


def _norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def cmd_align(args):
    """
    Transcribe with word-level timestamps, then align that against the real
    lyrics. Whisper mishears sung vocals constantly, but we already know the
    words — so we only need it for timing, and a sequence match against ground
    truth makes wrong words harmless.
    """
    import difflib
    from faster_whisper import WhisperModel

    print(f"transcribing with {args.model} (first run downloads the model)...")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(args.audio, word_timestamps=True,
                                   beam_size=5, vad_filter=False)

    heard = []
    for seg in segments:
        for w in (seg.words or []):
            n = _norm(w.word)
            if n:
                heard.append((n, float(w.start)))
    if not heard:
        sys.exit("transcription produced no words")
    print(f"  heard {len(heard)} words, last at {heard[-1][1]:.1f}s")

    lines = parse_lyrics(args.lyrics)
    flat = []
    for idx, (text, _) in enumerate(lines):
        for w in text.split():
            n = _norm(w)
            if n:
                flat.append((n, idx))

    sm = difflib.SequenceMatcher(None, [w for w, _ in flat],
                                 [w for w, _ in heard], autojunk=False)
    anchor = {}
    matched = 0
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            li = flat[a + k][1]
            anchor.setdefault(li, heard[b + k][1])
            matched += 1
    print(f"  matched {matched} words -> anchored {len(anchor)}/{len(lines)} lines")

    # interpolate any line Whisper never anchored, then force monotonic order
    times, known = [], sorted(anchor)
    for i in range(len(lines)):
        if i in anchor:
            times.append(anchor[i])
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is None:
            times.append(max(0.0, anchor[nxt] - (nxt - i) * 2.0))
        elif nxt is None:
            times.append(anchor[prev] + (i - prev) * 2.0)
        else:
            span = (anchor[nxt] - anchor[prev]) / (nxt - prev)
            times.append(anchor[prev] + (i - prev) * span)

    for i in range(1, len(times)):
        times[i] = max(times[i], times[i - 1] + 0.35)

    data = {"audio": os.path.relpath(args.audio, ROOT).replace("\\", "/"),
            "background": args.background,
            "duration": round(duration(args.audio), 2),
            "lines": [{"t": round(t, 2), "text": txt, "gold": hook}
                      for t, (txt, hook) in zip(times, lines)]}
    save_timing(data)
    print(f"wrote timing.json — first {times[0]:.2f}s, last {times[-1]:.2f}s")


def cmd_clip(args):
    """
    Cut a 9:16 short from an existing song — no new artwork needed. Reuses the
    same scene stills, cropped to vertical, with the lyric timings already
    solved by `align`. One export serves TikTok, Shorts and Reels.
    """
    data = load_timing()
    scenes = collect_scenes(args.background or data.get("background"))
    dur = args.to - args.a_from
    if dur <= 0:
        sys.exit("--to must be greater than --from")

    # Work out which still was on screen at the clip's midpoint, so the short
    # matches what the long video showed at that moment.
    if args.scene:
        pick = args.scene
    else:
        seg = (data["duration"] + args.xfade * (len(scenes) - 1)) / len(scenes)
        idx = min(len(scenes) - 1, int((args.a_from + dur / 2) / max(seg - args.xfade, 0.01)))
        pick = scenes[idx]
    print(f"  scene: {pick}")

    lines = [l for l in data["lines"] if args.a_from - 0.5 <= l["t"] < args.to]
    if not lines:
        sys.exit(f"no lyric lines between {args.a_from}s and {args.to}s")

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bone,{FONT},{args.size},{BONE},{BONE},{OUTLINE_COL},&HA0000000,0,{ITALIC},0,0,100,100,1,0,1,2,4,5,70,70,60,1
Style: Gold,{FONT},{args.size},{GOLD},{GOLD},{OUTLINE_COL},&HA0000000,0,{ITALIC},0,0,100,100,1,0,1,3,3,5,70,70,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    for i, ln in enumerate(lines):
        start = max(0.0, ln["t"] - args.a_from)
        nxt = lines[i + 1]["t"] - args.a_from if i + 1 < len(lines) else dur
        end = max(min(nxt - 0.08, dur), start + 0.4)
        style = "Gold" if ln.get("gold") else "Bone"
        body.append(f"Dialogue: 0,{ts(start)},{ts(end)},{style},,0,0,0,,"
                    f"{{\\fad(250,250)}}{ln['text']}")
    with open(ASS, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(body) + "\n")

    prepped = os.path.join(ROOT, "_bg_clip.jpg")
    prep_background(os.path.join(ROOT, pick), prepped, args.darken)

    # 9:16 crop anchored horizontally: 0 = left edge, 0.5 = centre, 1 = right
    im = Image.open(prepped)
    cw = int(im.height * 9 / 16)
    cx = int((im.width - cw) * min(max(args.anchor, 0.0), 1.0))
    vf = (f"crop={cw}:{im.height}:{cx}:0,scale=1080:1920,"
          f"zoompan=z='min(zoom+0.00012,1.12)':d={int(dur*30)+30}:s=1080x1920:fps=30,"
          f"ass=lyrics.ass")

    out = args.out or f"clip_{int(args.a_from)}_{int(args.to)}.mp4"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", "_bg_clip.jpg",
           "-ss", str(args.a_from), "-t", str(dur), "-i", data["audio"],
           "-filter_complex", vf, "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
           "-t", str(dur), out]
    print(f"rendering 1080x1920 ({dur:.0f}s, {len(lines)} lines) -> {out}")
    run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  done — {out}")


def load_timing():
    if not os.path.exists(TIMING):
        sys.exit("timing.json not found — run `init` first")
    with open(TIMING, encoding="utf-8") as f:
        return json.load(f)


def save_timing(data):
    with open(TIMING, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cmd_shift(args):
    """Move lines later or earlier in bulk — the fix when everything is uniformly off."""
    data = load_timing()
    n = 0
    for ln in data["lines"]:
        if args.after is not None and ln["t"] < args.after:
            continue
        ln["t"] = round(max(0.0, ln["t"] + args.by), 2)
        n += 1
    save_timing(data)
    where = f" (from {args.after}s on)" if args.after is not None else ""
    print(f"shifted {n} lines by {args.by:+.2f}s{where}")


def cmd_fit(args):
    """
    Anchor the first and last lyric to times you read off the preview, then
    rescale everything between. Fixes drift that accumulates over the song.
    """
    data = load_timing()
    lines = data["lines"]
    old_first, old_last = lines[0]["t"], lines[-1]["t"]
    if old_last <= old_first:
        sys.exit("timings look degenerate — re-run `init`")

    factor = (args.last - args.first) / (old_last - old_first)
    for ln in lines:
        ln["t"] = round(args.first + (ln["t"] - old_first) * factor, 2)
    save_timing(data)
    print(f"refitted {len(lines)} lines: first {args.first}s, last {args.last}s "
          f"(stretch x{factor:.3f})")


def ts(sec):
    sec = max(0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(data):
    lines = data["lines"]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bone,{FONT},{FONT_SIZE},{BONE},{BONE},{OUTLINE_COL},&HA0000000,0,{ITALIC},0,0,100,100,1,0,1,{BONE_OUTLINE},4,5,220,220,60,1
Style: Gold,{FONT},{FONT_SIZE},{GOLD},{GOLD},{OUTLINE_COL},&HA0000000,0,{ITALIC},0,0,100,100,1,0,1,{GOLD_OUTLINE},3,5,220,220,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    for i, ln in enumerate(lines):
        start = ln["t"]
        end = lines[i + 1]["t"] if i + 1 < len(lines) else data["duration"]
        end = max(end - 0.08, start + 0.4)   # small gap so lines don't butt together
        style = "Gold" if ln.get("gold") else "Bone"
        text = ln["text"].replace("\n", " ")
        body.append(f"Dialogue: 0,{ts(start)},{ts(end)},{style},,0,0,0,,"
                    f"{{\\fad(320,320)}}{text}")
    return head + "\n".join(body) + "\n"


def prep_background(src, dst, darken):
    """Pre-darken the still so lyrics stay readable without heavy outlines."""
    im = Image.open(src).convert("RGB")
    if im.width < 1920:
        s = 1920 / im.width
        im = im.resize((1920, round(im.height * s)), Image.LANCZOS)
    im = ImageEnhance.Brightness(im).enhance(darken)
    im.save(dst, quality=95)


def collect_scenes(spec):
    """A folder of images, or a single image. Sorted so numbering controls order."""
    p = os.path.join(ROOT, spec)
    if os.path.isdir(p):
        found = sorted(f for f in os.listdir(p)
                       if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
        return [os.path.join(spec, f).replace("\\", "/") for f in found]
    return [spec]


def scene_filter(n, total, w, h, fps, xfade, still):
    """
    Ken Burns each scene, then chain them with crossfades.

    Scenes overlap during the fade, so each must run longer than its share:
    seg = (total + xfade*(n-1)) / n, and fade k starts at k*(seg - xfade).
    """
    seg = (total + xfade * (n - 1)) / n
    frames = int(seg * fps) + fps
    parts = []
    for i in range(n):
        if still:
            v = (f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                 f"crop={w}:{h},setsar=1[v{i}]")
        else:
            # alternate zoom direction so consecutive scenes don't feel identical
            z = 0.10 / frames
            v = (f"[{i}:v]scale={w*2}:-1,"
                 f"zoompan=z='min(zoom+{z:.9f},1.10)':d={frames}:s={w}x{h}:fps={fps},"
                 f"setsar=1[v{i}]")
        parts.append(v)

    prev = "v0"
    for i in range(1, n):
        out = f"x{i}"
        off = (seg - xfade) * i
        parts.append(f"[{prev}][v{i}]xfade=transition=fade:"
                     f"duration={xfade}:offset={off:.3f}[{out}]")
        prev = out

    parts.append(f"[{prev}]ass=lyrics.ass[vout]")
    return ";".join(parts), seg


def cmd_render(args):
    if not os.path.exists(TIMING):
        sys.exit("timing.json not found — run `init` first")
    with open(TIMING, encoding="utf-8") as f:
        data = json.load(f)

    with open(ASS, "w", encoding="utf-8") as f:
        f.write(build_ass(data))

    scenes = collect_scenes(args.background or data.get("background"))
    missing = [s for s in scenes if not os.path.exists(os.path.join(ROOT, s))]
    if not scenes or missing:
        sys.exit(f"background not found: {missing or args.background}")

    w, h = (854, 480) if args.preview else (1920, 1080)
    fps = 24 if args.preview else 30
    crf = "30" if args.preview else "18"
    preset = "veryfast" if args.preview else "medium"
    total = data["duration"]

    prepped = []
    for i, s in enumerate(scenes):
        dst = os.path.join(ROOT, f"_bg_{i:02d}.jpg")
        prep_background(os.path.join(ROOT, s), dst, args.darken)
        prepped.append(os.path.basename(dst))

    vf, seg = scene_filter(len(prepped), total, w, h, fps, args.xfade, args.still)

    cmd = ["ffmpeg", "-y"]
    for f in prepped:
        cmd += ["-loop", "1", "-t", f"{seg + args.xfade:.3f}", "-i", f]
    cmd += ["-i", data["audio"], "-filter_complex", vf,
            "-map", "[vout]", "-map", f"{len(prepped)}:a",
            "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "320k", "-shortest",
            args.out or ("preview.mp4" if args.preview else "lyric_video.mp4")]

    out = cmd[-1]
    print(f"rendering {w}x{h} -> {out}  ({len(prepped)} scene(s), {seg:.1f}s each)")
    run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    size = os.path.getsize(os.path.join(ROOT, out)) / 1e6
    print(f"  done — {out} ({size:.1f} MB)")


def main():
    p = argparse.ArgumentParser(description="Broken & Restored lyric video generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init")
    i.add_argument("--audio", required=True)
    i.add_argument("--lyrics", required=True)
    i.add_argument("--background", default="assets/crack.jpg")

    for name in ("render", "preview"):
        r = sub.add_parser(name)
        r.add_argument("--background", default=None)
        r.add_argument("--darken", type=float, default=0.55,
                       help="background brightness, lower = darker (default 0.55)")
        r.add_argument("--still", action="store_true", help="disable the slow zoom")
        r.add_argument("--xfade", type=float, default=1.5,
                       help="crossfade seconds between scenes (default 1.5)")
        r.add_argument("--out", default=None)

    s = sub.add_parser("shift")
    s.add_argument("--by", type=float, required=True,
                   help="seconds to add (negative = earlier)")
    s.add_argument("--after", type=float, default=None,
                   help="only affect lines at or after this time")

    fi = sub.add_parser("fit")
    fi.add_argument("--first", type=float, required=True,
                    help="when the FIRST lyric actually starts")
    fi.add_argument("--last", type=float, required=True,
                    help="when the LAST lyric actually starts")

    al = sub.add_parser("align")
    al.add_argument("--audio", required=True)
    al.add_argument("--lyrics", required=True)
    al.add_argument("--background", default="assets/crack.jpg")
    al.add_argument("--model", default="small.en",
                    help="whisper model: tiny.en, base.en, small.en, medium.en")

    c = sub.add_parser("clip")
    c.add_argument("--from", dest="a_from", type=float, required=True)
    c.add_argument("--to", type=float, required=True)
    c.add_argument("--background", default=None)
    c.add_argument("--scene", default=None, help="force a specific still")
    c.add_argument("--anchor", type=float, default=0.5,
                   help="0=crop to left edge, 0.5=centre, 1=right")
    c.add_argument("--size", type=int, default=92, help="caption size for phones")
    c.add_argument("--darken", type=float, default=0.55)
    c.add_argument("--xfade", type=float, default=2.0)
    c.add_argument("--out", default=None)

    a = p.parse_args()
    if a.cmd == "init":
        return cmd_init(a)
    if a.cmd == "align":
        return cmd_align(a)
    if a.cmd == "clip":
        return cmd_clip(a)
    if a.cmd == "shift":
        return cmd_shift(a)
    if a.cmd == "fit":
        return cmd_fit(a)
    a.preview = (a.cmd == "preview")
    cmd_render(a)


if __name__ == "__main__":
    main()
