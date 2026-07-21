/* ============================================================
   app.js — the BEHAVIOUR. Reads grid_v2.json (built by the
   Python model — the only place verdicts are computed) and
   renders it. The one thing computed HERE is the live flag,
   from the EA's open API, using the same formula as the
   pipeline — and it's display-only context, never a verdict.

   Layout of this file:
     1. constants + tiny helpers
     2. data loading (grid with offline fallbacks, EA live flag)
     3. render functions, one per component, top of screen down
     4. wiring (tabs, day nav, clocks)
   ============================================================ */

"use strict";

// ------------------------------------------------ 1. constants + helpers
const GRID_URL = "../data/grid_v2.json";
const EA_URL = "https://environment.data.gov.uk/flood-monitoring" +
               "/id/stations/0009/readings?parameter=level&_sorted&since=";
const PLA_FLAG_PAGE = "https://pla.co.uk/ebb-tide-flag-warning";

const SLOT_FIRST = 4 * 60 + 30;   // 04:30 — first slot of the model day
const SLOT_LAST = 20 * 60 + 30;   // 20:30 — last
const N_SLOTS = 33;               // (20:30-04:30)/30min + 1

const STALE_AFTER_H = 13;         // two missed 6-hourly runs => warn

// verdict -> {cls: css suffix, word: table label, rank: severity}
const VERDICTS = {
  "ROW":                     { cls: "g", word: "ROW",       rank: 0 },
  "Caution":                 { cls: "a", word: "CARE",      rank: 1 },
  "Don't row":               { cls: "r", word: "DON'T",     rank: 2 },
  "No launch (embankment)":  { cls: "t", word: "NO LAUNCH", rank: 2 },
};
const LIGHT_CLS = { GREEN: "g", AMBER: "a", RED: "r" };

const $ = (id) => document.getElementById(id);
const pad = (n) => String(n).padStart(2, "0");

// "2026-07-20" + "06:30" -> a Date in the device's local time.
// The grid is London wall-clock and this app lives on a London phone,
// so device-local IS London — stated assumption, not an accident.
function slotDate(dayKey, hhmm) {
  const [y, m, d] = dayKey.split("-").map(Number);
  const [hh, mm] = hhmm.split(":").map(Number);
  return new Date(y, m - 1, d, hh, mm);
}
const DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const dayShort = (k) => {
  const d = slotDate(k, "12:00");
  return `${DAYS[d.getDay()]} ${d.getDate()}`;
};
const todayKey = () => {
  const n = new Date();
  return `${n.getFullYear()}-${pad(n.getMonth() + 1)}-${pad(n.getDate())}`;
};
const minsNow = () => { const n = new Date(); return n.getHours() * 60 + n.getMinutes(); };
const slotIndex = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return (h * 60 + m - SLOT_FIRST) / 30;
};
const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------ 2. state + data loading
let G = null;              // the whole grid file
let dayKeys = [];          // its dates, sorted
let dayIdx = 0;            // which day is on screen
let view = "today";        // today | month | info
let selTime = null;        // slot selected in the table (defaults to "now")
let liveFlag = null;       // computed client-side from the EA API
let gridSource = "live";   // or "cache" — shown when offline

async function loadGrid() {
  try {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 8000);
    const r = await fetch(GRID_URL, { cache: "no-store", signal: ctrl.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const g = await r.json();
    try { localStorage.setItem("tideway-grid", JSON.stringify(g)); } catch (e) {}
    return { g, source: "live" };
  } catch (err) {
    // offline / pipeline unreachable: last good copy, two layers deep
    try {
      const s = localStorage.getItem("tideway-grid");
      if (s) return { g: JSON.parse(s), source: "cache" };
    } catch (e) {}
    if (window.caches) {
      const hit = await caches.match(GRID_URL);
      if (hit) return { g: await hit.json(), source: "cache" };
    }
    throw err;
  }
}

// The EA flag, computed exactly like pipeline/fetch_flag.py.
function flagBand(v) {
  if (v >= 2.6) return "RED";
  if (v >= 1.7) return "YELLOW";
  if (v >= 0.0) return "GREEN";
  return "BLACK";
}
async function refreshLiveFlag() {
  try {
    const since = new Date(Date.now() - 36 * 3600e3)
      .toISOString().replace(/\.\d+Z$/, "Z");
    const r = await fetch(EA_URL + since);
    if (!r.ok) throw new Error(`EA HTTP ${r.status}`);
    const items = (await r.json()).items || [];
    const reads = items
      .map((it) => ({ t: new Date(it.dateTime), v: Number(it.value) }))
      .filter((x) => Number.isFinite(x.v))
      .sort((a, b) => a.t - b.t);
    if (!reads.length) throw new Error("EA returned no readings");

    const now = new Date();
    const b = new Date(now);                     // last 06:00/18:00 boundary
    b.setMinutes(0, 0, 0);
    if (now.getHours() >= 18) b.setHours(18);
    else if (now.getHours() >= 6) b.setHours(6);
    else { b.setDate(b.getDate() - 1); b.setHours(18); }

    const minIn = (t0, t1) => {
      const vals = reads.filter((x) => x.t >= t0 && x.t <= t1).map((x) => x.v);
      return vals.length ? Math.min(...vals) : null;
    };
    const curMin = minIn(new Date(b - 12 * 3600e3), b);
    const trailMin = minIn(new Date(now - 12 * 3600e3), now);
    const last = reads[reads.length - 1];
    liveFlag = {
      source: "live",
      latest: { time: last.t, level_m: last.v },
      current: curMin === null ? null :
        { flag: flagBand(curMin), set_at: b, min_12h_m: curMin },
      next: trailMin === null ? null :
        { flag_estimate: flagBand(trailMin),
          at: new Date(b.getTime() + 12 * 3600e3), trailing_min_m: trailMin },
    };
  } catch (err) {
    liveFlag = null;   // fall back to the grid's pipeline snapshot
  }
  renderFlag();
  renderStats();
}

// ------------------------------------------------ 3. render, top down
function dayRows() { return G.grid[dayKeys[dayIdx]] || []; }
function isToday() { return dayKeys[dayIdx] === todayKey(); }

// the slot the hero/stat panels describe: live "now" on today,
// else whatever the user tapped, else the first daylight slot
function focusRow() {
  const rows = dayRows();
  if (selTime) return rows.find((r) => r.time === selTime) || null;
  if (isToday()) {
    const idx = Math.floor((minsNow() - SLOT_FIRST) / 30);
    if (idx >= 0 && idx < rows.length) return rows[idx];
    return null;                    // before 04:30 or after 20:30
  }
  return rows.find((r) => r.overall === "ROW") || rows[0] || null;
}

function renderTopbar() {
  $("dayLabel").textContent = dayShort(dayKeys[dayIdx]);
  $("prevDay").disabled = dayIdx === 0;
  $("nextDay").disabled = dayIdx === dayKeys.length - 1;
  const off = -slotDate(dayKeys[dayIdx], "12:00").getTimezoneOffset();
  $("tzLabel").textContent = off === 60 ? "BST" : "GMT";
}

function renderBanner() {
  const el = $("banner");
  const ageH = (Date.now() - new Date(G.generated).getTime()) / 3600e3;
  if (gridSource === "cache") {
    el.textContent = `OFFLINE — showing the last saved grid (${Math.round(ageH)} h old)`;
    el.classList.add("show");
  } else if (ageH > STALE_AFTER_H) {
    el.textContent = `GRID IS ${Math.round(ageH)} H OLD — the pipeline may be failing; check the Actions tab`;
    el.classList.add("show");
  } else {
    el.classList.remove("show");
  }
  const gen = new Date(G.generated);
  $("genNote").textContent =
    `grid generated ${pad(gen.getHours())}:${pad(gen.getMinutes())} · ` +
    `${gen.getDate()} ${MONTHS[gen.getMonth()]} · model ${G.model}` +
    (gridSource === "cache" ? " · OFFLINE COPY" : "");
}

// ---- the ring: 33 arcs, one per 30-min slot, 04:30 at the top going clockwise
function polar(cx, cy, r, deg) {
  const rad = (deg - 90) * Math.PI / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}
function arcPath(cx, cy, r, a0, a1) {
  const [x0, y0] = polar(cx, cy, r, a0);
  const [x1, y1] = polar(cx, cy, r, a1);
  return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}
const RING_COLORS = { g: "#2E7D32", a: "#F2A71B", r: "#C62828", t: "#1B7A8C" };

function slotColor(row) {
  if (!row) return { color: "rgba(255,255,255,.06)", op: 1 };
  if (row.tide_only) {
    const c = RING_COLORS[LIGHT_CLS[row.tide_light]] || "#54677A";
    return { color: c, op: 0.4 };                    // dimmed: tide only
  }
  const v = VERDICTS[row.overall];
  return { color: v ? RING_COLORS[v.cls] : "#54677A", op: 1 };
}

function renderRing() {
  const rows = dayRows();
  const cx = 195, cy = 150, R = 108;
  const span = 360 / N_SLOTS, gap = 1.6;
  let svg = "";
  for (let i = 0; i < N_SLOTS; i++) {
    const row = rows[i];
    const { color, op } = slotColor(row);
    const a0 = i * span, a1 = (i + 1) * span - gap;
    svg += `<path d="${arcPath(cx, cy, R, a0, a1)}" stroke="${color}"
      stroke-opacity="${op}" stroke-width="22" fill="none"/>`;
  }
  for (const h of [6, 9, 12, 15, 18]) {           // hour ticks inside the ring
    const a = ((h * 60 - SLOT_FIRST) / (16 * 60)) * 360;
    const [x, y] = polar(cx, cy, 80, a);
    svg += `<text x="${x}" y="${y + 3}" text-anchor="middle" font-size="10"
      fill="#54677A">${pad(h)}</text>`;
  }
  if (isToday()) {                                 // the "now" radial marker
    const m = minsNow();
    if (m >= SLOT_FIRST && m <= SLOT_LAST + 29) {
      const a = ((m - SLOT_FIRST) / (16 * 60)) * 360;
      const [x0, y0] = polar(cx, cy, 92, a);
      const [x1, y1] = polar(cx, cy, 124, a);
      svg += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"
        stroke="#FFFFFF" stroke-width="2.5" stroke-linecap="round"/>`;
    }
  }
  $("ring").innerHTML = svg;
}

// windows of consecutive slots for the hero status + WINDOWS chips
function findRuns(rows, accept) {
  const runs = [];
  let start = null, last = null;
  rows.forEach((r) => {
    if (accept(r)) { if (start === null) start = r.time; last = r; }
    else if (start !== null) { runs.push([start, last.time]); start = null; }
  });
  if (start !== null) runs.push([start, rows[rows.length - 1].time]);
  return runs;
}
const plus30 = (hhmm) => {                 // slot start -> its end time
  const [h, m] = hhmm.split(":").map(Number);
  const t = h * 60 + m + 30;
  return `${pad(Math.floor(t / 60))}:${pad(t % 60)}`;
};

function renderHero() {
  const rows = dayRows();
  const cap = $("heroCap"), word = $("heroWord"),
        chip = $("heroChip"), status = $("heroStatus");
  chip.hidden = true;
  status.innerHTML = "";
  const windRows = rows.filter((r) => !r.tide_only);
  const tideOnlyDay = rows.length > 0 && windRows.length === 0;

  const setWord = (txt, cls) => { word.textContent = txt; word.className = `word num ${cls}`; };

  if (tideOnlyDay) {
    cap.textContent = dayShort(dayKeys[dayIdx]);
    setWord("TIDE ONLY", "info");
    const ok = findRuns(rows, (r) => r.tide_light === "GREEN");
    status.innerHTML = ok.length
      ? `<span class="m">TIDE WINDOWS ${ok.map(([a, b]) => `${a}–${plus30(b)}`).join(" · ")}</span>`
      : `<span class="m">NO GREEN TIDE WINDOW</span>`;
    return;
  }

  if (isToday()) {
    const m = minsNow();
    const idx = Math.floor((m - SLOT_FIRST) / 30);
    if (m < SLOT_FIRST) {
      const first = windRows.find((r) => VERDICTS[r.overall]?.rank < 2);
      cap.textContent = "FIRST WINDOW";
      setWord(first ? first.time : "NONE", "info");
      status.innerHTML = first
        ? `<span class="m">${first.overall === "ROW" ? "GREEN" : "AMBER"} FROM ${first.time}</span>`
        : `<span class="r">NO WINDOW TODAY</span>`;
      return;
    }
    if (idx >= rows.length) {
      cap.textContent = `NOW · ${pad(new Date().getHours())}:${pad(new Date().getMinutes())}`;
      setWord("DONE", "info");
      status.innerHTML = `<span class="m">TODAY'S DONE — BROWSE ›</span>`;
      return;
    }
    const row = rows[idx];
    cap.textContent = `NOW · ${pad(new Date().getHours())}:${pad(new Date().getMinutes())}`;
    heroForRow(row, rows, idx, setWord, chip, status);
    return;
  }

  // another (wind-known) day: lead with its best window
  const ideal = findRuns(windRows, (r) => r.overall === "ROW");
  const okRun = findRuns(windRows, (r) => VERDICTS[r.overall]?.rank < 2);
  cap.textContent = dayShort(dayKeys[dayIdx]);
  if (ideal.length) {
    setWord(ideal[0][0], "row");
    status.innerHTML = `<span class="g">IDEAL ${ideal.map(([a, b]) => `${a}–${plus30(b)}`).join(" · ")}</span>`;
  } else if (okRun.length) {
    setWord(okRun[0][0], "care");
    status.innerHTML = `<span class="a">WITH CARE ${okRun.map(([a, b]) => `${a}–${plus30(b)}`).join(" · ")}</span>`;
  } else {
    setWord("NO WINDOW", "dont");
    status.innerHTML = `<span class="r">NOTHING ROWABLE</span>`;
  }
}

function heroForRow(row, rows, idx, setWord, chip, status) {
  const v = VERDICTS[row.overall];
  if (row.overall === "ROW") setWord("ROW", "row");
  else if (row.overall === "Caution") { setWord("ROW", "care"); chip.hidden = false; }
  else if (row.overall === "No launch (embankment)") setWord("NO LAUNCH", "gate");
  else setWord("DON'T ROW", "dont");

  const lines = [];
  if (v.rank < 2) {                       // rowable: when does it get worse?
    let until = null;
    for (let j = idx + 1; j < rows.length; j++) {
      if (rows[j].tide_only) break;
      if (VERDICTS[rows[j].overall].rank > v.rank) { until = rows[j]; break; }
    }
    lines.push(until
      ? `<span class="${VERDICTS[until.overall].rank === 2 ? "r" : "a"}">` +
        `${VERDICTS[until.overall].rank === 2 ? "RED" : "AMBER"} AT ${until.time}${inHM(until.time)}</span>`
      : `<span class="g">CLEAR TO ${plus30(rows[rows.length - 1].time)}</span>`);
  } else {                                // not rowable: when can I go?
    const next = rows.slice(idx + 1).find(
      (r) => !r.tide_only && VERDICTS[r.overall].rank < 2);
    lines.push(next
      ? `<span class="g">ROWABLE AT ${next.time}${inHM(next.time)}</span>`
      : `<span class="r">NO WINDOW LEFT TODAY</span>`);
  }
  if (row.day_deteriorates_at)
    lines.push(`<span class="m">DETERIORATES FROM ${row.day_deteriorates_at}</span>`);
  if (row.notes)
    lines.push(`<span class="m">${esc(row.notes.toUpperCase())}</span>`);
  status.innerHTML = lines.join("<br>");
}
function inHM(hhmm) {
  const mins = slotIndex(hhmm) * 30 + SLOT_FIRST - minsNow();
  if (mins <= 0) return "";
  const h = Math.floor(mins / 60), m = mins % 60;
  return ` — in ${h ? h + "h " : ""}${m}m`;
}

function renderStats() {
  const row = focusRow();
  const t = $("statTide"), w = $("statWind"), f = $("statFlag");
  if (row) {
    t.innerHTML = `${row.height_putney_m.toFixed(1)}<span class="u"> m</span>`;
    t.className = `v num ${LIGHT_CLS[row.tide_light] || "m"}`;
    if (row.wind_mph === null) { w.textContent = "–"; w.className = "v num m"; }
    else {
      w.innerHTML = `${row.wind_mph}<span class="u"> mph · G${row.gust_mph}</span>`;
      w.className = `v num ${LIGHT_CLS[row.wind_light] || "m"}`;
    }
  } else { t.textContent = w.textContent = "–"; }
  const flag = liveFlag || G.flag;
  const cur = flag && flag.current;
  f.textContent = cur ? cur.flag : "–";
  f.style.fontSize = "18px"; f.style.paddingTop = "5px";
  f.className = "v num " +
    (cur ? ({ RED: "r", YELLOW: "a", GREEN: "g", BLACK: "black" }[cur.flag] || "m") : "m");
}

function renderTurns() {
  const row = focusRow();
  const box = $("turns"), meta = $("turnsMeta"), title = $("turnsTitle");
  if (!row || !row.sessions) {
    title.textContent = "HOW FAR CAN I GO";
    box.innerHTML = `<span class="turnmeta">wind unknown this far out — no session verdicts</span>`;
    meta.textContent = "";
    return;
  }
  title.textContent = `HOW FAR CAN I GO — AT ${row.time}`;
  const cls = { "Row": "row", "Row (care)": "care", "Don't row": "dont" };
  box.innerHTML = Object.entries(row.sessions).map(([name, s]) =>
    `<span class="turn ${cls[s.verdict] || "dont"}${name === row.best_turn ? " best" : ""}">
       ${esc(name.toUpperCase())}<small>${s.duration_min}′ · ${esc(s.verdict.toUpperCase())}</small>
     </span>`).join("");
  const worst = Object.values(row.sessions)
    .filter((s) => s.worst_reach)
    .sort((a, b) => (b.worst_light === "RED") - (a.worst_light === "RED"))[0];
  meta.textContent =
    (row.best_turn ? `BEST TURN ${row.best_turn.toUpperCase()} · ` : "") +
    (worst ? `worst ${worst.worst_light} @ ${worst.worst_reach}` : "all reaches green") +
    (row.confidence_90min ? ` · ensemble ${Math.round(row.confidence_90min.p_rowable * 100)}% rowable` : "");
}

function renderHwlw() {
  const ev = (G.putney_hwlw[dayKeys[dayIdx]] || []).slice(0, 4);
  $("hwlw").innerHTML = ev.map((e) => `
    <div class="card">
      <div class="t ${e.type.toLowerCase()}">${e.type}</div>
      <div class="when num">${e.time}</div>
      <div class="h">${e.height_m.toFixed(1)} m</div>
    </div>`).join("");
}

// ---- tide curve: cosine interpolation between HW/LW events — the same
// shape the model itself uses, so the curve never disagrees with the grid
function dayEvents(dayKey) {
  const all = [];
  for (const [k, evs] of Object.entries(G.putney_hwlw))
    for (const e of evs) all.push({ t: slotDate(k, e.time), h: e.height_m, type: e.type });
  all.sort((a, b) => a.t - b.t);
  const d0 = slotDate(dayKey, "00:00"), d1 = new Date(d0.getTime() + 24 * 3600e3);
  const inWin = [];
  for (let i = 0; i < all.length; i++) {
    if (all[i].t >= d0 && all[i].t <= d1) {
      if (!inWin.length && i > 0) inWin.push(all[i - 1]);   // one before
      inWin.push(all[i]);
    } else if (inWin.length && all[i].t > d1) { inWin.push(all[i]); break; }
  }
  return inWin;
}
function heightAt(evs, t) {
  for (let i = 0; i < evs.length - 1; i++) {
    const a = evs[i], b = evs[i + 1];
    if (t >= a.t && t <= b.t) {
      const tau = (t - a.t) / (b.t - a.t);
      return a.h + (b.h - a.h) * (1 - Math.cos(Math.PI * tau)) / 2;
    }
  }
  return null;
}

function renderCurve() {
  const dayKey = dayKeys[dayIdx];
  const rows = dayRows();
  const evs = dayEvents(dayKey);
  const X0 = 30, X1 = 384, Y0 = 14, Y1 = 148;
  const T0 = slotDate(dayKey, "04:00"), T1 = slotDate(dayKey, "21:00");
  const x = (t) => X0 + (t - T0) / (T1 - T0) * (X1 - X0);
  const maxH = Math.max(5, ...evs.map((e) => e.h)) + 0.4;
  const y = (h) => Y1 - (h / maxH) * (Y1 - Y0);

  let svg = `<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="rgba(74,163,223,.25)"/>
    <stop offset="1" stop-color="rgba(74,163,223,0)"/></linearGradient></defs>`;

  for (let gm = 2; gm < maxH; gm += 2)               // gridlines every 2 m
    svg += `<line x1="${X0}" y1="${y(gm)}" x2="${X1}" y2="${y(gm)}"
      stroke="rgba(255,255,255,.07)"/><text x="${X0 - 4}" y="${y(gm) + 3}"
      text-anchor="end" font-size="8" fill="#54677A">${gm}m</text>`;

  const pts = [];
  for (let t = T0.getTime(); t <= T1.getTime(); t += 10 * 60e3) {
    const h = heightAt(evs, new Date(t));
    if (h !== null) pts.push([x(new Date(t)), y(h)]);
  }
  if (pts.length > 1) {
    const line = pts.map(([px, py], i) =>
      `${i ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ");
    svg += `<path d="${line} L ${pts[pts.length - 1][0].toFixed(1)} ${Y1}
      L ${pts[0][0].toFixed(1)} ${Y1} Z" fill="url(#fade)"/>`;
    svg += `<path d="${line}" stroke="#4AA3DF" stroke-width="2" fill="none"/>`;
  }

  for (const e of evs) {                             // HW/LW dots + labels
    if (e.t < T0 || e.t > T1) continue;
    svg += `<circle cx="${x(e.t)}" cy="${y(e.h)}" r="3" fill="#fff"
        stroke="#0D1B2A" stroke-width="1.5"/>
      <text x="${x(e.t)}" y="${y(e.h) + (e.type === "HW" ? -8 : 14)}"
        text-anchor="middle" font-size="9" fill="#C3CCD2"
        >${e.type} ${pad(e.t.getHours())}:${pad(e.t.getMinutes())} · ${e.h.toFixed(1)}m</text>`;
  }

  // TIDE / WIND mini strips: the day at a glance, same palette as everything
  const cellW = (X1 - X0) / N_SLOTS;
  const strip = (yTop, label, colorOf) => {
    let s = `<text x="${X0 - 4}" y="${yTop + 7}" text-anchor="end"
      font-size="7.5" letter-spacing="1" fill="#54677A">${label}</text>`;
    for (let i = 0; i < N_SLOTS; i++) {
      const r = rows[i];
      const c = r ? colorOf(r) : null;
      s += `<rect x="${(X0 + i * cellW).toFixed(1)}" y="${yTop}"
        width="${(cellW - 1.2).toFixed(1)}" height="8" rx="1.5"
        fill="${c ? c.color : "rgba(255,255,255,.05)"}" fill-opacity="${c ? c.op : 1}"/>`;
    }
    return s;
  };
  svg += strip(170, "TIDE", (r) =>
    ({ color: RING_COLORS[LIGHT_CLS[r.tide_light]] || "#54677A", op: r.tide_only ? 0.5 : 1 }));
  svg += strip(184, "WIND", (r) => r.wind_light
    ? { color: RING_COLORS[LIGHT_CLS[r.wind_light]], op: 1 }
    : { color: "rgba(255,255,255,.05)", op: 1 });

  for (const h of [6, 9, 12, 15, 18, 21])            // hour axis
    svg += `<text x="${x(slotDate(dayKey, pad(h) + ":00"))}" y="212"
      text-anchor="middle" font-size="8" fill="#54677A">${pad(h)}</text>`;

  if (isToday()) {                                   // the "now" line
    const now = new Date();
    if (now >= T0 && now <= T1) {
      svg += `<line x1="${x(now)}" y1="${Y0}" x2="${x(now)}" y2="${Y1 + 44}"
        stroke="#fff" stroke-width="1.5" stroke-opacity=".8"/>
        <circle cx="${x(now)}" cy="${Y0}" r="2.5" fill="#fff"/>`;
    }
  }
  $("curve").innerHTML = svg;
}

function renderWindows() {
  const rows = dayRows();
  const windRows = rows.filter((r) => !r.tide_only);
  $("windowsTitle").textContent = `WINDOWS — ${dayShort(dayKeys[dayIdx])}`;
  let chips = [];
  if (windRows.length) {
    findRuns(windRows, (r) => r.overall === "ROW").forEach(([a, b]) =>
      chips.push(`<span class="win ideal">IDEAL ${a}–${plus30(b)}</span>`));
    findRuns(windRows, (r) => r.overall === "Caution").forEach(([a, b]) =>
      chips.push(`<span class="win ok">CARE ${a}–${plus30(b)}</span>`));
    findRuns(windRows, (r) => VERDICTS[r.overall]?.rank === 2).forEach(([a, b]) =>
      chips.push(`<span class="win closed">✕ ${a}–${plus30(b)}</span>`));
  } else {
    findRuns(rows, (r) => r.tide_light === "GREEN").forEach(([a, b]) =>
      chips.push(`<span class="win ok">TIDE ${a}–${plus30(b)}</span>`));
    chips.push(`<span class="win none">WIND UNKNOWN THIS FAR OUT</span>`);
  }
  $("windows").innerHTML = chips.slice(0, 8).join("") ||
    `<span class="win none">NO DATA</span>`;
}

function renderSlots() {
  const rows = dayRows();
  const el = $("slots");
  const nowIdx = isToday() ? Math.floor((minsNow() - SLOT_FIRST) / 30) : -1;
  const collapsed = el.dataset.expanded !== "1" && nowIdx > 2;

  let html = `<div class="head"><span>TIME</span><span>TIDE</span><span>WIND</span>
    <span style="text-align:right">VERDICT</span></div>`;
  if (collapsed)
    html += `<button class="toggle" id="slotToggle">▸ EARLIER — 04:30 TO ${rows[nowIdx - 1]?.time || ""}</button>`;

  rows.forEach((r, i) => {
    if (collapsed && i < nowIdx) return;
    const v = r.tide_only ? null : VERDICTS[r.overall];
    const vcls = r.tide_only ? "n" : (v ? v.cls : "n");
    const word = r.tide_only ? "TIDE ONLY" : (v ? v.word : "–");
    const glyph = r.tide_status.startsWith("High water") ? "◦"
      : r.tide_status.startsWith("Low water") ? "◦" : (r.flooding ? "▴" : "▾");
    const wind = r.wind_mph === null ? `<small>—</small>`
      : `<span class="wind-arrow" style="transform:rotate(${r.wind_dir + 180}deg)">↑</span>
         ${r.wind_mph} <small>· G${r.gust_mph}</small>`;
    const rowCls = ["row"];
    if (i < nowIdx) rowCls.push("past");
    if (v) rowCls.push({ g: "vrow", a: "vcare", r: "vdont", t: "vgate" }[v.cls]);
    if (selTime === r.time) rowCls.push("sel");
    html += `<div class="${rowCls.join(" ")}" data-t="${r.time}">
      <span class="time num">${r.time}</span>
      <span class="cell">${r.height_putney_m.toFixed(1)}m ${glyph}
        ${r.hw_gate ? `<small>gate:${r.hw_gate}</small>` : ""}</span>
      <span class="cell">${wind}</span>
      <span class="verdict"><i class="sw ${vcls}"></i><span class="v${vcls}">${word}</span></span>
    </div>`;
  });
  el.innerHTML = html;

  const tog = $("slotToggle");
  if (tog) tog.onclick = () => { el.dataset.expanded = "1"; renderSlots(); };
  el.querySelectorAll(".row").forEach((n) => n.addEventListener("click", () => {
    selTime = selTime === n.dataset.t ? null : n.dataset.t;
    renderSlots(); renderTurns(); renderStats();
  }));
}

const FLAG_ACTIONS = {
  BLACK: "BLACK — don't boat at low tide. The LW band is binding today.",
  RED: "RED — CRSA clearance required; low flood only, no ebb outings.",
  YELLOW: "YELLOW — reduced flow but caution: check stream at Putney before boating.",
  GREEN: "GREEN — normal caution and a good lookout.",
};

function renderFlag() {
  const chipEl = $("flagChip"), meta = $("flagMeta"), action = $("flagAction");
  const f = liveFlag || G.flag;
  if (!f || !f.current) {
    chipEl.textContent = "–"; chipEl.className = "flagchip num";
    meta.textContent = "no flag data"; action.textContent = "";
    return;
  }
  const fmtT = (t) => { const d = new Date(t);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  chipEl.textContent = f.current.flag;
  chipEl.className = `flagchip num ${f.current.flag}`;
  const nxt = f.next;
  meta.innerHTML =
    `Richmond now <b>${Number(f.latest.level_m).toFixed(2)} m</b> · ${fmtT(f.latest.time)}<br>` +
    (nxt ? `next setting ${fmtT(nxt.at)} — trending <b>${nxt.flag_estimate}</b>
      (12 h min ${Number(nxt.trailing_min_m).toFixed(2)} m)<br>` : "") +
    `<span class="src">${liveFlag ? "live · EA gauge 0009" :
      `pipeline snapshot ${G.flag && G.flag.fetched ? G.flag.fetched.slice(11, 16) : ""}`}
      · <a href="${PLA_FLAG_PAGE}" target="_blank" rel="noopener">cross-check PLA</a></span>`;
  action.textContent = FLAG_ACTIONS[f.current.flag] || "";
  $("mustFlag").textContent = `Check the flag on the day — computed now: ${f.current.flag}.`;
}

// ---- month view: 35 days, tide-first, wind overlaid only where it exists
function renderMonth() {
  const el = $("viewMonth");
  let html = "";
  let lastMonth = null;
  dayKeys.forEach((k, i) => {
    const d = slotDate(k, "12:00");
    if (d.getMonth() !== lastMonth) {
      lastMonth = d.getMonth();
      html += `<div class="weekhdr">${MONTHS[lastMonth]} ${d.getFullYear()}</div>`;
    }
    const rows = G.grid[k] || [];
    const windRows = rows.filter((r) => !r.tide_only);
    const strip = Array.from({ length: N_SLOTS }, (_, s) => {
      const r = rows[s];
      if (!r) return `<i class="x"></i>`;
      if (r.tide_only)
        return `<i class="${(LIGHT_CLS[r.tide_light] || "x") + "2"}"></i>`;
      const v = VERDICTS[r.overall];
      return `<i class="${v ? v.cls : "x"}"></i>`;
    }).join("");

    let sub;
    if (windRows.length) {
      const ideal = findRuns(windRows, (r) => r.overall === "ROW");
      const firstOk = windRows.find((r) => VERDICTS[r.overall]?.rank < 2);
      const conf = ideal.length &&
        windRows.find((r) => r.time === ideal[0][0])?.confidence_90min;
      sub = (firstOk ? `<span class="best">BEST ${firstOk.time}</span> · ` : "") +
        (ideal.length
          ? `ideal ${ideal.map(([a, b]) => `${a}–${plus30(b)}`).join(", ")}`
          : "nothing ideal") +
        (conf ? ` <span class="conf">· ${Math.round(conf.p_rowable * 100)}%</span>` : "");
    } else {
      const ok = findRuns(rows, (r) => r.tide_light === "GREEN");
      sub = `<span class="tideonly">TIDE ONLY</span> · ` +
        (ok.length ? ok.map(([a, b]) => `${a}–${plus30(b)}`).join(", ") : "no green band");
    }
    html += `<div class="mrow${k === todayKey() ? " today" : ""}" data-i="${i}">
      <div class="d"><div class="a num">${DAYS[d.getDay()]}</div>
        <div class="n">${d.getDate()} ${MONTHS[d.getMonth()]}</div></div>
      <div><div class="strip">${strip}</div><div class="sub">${sub}</div></div>
    </div>`;
  });
  html += `<div class="legend">
    <span><i class="sw g"></i>row</span><span><i class="sw a"></i>care</span>
    <span><i class="sw r"></i>don't</span><span><i class="sw t"></i>no launch</span>
    <span><i class="sw" style="background:rgba(46,125,50,.38)"></i>tide-only (dimmed)</span></div>`;
  el.innerHTML = html;
  el.querySelectorAll(".mrow").forEach((n) => n.addEventListener("click", () => {
    dayIdx = Number(n.dataset.i); selTime = null; switchView("today");
  }));
}

// ------------------------------------------------ 4. wiring
function renderToday() {
  renderRing(); renderHero(); renderStats(); renderTurns();
  renderHwlw(); renderCurve(); renderWindows(); renderSlots(); renderFlag();
}
function renderAll() {
  renderTopbar(); renderBanner();
  if (view === "today") renderToday();
  if (view === "month") renderMonth();
}
function switchView(v) {
  view = v;
  $("viewToday").hidden = v !== "today";
  $("viewMonth").hidden = v !== "month";
  $("viewInfo").hidden = v !== "info";
  for (const [id, name] of [["tabToday", "today"], ["tabMonth", "month"], ["tabInfo", "info"]])
    $(id).classList.toggle("active", name === view);
  renderAll();
}

function moveDay(delta) {
  dayIdx = Math.max(0, Math.min(dayKeys.length - 1, dayIdx + delta));
  selTime = null;
  const slots = $("slots"); delete slots.dataset.expanded;
  renderAll();
}

async function main() {
  try {
    const { g, source } = await loadGrid();
    G = g; gridSource = source;
  } catch (err) {
    $("banner").textContent = "NO DATA — first load needs a connection";
    $("banner").classList.add("show");
    return;
  }
  dayKeys = Object.keys(G.grid).sort();
  const t = dayKeys.indexOf(todayKey());
  dayIdx = t >= 0 ? t : 0;

  $("prevDay").onclick = () => moveDay(-1);
  $("nextDay").onclick = () => moveDay(1);
  $("tabToday").onclick = () => switchView("today");
  $("tabMonth").onclick = () => switchView("month");
  $("tabInfo").onclick = () => switchView("info");

  renderAll();
  refreshLiveFlag();
  setInterval(refreshLiveFlag, 5 * 60e3);          // live level, 5-minutely
  setInterval(() => { if (view === "today") renderToday(); }, 30e3); // "now"

  if ("serviceWorker" in navigator)
    navigator.serviceWorker.register("sw.js").catch(() => {});
}

main();
