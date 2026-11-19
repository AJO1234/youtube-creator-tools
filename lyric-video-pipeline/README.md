# Broken & Restored — channel art generator

Renders every image the channel needs at exact YouTube dimensions, with
consistent typography. Nothing here needs Canva.

## Requirements

Python 3 with Pillow (`python -m pip install Pillow`). Fonts are Windows
built-ins — Impact for display, Bahnschrift for the light letterspaced lines.

## The three files you touch

| File | What it's for |
|---|---|
| `brand.json` | Design tokens — colours, sizes, margins. Change the look here. |
| `songs.json` | Batch job list — one entry per asset you want generated. |
| `assets/` | Source images from Grok (crack backgrounds, figure shots). |

`brand.py` itself should not need editing.

## Everyday use

Add entries to `songs.json`, then:

```bash
python brand.py batch
```

Output lands in `out/`. Re-running overwrites, so it's safe to run repeatedly.

A thumbnail entry looks like this:

```json
{ "type": "thumbnail", "image": "assets/fig01.jpg", "out": "thumb_09.png",
  "setup": "I BUILT MY WALLS", "payoff": "OUT OF EVERY GOODBYE" }
```

`setup` is the small bone-white line, `payoff` is the big gold phrase. Line
breaks are chosen automatically — the tool tries every split and keeps whichever
renders largest inside the text column, so you never set a font size.

## One-off commands

```bash
python brand.py banner    --image assets/crack.jpg
python brand.py thumbnail --image assets/fig01.jpg --setup "GOD DIDN'T" --payoff "STOP THE STORM"
python brand.py avatar    --image assets/slab.jpg
python brand.py watermark --image assets/slab.jpg
```

Add `--out path/to/file.png` to control the destination.

## Output sizes

| Asset | Size | Notes |
|---|---|---|
| Banner | 2560 × 1440 | text stays inside the 1546 × 423 desktop safe zone |
| Thumbnail | 1280 × 720 | verified legible down to 168 px |
| Avatar | 800 × 800 | |
| Watermark | 150 × 150 | circular, transparent — for the player branding overlay |

## How contrast is handled

Gold text on warm gold-flecked backgrounds goes muddy, so both the banner and
thumbnail composite a feathered dark scrim beneath the type — a soft band on the
banner, a one-sided fade on the thumbnail. It reads as natural shadow rather than
a box. Tune with `scrim_strength` in `brand.json`; raise it if a busy source
image is fighting the text.

## Generating source images

Source images are generated with Grok. The rule that matters: leave one half of
the thumbnail frame empty black for text, and keep the banner crack centred.
`figure_side` in `brand.json` controls which side the text column sits on.

## Palette

| | Hex | Use |
|---|---|---|
| ink | `#0A0A0B` | backgrounds |
| bone | `#E8E6E1` | "BROKEN", thumbnail setup line |
| gold | `#C9962E` | "RESTORED", thumbnail payoff |
| muted | `#B0ABA4` | tagline |
| blood | `#8A1F1F` | accent, unused so far |

---

# Lyric video generator — `lyricvid.py`

Turns a song plus a lyrics text file into a finished 1080p lyric video. No
CapCut, no manual keyframing.

## Workflow

```bash
python lyricvid.py align --audio audio/song01.wav --lyrics songs/video-01/lyrics.txt
python lyricvid.py preview      # fast 480p — check sync
python lyricvid.py render       # final 1080p
```

`align` is the one to use. It runs Whisper over the song for word-level
timestamps, then sequence-matches that against your real lyrics — so it doesn't
matter that Whisper mishears sung vocals, because the words are already known
and only the timing is being taken from it. On song 1 it anchored 55/55 lines
with no interpolation.

`init` is the fallback if you ever want timings without running Whisper: it
spreads lines by character count, which needs correcting by hand afterwards.

`init` writes `timing.json` with a first guess at every line's start time,
weighted by line length so a long bar gets more room than "So no." That guess
will drift — open `timing.json`, play the preview, and nudge the `t` values.
Only the lines that feel late or early need touching.

## Lyrics file format

Plain text, one displayed line per row. Section markers in `[brackets]` are
never shown on screen, but they control colour: any section whose name contains
**hook** or **chorus** renders in gold, everything else in bone. So the hook
lands differently from the verses with no extra work.

## Options

| Flag | Effect |
|---|---|
| `--darken 0.55` | background brightness; lower is darker if lyrics are competing |
| `--still` | disables the slow Ken Burns zoom |
| `--background path.jpg` | override the still used |
| `--out name.mp4` | output filename |

## Notes

- Audio is encoded at 320k AAC. Feed it the WAV from Suno, not the MP3 — no
  reason to stack two lossy passes.
- The background is pre-darkened in Pillow rather than relying on heavy text
  outlines, which keeps the type clean.
- Render time on a 4-minute song is a few minutes at 1080p; the 480p preview is
  much faster and is the one to iterate on.

## Fixing sync without editing JSON by hand

Two commands cover almost every timing problem.

**Everything uniformly off** (usual cause: the tool doesn't know how long your
instrumental intro runs):

```bash
python lyricvid.py shift --by 4.5        # everything 4.5s later
python lyricvid.py shift --by -2         # everything 2s earlier
python lyricvid.py shift --by 1 --after 90   # only the back half
```

**Drift that grows over the song** — starts fine, ends way off. Note when the
first and last lyric actually land, then anchor both:

```bash
python lyricvid.py fit --first 9.2 --last 214
```

That rescales every line in between proportionally. Usually a single `fit` gets
the whole song close, and only a few lines need hand-nudging afterwards.

## Multiple background scenes

A single still held for four minutes is the main visual gap versus the channels
winning this niche — Vikings of Christ changes scene roughly every seven seconds.
Point `--background` at a folder instead of a file and the tool spreads every
image across the song with crossfades:

```bash
python lyricvid.py preview --background assets/scenes
python lyricvid.py render  --background assets/scenes --xfade 2
```

Images are used in filename order, so number them `01.jpg`, `02.jpg` and so on.
Each still gets its own slow Ken Burns move. `--xfade` sets the dissolve length
in seconds (default 1.5). Eight to twelve scenes across a four-minute song lands
near the 20–30 second pacing the niche uses.

## Setup line: wrap, don't shrink

Long setup lines used to be shrunk until they fitted one row, which made them
unreadable at feed size. They now wrap to `setup_max_lines` (default 2) at
`setup_size` (default 52) and only shrink if they still overflow. If a setup line
looks small, shorten the text rather than lowering the size.

## Vertical clips for TikTok / Shorts / Reels

No new artwork needed — clips reuse the same scene stills, cropped to 9:16, with
the lyric timings `align` already solved. One export serves all three platforms.

```bash
python lyricvid.py clip --from 60 --to 86 --background assets/scenes03 --out clip03_hook.mp4
```

The tool works out which still was on screen at that moment in the long video and
uses it, so the short matches the full version. Override with `--scene path.jpg`.

| Flag | Effect |
|---|---|
| `--anchor 0.5` | horizontal crop point — 0 is the left edge, 1 the right. Push it toward the subject if a figure gets cut. |
| `--size 92` | caption size; phones need it far larger than the 16:9 render |
| `--scene` | force a specific still instead of the auto-picked one |

Pick the window around a hook — 20–35 seconds is the right length. Note that
`--background` must be passed explicitly if `align` was run with a different one.
