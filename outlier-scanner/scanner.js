#!/usr/bin/env node
/**
 * YouTube Outlier Scanner
 * Finds videos from small/new channels that are massively outperforming
 * their subscriber count — the "blowing up" signal that tells you what
 * formats and topics are working in the niche RIGHT NOW.
 *
 * Usage:
 *   node scanner.js              -> full scan, prints report + saves report.md
 *   node scanner.js --quick      -> 2 queries only (fast test)
 *   node scanner.js --days 30    -> only videos uploaded in last N days (default 90)
 *
 * Data source: yt-dlp (must be on PATH). No API keys needed.
 */

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

// ---------------- CONFIG ----------------

// Niche search queries — edit freely. Each costs ~30-60s of scan time.
const QUERIES = [
  "history mystery documentary",
  "lost civilization documentary",
  "unsolved mystery history",
  "dark history documentary",
  "ancient secrets documentary",
  "what happened to documentary",
];

// YouTube search filter params (sp=):
//   CAMSBAgEEAE%3D = sort by view count + uploaded this month + video
//   CAMSBAgDEAE%3D = sort by view count + uploaded this week + video
//   CAI%3D         = sort by upload date
const SEARCH_FILTERS = [
  { sp: "CAMSBAgEEAE%253D", label: "top this month" },
  { sp: "CAMSBAgDEAE%253D", label: "top this week" },
];

const RESULTS_PER_SEARCH = 12; // videos pulled per query+filter
const MAX_SUBS = 1_000_000;    // ignore giant channels (brand-driven views)
const MIN_VIEWS = 5_000;       // ignore videos with no traction yet
const MIN_DURATION_S = 120;    // skip Shorts (we study long-form)
const MIN_RATIO = 1.0;         // views must be >= 1x subscribers

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
  "%(upload_date)s",
  "%(duration)s",
  "%(channel)s",
  "%(channel_url)s",
  "%(title)s",
].join(SEP);

function fetchMeta(ids, cache) {
  const fresh = ids.filter((id) => !cache.videos[id]);
  process.stderr.write(`  metadata: ${ids.length} videos (${fresh.length} new, ${ids.length - fresh.length} cached)\n`);

  // batch in chunks of 15 so one bad video doesn't waste a huge batch
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
      const [id, views, subs, upDate, dur, channel, channelUrl] = p;
      cache.videos[id] = {
        id,
        views: +views || 0,
        subs: +subs || 0,
        uploadDate: upDate,
        duration: +dur || 0,
        channel,
        channelUrl,
        title: p.slice(7).join(SEP),
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

    const viewsPerDay = v.views / age;
    // outlier score: how hard it's outperforming, weighted by recency & velocity
    const score = ratio * Math.log10(viewsPerDay + 10);

    out.push({ ...v, age, ratio, viewsPerDay, score });
  }
  return out.sort((a, b) => b.score - a.score);
}

// ---------------- REPORT ----------------

function report(ranked) {
  const lines = [];
  lines.push(`# YouTube Outlier Report — ${new Date().toISOString().slice(0, 10)}`);
  lines.push("");
  lines.push(`Videos from channels under ${fmt(MAX_SUBS)} subs that are outperforming their size.`);
  lines.push(`Score = (views ÷ subs) weighted by daily view velocity. Higher = stronger signal.`);
  lines.push("");

  if (!ranked.length) {
    lines.push("No outliers found with current filters. Try --days 180 or lower MIN_VIEWS.");
  }

  ranked.slice(0, 25).forEach((v, i) => {
    lines.push(`## ${i + 1}. ${v.title}`);
    lines.push(`- **Channel:** ${v.channel} (${fmt(v.subs)} subs) — ${v.channelUrl}`);
    lines.push(`- **Views:** ${fmt(v.views)} in ${v.age} days (${fmt(Math.round(v.viewsPerDay))}/day) — **${v.ratio.toFixed(1)}x subscriber count**`);
    lines.push(`- **Length:** ${Math.round(v.duration / 60)} min | Score: ${v.score.toFixed(1)}`);
    lines.push(`- **Watch:** https://youtu.be/${v.id}`);
    lines.push(`- **Get transcript:** \`yt-dlp --skip-download --write-auto-sub --sub-lang en https://youtu.be/${v.id}\``);
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

  const queries = quick ? QUERIES.slice(0, 2) : QUERIES;
  const filters = quick ? SEARCH_FILTERS.slice(0, 1) : SEARCH_FILTERS;

  console.error(`\n== YT Outlier Scanner == (${queries.length} queries, last ${maxAge} days)\n`);
  console.error("Phase 1: discovering videos...");
  const ids = discoverIds(queries, filters);
  console.error(`  found ${ids.length} unique videos\n`);

  console.error("Phase 2: fetching metadata...");
  const cache = loadCache();
  const videos = fetchMeta(ids, cache);
  fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2));

  console.error("\nPhase 3: ranking outliers...\n");
  const ranked = rank(videos, maxAge);

  const md = report(ranked);
  fs.writeFileSync(REPORT_FILE, md);
  console.log(md);
  console.error(`\nSaved: ${REPORT_FILE} (${ranked.length} outliers from ${videos.length} videos)`);
}

main();
