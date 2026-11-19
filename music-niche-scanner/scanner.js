#!/usr/bin/env node
/**
 * YouTube Music-Niche Demand Scanner
 * Sister tool to yt-outlier-scanner (history/mystery). Same engine, retuned for
 * the AI emotional/motivational rap niche, plus a DEMAND BOARD that tells you
 * which themes, hook phrases and descriptor tails are actually being rewarded —
 * i.e. what to write next, not just which videos won.
 *
 * Usage:
 *   node scanner.js              -> full scan, prints report + saves report.md
 *   node scanner.js --quick      -> 3 queries only (fast test)
 *   node scanner.js --days 30    -> only videos uploaded in last N days (default 90)
 *   node scanner.js --lane faith -> restrict to one lane (faith|emotional|motivation)
 *
 * Data source: yt-dlp (must be on PATH). No API keys needed.
 */

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

// ---------------- CONFIG ----------------

// Queries grouped by lane so we can scan one lane at a time.
const LANES = {
  faith: [
    "emotional christian rap",
    "christian rap about betrayal",
    "christian hip hop healing song",
    "gospel rap english lyrics",
  ],
  emotional: [
    "emotional rap lyrics",
    "sad rap song lyrics",
    "storytelling rap emotional",
    "deep emotional rap about pain",
  ],
  heartbreak: [
    "heartbreak rap song lyrics",
    "sad rap about losing her",
    "emotional rap about breakup",
    "rap song about moving on lyrics",
  ],
  motivation: [
    "motivational rap lyrics video",
    "aggressive motivational rap gym",
    "discipline rap motivation",
  ],
};

const SEARCH_FILTERS = [
  { sp: "CAMSBAgEEAE%253D", label: "top this month" },
  { sp: "CAMSBAgDEAE%253D", label: "top this week" },
];

const RESULTS_PER_SEARCH = 12; // videos pulled per query+filter
const MAX_SUBS = 500_000;      // ignore giant channels (brand-driven views)
const MIN_VIEWS = 3_000;       // songs traction-gate lower than documentaries
const MIN_DURATION_S = 100;    // songs run 2-8 min; this only trims true Shorts
const MIN_RATIO = 1.0;         // views must be >= 1x subscribers
// Bought views show up as a huge view/sub ratio with almost no likes. Real
// organic videos in this niche sit at 0.7-3%; promoted uploads land near 0.02%.
const MIN_LIKE_RATE = 0.004;   // 0.4% — below this the views were almost certainly paid

// Stripped before theme extraction — these are format labels, not themes.
const FORMAT_WORDS = new Set(
  ("lyrics lyric video official music song songs rap hip hop hiphop ai " +
   "cinematic epic powerful deep new full audio mix collection vol " +
   "feat ft prod remix cover version 2024 2025 2026").split(" ")
);

// True stopwords. Pronouns are deliberately KEPT — "me", "my", "i" carry the
// confessional signal that defines this niche.
const STOPWORDS = new Set(
  ("the a an of to in on and or but for with is was are be been at as by from " +
   "that this these those it its").split(" ")
);

const CACHE_FILE = path.join(__dirname, "cache.json");
const REPORT_FILE = path.join(__dirname, "report.md");

// ---------------- HELPERS ----------------

function ytdlp(args, timeoutMs = 300000) {
  try {
    return execFileSync("yt-dlp", args, {
      encoding: "utf8",
      timeout: timeoutMs,
      maxBuffer: 32 * 1024 * 1024,
      windowsHide: true,
    });
  } catch (e) {
    // yt-dlp exits non-zero if ANY video in a batch fails; stdout still has the good ones
    return e.stdout ? e.stdout.toString() : "";
  }
}

function daysSince(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return null;
  const d = new Date(
    +yyyymmdd.slice(0, 4),
    +yyyymmdd.slice(4, 6) - 1,
    +yyyymmdd.slice(6, 8)
  );
  return Math.max(1, Math.round((Date.now() - d.getTime()) / 86400000));
}

function fmt(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function loadCache() {
  try {
    return JSON.parse(fs.readFileSync(CACHE_FILE, "utf8"));
  } catch {
    return { videos: {} };
  }
}

// ---------------- PHASE 1: discover video IDs ----------------

function discoverIds(queries, filters) {
  const ids = new Set();
  for (const q of queries) {
    for (const f of filters) {
      const url = `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}&sp=${f.sp}`;
      process.stderr.write(`  searching: "${q}" (${f.label})...\n`);
      const out = ytdlp([
        url,
        "--flat-playlist",
        "--playlist-end", String(RESULTS_PER_SEARCH),
        "--no-warnings",
        "--print", "%(id)s",
      ], 120000);
      for (const line of out.split("\n")) {
        const id = line.trim();
        if (/^[A-Za-z0-9_-]{11}$/.test(id)) ids.add(id);
      }
    }
  }
  return [...ids];
}

// ---------------- PHASE 2: fetch metadata ----------------

const SEP = "\t";
const PRINT_FMT = [
  "%(id)s",
  "%(view_count)s",
  "%(channel_follower_count)s",
  "%(like_count)s",
  "%(upload_date)s",
  "%(duration)s",
  "%(channel)s",
  "%(channel_url)s",
  "%(title)s",
].join(SEP);

function fetchMeta(ids, cache) {
  const fresh = ids.filter((id) => !cache.videos[id]);
  process.stderr.write(`  metadata: ${ids.length} videos (${fresh.length} new, ${ids.length - fresh.length} cached)\n`);

  for (let i = 0; i < fresh.length; i += 15) {
    const chunk = fresh.slice(i, i + 15);
    process.stderr.write(`  fetching ${i + 1}-${Math.min(i + 15, fresh.length)} of ${fresh.length}...\n`);
    const out = ytdlp([
      ...chunk.map((id) => `https://www.youtube.com/watch?v=${id}`),
      "--skip-download",
      "--no-warnings",
      "--ignore-errors",
      "--print", PRINT_FMT,
    ]);
    for (const line of out.split("\n")) {
      const p = line.split(SEP);
      if (p.length < 8) continue;
      const [id, views, subs, likes, upDate, dur, channel, channelUrl] = p;
      cache.videos[id] = {
        id,
        views: +views || 0,
        subs: +subs || 0,
        likes: +likes || 0,
        uploadDate: upDate,
        duration: +dur || 0,
        channel,
        channelUrl,
        title: p.slice(8).join(SEP),
        fetchedAt: new Date().toISOString().slice(0, 10),
      };
    }
  }
  return ids.map((id) => cache.videos[id]).filter(Boolean);
}

// ---------------- PHASE 3: score & rank ----------------

function rank(videos, maxAgeDays) {
  const out = [];
  for (const v of videos) {
    const age = daysSince(v.uploadDate);
    if (age === null || age > maxAgeDays) continue;
    if (v.duration < MIN_DURATION_S) continue;
    if (v.views < MIN_VIEWS) continue;
    if (v.subs <= 0 || v.subs > MAX_SUBS) continue;

    const ratio = v.views / v.subs;
    if (ratio < MIN_RATIO) continue;

    // drop paid-promotion videos; keep them only when likes are missing entirely
    const likeRate = v.likes > 0 ? v.likes / v.views : null;
    if (likeRate !== null && likeRate < MIN_LIKE_RATE) continue;

    const viewsPerDay = v.views / age;
    const score = ratio * Math.log10(viewsPerDay + 10);

    out.push({ ...v, age, ratio, viewsPerDay, score, likeRate });
  }
  return out.sort((a, b) => b.score - a.score);
}

// ---------------- PHASE 4: demand board ----------------

// The hook is everything before the first pipe; the tail is what follows.
// Winners in this niche use "[HOOK] | [genre descriptor] | [use case]".
function splitTitle(title) {
  const parts = title.split("|").map((s) => s.trim()).filter(Boolean);
  return { hook: parts[0] || title.trim(), tail: parts.slice(1).join(" | ") };
}

function words(str) {
  return str
    .toLowerCase()
    .replace(/[^a-z0-9'\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

// Score-weighted n-gram counting: a phrase that shows up in a 300x video
// outranks one that shows up in three 2x videos.
function themeBoard(ranked) {
  const uni = new Map();
  const bi = new Map();

  const bump = (map, key, v) => {
    const e = map.get(key) || { score: 0, hits: 0, best: null };
    e.score += v.score;
    e.hits += 1;
    if (!e.best || v.ratio > e.best.ratio) e.best = v;
    map.set(key, e);
  };

  for (const v of ranked) {
    const w = words(splitTitle(v.title).hook)
      .filter((x) => !FORMAT_WORDS.has(x) && x.length > 1);

    const seenU = new Set();
    const meaningful = w.filter((x) => !STOPWORDS.has(x));
    for (const x of meaningful) {
      if (seenU.has(x)) continue;
      seenU.add(x);
      bump(uni, x, v);
    }

    // bigrams keep stopwords so phrases read naturally ("broke me", "no one")
    const seenB = new Set();
    for (let i = 0; i < w.length - 1; i++) {
      const g = `${w[i]} ${w[i + 1]}`;
      if (STOPWORDS.has(w[i]) && STOPWORDS.has(w[i + 1])) continue;
      if (seenB.has(g)) continue;
      seenB.add(g);
      bump(bi, g, v);
    }
  }

  const top = (map, minHits) =>
    [...map.entries()]
      .filter(([, e]) => e.hits >= minHits)
      .sort((a, b) => b[1].score - a[1].score)
      .slice(0, 20);

  return { uni: top(uni, 2), bi: top(bi, 2) };
}

// Which descriptor tails are the winners locking in?
function tailBoard(ranked) {
  const m = new Map();
  for (const v of ranked) {
    const { tail } = splitTitle(v.title);
    if (!tail) continue;
    const e = m.get(tail) || { score: 0, hits: 0, channels: new Set() };
    e.score += v.score;
    e.hits += 1;
    e.channels.add(v.channel);
    m.set(tail, e);
  }
  return [...m.entries()].sort((a, b) => b[1].score - a[1].score).slice(0, 12);
}

// Channels compounding across multiple winners are the ones worth studying.
function channelBoard(ranked) {
  const m = new Map();
  for (const v of ranked) {
    const e = m.get(v.channel) || { score: 0, hits: 0, subs: v.subs, views: 0, url: v.channelUrl };
    e.score += v.score;
    e.hits += 1;
    e.views += v.views;
    e.subs = Math.max(e.subs, v.subs);
    m.set(v.channel, e);
  }
  return [...m.entries()].sort((a, b) => b[1].score - a[1].score).slice(0, 15);
}

// ---------------- REPORT ----------------

function report(ranked, laneLabel) {
  const lines = [];
  lines.push(`# Music Niche Demand Report — ${new Date().toISOString().slice(0, 10)}`);
  lines.push("");
  lines.push(`Lane: **${laneLabel}** | Channels under ${fmt(MAX_SUBS)} subs outperforming their size.`);
  lines.push(`Score = (views ÷ subs) weighted by daily view velocity. Higher = stronger signal.`);
  lines.push("");

  if (!ranked.length) {
    lines.push("No outliers found with current filters. Try --days 180 or lower MIN_VIEWS.");
    return lines.join("\n");
  }

  // ---- demand board first: this is what you act on ----
  const { uni, bi } = themeBoard(ranked);
  const tails = tailBoard(ranked);

  lines.push("## 🔥 Demand board — what to write next");
  lines.push("");
  lines.push("### Hook phrases in demand (score-weighted, 2+ appearances)");
  lines.push("");
  lines.push("| Phrase | Weight | Videos | Best performer |");
  lines.push("|---|---:|---:|---|");
  for (const [g, e] of bi) {
    lines.push(`| ${g} | ${e.score.toFixed(0)} | ${e.hits} | ${e.best.ratio.toFixed(0)}x — ${e.best.title.slice(0, 46)} |`);
  }
  lines.push("");

  lines.push("### Theme words in demand");
  lines.push("");
  lines.push(uni.map(([w, e]) => `**${w}** (${e.score.toFixed(0)})`).join(" · "));
  lines.push("");

  lines.push("### Descriptor tails the winners are locking in");
  lines.push("");
  lines.push("| Tail | Weight | Uses | Channels |");
  lines.push("|---|---:|---:|---:|");
  for (const [t, e] of tails) {
    lines.push(`| ${t.slice(0, 62)} | ${e.score.toFixed(0)} | ${e.hits} | ${e.channels.size} |`);
  }
  lines.push("");

  lines.push("### Channels compounding (study these)");
  lines.push("");
  lines.push("| Channel | Subs | Winners | Total views | URL |");
  lines.push("|---|---:|---:|---:|---|");
  for (const [c, e] of channelBoard(ranked)) {
    lines.push(`| ${c} | ${fmt(e.subs)} | ${e.hits} | ${fmt(e.views)} | ${e.url} |`);
  }
  lines.push("");

  // ---- raw outliers ----
  lines.push("## Top outliers");
  lines.push("");
  ranked.slice(0, 25).forEach((v, i) => {
    const { hook, tail } = splitTitle(v.title);
    lines.push(`### ${i + 1}. ${hook}`);
    if (tail) lines.push(`- **Tail:** ${tail}`);
    lines.push(`- **Channel:** ${v.channel} (${fmt(v.subs)} subs) — ${v.channelUrl}`);
    lines.push(`- **Views:** ${fmt(v.views)} in ${v.age} days (${fmt(Math.round(v.viewsPerDay))}/day) — **${v.ratio.toFixed(1)}x subscriber count**`);
    lines.push(`- **Length:** ${Math.floor(v.duration / 60)}:${String(v.duration % 60).padStart(2, "0")} | Score: ${v.score.toFixed(1)} | Likes: ${v.likeRate === null ? "n/a" : (v.likeRate * 100).toFixed(2) + "%"}`);
    lines.push(`- **Watch:** https://youtu.be/${v.id}`);
    lines.push("");
  });

  return lines.join("\n");
}

// ---------------- MAIN ----------------

function main() {
  const args = process.argv.slice(2);
  const quick = args.includes("--quick");
  const daysIdx = args.indexOf("--days");
  const maxAge = daysIdx >= 0 ? +args[daysIdx + 1] || 90 : 90;
  const laneIdx = args.indexOf("--lane");
  const lane = laneIdx >= 0 ? args[laneIdx + 1] : null;

  let queries;
  let laneLabel;
  if (lane && LANES[lane]) {
    queries = LANES[lane];
    laneLabel = lane;
  } else {
    queries = Object.values(LANES).flat();
    laneLabel = "all";
  }
  if (quick) queries = queries.slice(0, 3);

  const filters = quick ? SEARCH_FILTERS.slice(0, 1) : SEARCH_FILTERS;

  console.error(`\n== YT Music Demand Scanner == (lane: ${laneLabel}, ${queries.length} queries, last ${maxAge} days)\n`);
  console.error("Phase 1: discovering videos...");
  const ids = discoverIds(queries, filters);
  console.error(`  found ${ids.length} unique videos\n`);

  console.error("Phase 2: fetching metadata...");
  const cache = loadCache();
  const videos = fetchMeta(ids, cache);
  fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2));

  console.error("\nPhase 3: ranking outliers...\n");
  const ranked = rank(videos, maxAge);

  const md = report(ranked, laneLabel);
  fs.writeFileSync(REPORT_FILE, md);
  console.log(md);
  console.error(`\nSaved: ${REPORT_FILE} (${ranked.length} outliers from ${videos.length} videos)`);
}

main();
