# YouTube Creator Tools

Three small tools I built to run YouTube channels with as little manual work as
possible.

| Tool | Language | What it does |
|---|---|---|
| [`outlier-scanner/`](outlier-scanner/) | Node.js | Finds videos from small channels that are far outperforming their subscriber count, which shows what is working in a niche right now |
| [`music-niche-scanner/`](music-niche-scanner/) | Node.js | The same engine retuned for music, plus a **demand board** that shows which themes and hook phrases are being rewarded, so you know what to write next |
| [`lyric-video-pipeline/`](lyric-video-pipeline/) | Python | Turns a song and a lyrics file into a finished 1080p lyric video, plus vertical Shorts/TikTok clips and all channel art (banner, thumbnails, avatar) |

## Highlights

- **No API keys.** Both scanners use `yt-dlp` for data.
- **Lyric sync with Whisper, without trusting Whisper's words.** Whisper mishears
  sung vocals, so the pipeline only takes its *timing*. It sequence-matches the
  transcript against the known lyrics, and anchored 55 of 55 lines on the first song.
- **Thumbnail text that sizes itself.** The renderer tries every line break and
  keeps whichever renders largest inside the text column, so no font sizes are set
  by hand.
- **One edit, three platforms.** Vertical clips reuse the same scenes and timings
  as the full video.

## Requirements

- Node.js 18+ and `yt-dlp` on PATH (scanners)
- Python 3, Pillow, FFmpeg and `faster-whisper` (lyric pipeline)

```bash
node outlier-scanner/scanner.js --quick
python lyric-video-pipeline/lyricvid.py align --audio song.wav --lyrics lyrics.txt
```

© 2026 AJO1234 — all rights reserved. Shared for portfolio purposes.
