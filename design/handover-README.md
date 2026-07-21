# Handoff: TIDEGUIDE v2 — Thames Tidal Rowing Guide (PWA)

## Overview
A mobile-first go/no-go app for rowers launching from Putney (London RC). It renders a 35-day forecast grid (`data/grid_v2.json`, regenerated every 6 h by an external pipeline) into three views: a **Day view** (verdict ring, tide chart, session planner, slot table, ebb-flag card), a **Month view** (one bar per day), and an **Info view** (how to read everything). It also computes the PLA ebb-tide flag live from EA river-level readings. The app is display-only: **the grid JSON is the single source of truth — the app renders it and adds nothing** (the one exception: the live EA flag, which is explicitly context-only and never changes a verdict).

## About the Design Files
The files in this bundle are **design references created in HTML** — a working prototype showing intended look and behavior, not production code to copy directly. Your task is to **recreate this design in the target codebase's existing environment** (React, Vue, Svelte, native, etc.) using its established patterns — or, if no codebase exists yet, choose an appropriate lightweight stack (this is a single-screen PWA; vanilla + a small framework is plenty). The prototype's logic (in the `<script data-dc-script>` block of `TIDEGUIDE v2.dc.html`) is straightforward classic JS and is safe to port nearly 1:1; the templating/runtime (`support.js`) is prototype scaffolding — do not ship it.

## Fidelity
**High-fidelity.** Colors, type, spacing, copy, and interaction states are final. Recreate pixel-perfectly.

## Runtime Contract (endpoints, storage, timers)
- **Data**: `fetch('data/grid_v2.json', {cache:'no-store'})` once on load. On success, cache the raw JSON in `localStorage` (prototype key: `tideguide_grid_v2`). On failure, fall back to that cache and show the OFFLINE banner; if neither exists, show the load-failure screen.
- **Live flag**: one GET to `https://environment.data.gov.uk/flood-monitoring/id/stations/0009/readings?parameter=level&_sorted&since=<now-36h ISO>`. On failure, silently use the `flag` snapshot embedded in the grid JSON.
- **Timers**: a 20 s re-render tick (updates the NOW line/countdowns; no network), plus a re-render on `visibilitychange`. No polling.
- **Banners** (stacked into one amber pill, ` · `-separated): `OFFLINE — SHOWING SAVED GRID`; `GRID IS <N>H OLD — PIPELINE MAY BE FAILING` when `generated` is ≥ 13 h old; `TODAY NOT IN GRID — SHOWING <first day>` when today's date is missing; `TODAY'S DONE — SHOWING TOMORROW` when auto-advanced after 20:30.

### grid_v2.json fields consumed
`generated`, `wind_horizon`; per slot (`grid.<YYYY-MM-DD>[]`, 33 slots 04:30–20:30 at 30 min): `time`, `height_putney_m`, `tide_status`, `tide_light`, `wind_light`, `overall`, `wind_mph`, `gust_mph`, `wind_dir`, `wind_over_tide`, `hw_gate`, `notes`, `tide_only`, `day_deteriorates_at`, `confidence_90min.p_rowable`, `best_turn`, `sessions.<turn>.{verdict,duration_min,worst_reach}`; `putney_hwlw.<date>[].{type,time,height_m}`; `flag.{fetched,current.flag,latest.level_m,latest.time,next.flag_estimate,next.at}`.
Ignored: `model`, `hw_gate_putney_m`, `flag.current.min_12h_m`, `flag.next.trailing_min_m`.

### Verdict mapping (`overall` → UI)
- `"ROW"` → ROW, green `#42E34D`
- `"Caution"` → CARE, yellow `#F8C81C`
- `"Don't row"` → DON'T, red `#FF3B30`
- `"No launch (embankment)"` → NO LAUNCH, red (white-bordered swatch in legend)
- `tide_only: true` → no verdict; render dimmed tide lights only (38 %-alpha green/amber/red), and **exclude `hw_gate === "RED"` slots from tide windows** (gate is tide-deterministic, so this is honest without wind).

### Ebb-flag computation (live EA)
Band by metres: `< 0` BLACK · `0–1.7` GREEN · `1.7–2.6` YELLOW · `≥ 2.6` RED. Current flag = band of the **minimum reading in the 12 h preceding the last 06:00/18:00 setting time**; "next setting" estimate = band of the trailing-12 h minimum from now. Display source label `LIVE EA · HH:MM`, or `SNAPSHOT · HH:MM` when using the JSON fallback. Always display-only.

## Screens / Views
All views live in one 460 px max-width column, centered, on a page gradient `linear-gradient(180deg,#24282D 0%,#101214 38%,#0A0B0D 100%)`. Header: wave glyph (left), day-pill navigator `‹ TUE 21 JUL ›` (center, 1px `rgba(255,255,255,.18)` border, pill radius; tapping the label opens Month), `BST` tag (right). Below: TODAY / MONTH / INFO tabs (11px/600/2.2px tracking; active = white + 2px white underline, inactive `#5F6B73`).

### 1. Day view (`TODAY`)
- **Verdict ring**: SVG, 33 arc segments (one per slot, 22px stroke, 1.5° gap), radius 108 in a 390×300 viewBox; colored by verdict (or dimmed tide light on tide-only days). Hour labels 06–18 inside; HW/LW event labels outside; white radial NOW needle when live. Center stack: label (9.5px `#98A2AB`), hero word (Barlow Condensed 700; ROW 64px green / DON'T ROW 44px red / NO LAUNCH 40px + chip / first-window time 56px white), optional bordered chip (WITH CARE / TIDE ONLY / FIRST WINDOW / EMBANKMENT ≥ 6.3 M), then up to ~3 status lines (10.5px/600) — e.g. `GREEN UNTIL 14:30`, `RED AT 15:30 — IN 5H 48M`, `DETERIORATES FROM 13:00` (amber).
- **Live stats row** (live only): interpolated tide height + phase (`5.4 M · TIDE · SLACK`, colored by tide light) | divider | wind (`6 MPH NNE · WIND · GUST 14`, colored by wind light).
- **HW/LW cards**: up to 4, `rgba(255,255,255,.04)` bg, `.08` border, radius 8; type (HW amber `#F8C81C` / LW cyan `#4FB3C9`), time (Barlow Condensed 17px), height.
- **Tide chart**: 390×212 SVG; Catmull-Rom-smoothed height curve, 2px `#00A8E8`, fill fading from `rgba(0,168,232,.22)`; y-grid 0/2/4/6 m; HW/LW dots + labels; two 8px strip rows (TIDE, WIND) of per-slot lights; hour axis; white NOW line + dot when live.
- **HOW FAR CAN I GO — sessions card** (card `#15181B`, border `rgba(255,255,255,.08)`, radius 10): header + `LAUNCHING NOW · 09:42 · 100% CONF`; when a slot is selected in the table (see below) the card re-aims to that slot — header `LAUNCHING 09:00 · 100% CONF  ✕` in white, ✕ tap clears; chips per turn point (HAMMERSMITH / CORNEY / ULBC / CHISWICK BR / KEW) with verdict dot + duration (`90′`); `best_turn` chip gets tinted bg + stronger border; footer `BEST TURN — KEW · 90 MIN ROUND TRIP` (green) or `NO TURN ROWABLE IN ≤ 135 MIN…` (red); optional `WORST REACH — …` line.
- **Windows chips**: `IDEAL 06:30–14:00 · 100%` (filled green tint), plain rowable windows (green outline), `✕ 15:00–18:00` gaps (red outline). Tide-only days: dimmed `TIDE hh–hh` chips + `TIDE ONLY — NO WIND YET`.
- **Slot table**: grid `50px 92px 1fr 80px` (TIME / TIDE / WIND / VERDICT). Time in Barlow Condensed 15px (past slots dimmed when live); tide = height + ▾ EBB / ▴ FLOOD / ◦ SLACK in the tide-light color (white-space: nowrap); wind = rotated arrow (from `wind_dir`, pointing where wind blows to) + `6 NNE · G15` (nowrap), or `—` on tide-only; verdict = dot + word, row tinted by verdict at 4–5 % alpha. Full-width note line (10.5px `#6E7A83`) for `notes` + HW-gate warnings (skip the gate prefix when the pipeline note already mentions the embankment). When live and ≥ 2 slots past: collapse earlier rows behind `▸ EARLIER — 04:30 TO hh:mm` toggle. **Rows with `sessions` are tappable**: tap selects that slot as the launch time for HOW FAR CAN I GO (selected row: `rgba(255,255,255,.07)` tint + 2px white inset left edge; tap again to clear; tide-only rows inert, default cursor).
- **EBB-TIDE FLAG card**: flag glyph + title + source tag; big flag chip (BLACK: near-black bg/white text/white border · GREEN/YELLOW/RED: 15 % tint + 55 % border); `Richmond 3.33 m · 09:15` + `NEXT SETTING 18:00 — TRENDING BLACK`; action sentence per flag; one-line band legend; `CROSS-CHECK AT PLA.CO.UK ›` link (green); fine-print disclaimer.
- Footer: `GRID GENERATED 17:21 · 20 JUL` (9px `#454F56`).

### 2. Month view (`MONTH`)
Header `NEXT 35 DAYS · PUTNEY`. One row per day: label (`Mon 20` — today green + `TODAY` sub, tide-only days `TIDE ONLY` sub at 60 % opacity), then a 33-segment bar (1px gaps, 7px tall) of verdict colors (dimmed tide lights beyond the wind horizon), and a caption line: `BEST 06:00–15:00 · 100%` (left) + `IDEAL 06:30–14:00` green (right). Selected day highlighted `rgba(255,255,255,.05)`; tapping a row jumps to its Day view. Footer: axis labels, `TAP A DAY FOR THE FULL PLAN`, and `FULL VERDICTS TO <date> · TIDE ONLY BEYOND…` + confidence note.

### 3. Info view (`INFO`)
Stacked `#15181B` cards: HOW TO READ IT (5-row legend with 9px square swatches), HOW FAR CAN I GO, HW GATE (AMBER ≥ 5.90 m, RED ≥ 6.30 m), TIDE LIGHT (black-flag rule), WIND LIGHT (≤ 8 green / 9–13 amber / > 13 red, gusts ≥ 32, wind-over-tide), EBB-TIDE FLAG rules, SOURCES, and NOT MODELLED (amber-bordered). Footer: generated + date-range notes.

### 4. Load-failure screen
`COULDN'T LOAD THE GRID` (Barlow Condensed 30px `#FF6B5E`) + guidance to open once online.

## Interactions & Behavior
- **Day navigation**: ‹ › buttons, ← → keyboard, and horizontal swipe (≥ 55 px, must beat 2× vertical) — clamped to grid range; disabled arrows at `#2E353A`.
- **Initial day**: today; auto-advance to tomorrow after the last slot (20:30) with banner; fall back to day 1 (+ banner) if today isn't in the grid.
- **Live mode** only when viewing today within 04:30–21:00: NOW needle/line, stats row, collapsed table, sessions "LAUNCHING NOW".
- **Slot selection**: tapping a table row with `sessions` re-aims the sessions card to that launch slot; tapping the selected row again, the header ✕, changing day (arrows/keys/swipe/month tap), or switching tabs clears back to the default (live now / first rowable slot). Sanity case (sample grid, Fri 24 Jul): 04:30 → only HAMMERSMITH (care), best turn HAMMERSMITH; 09:00 → all five turns Row, best turn KEW · 100%.
- Month row tap → that day. Day-pill tap → Month. No other animation; all state changes are instant re-renders.
- Links: green `#42E34D`, hover `#7BF08A`.

## State Management
`view` (today|month|info), `dayIdx` (null = follow clock), `selSlot` (HH:MM of the launch slot pinned in the sessions card, null = default), `earlierOpen` (table collapse), `ready/loadFail/offline`, 20 s `tick`. Derived per render: `nowMin`, `todayIdx`, live flag result. Tweakable demo props in the prototype: `demoDate` (YYYY-MM-DD), `demoTime` (HH:MM), `showNotes`.

## Design Tokens
- **Colors**: green `#42E34D` (hover `#7BF08A`, muted `#9BE3A6`), amber `#F8C81C`, red `#FF3B30` (soft `#FF6B5E`, muted `#FF8A80`), tide-curve blue `#00A8E8`, LW cyan `#4FB3C9`; text white / `#D6DDE2` / `#C3CCD2` / `#B9C2C9` / `#98A2AB` / `#6E7A83` / `#5F6B73` / `#454F56` / disabled `#2E353A`; surfaces `#0A0B0D` (base), `#101214`, `#15181B` (cards), `#24282D` (gradient top); PWA theme `#0D1B2A`; hairlines `rgba(255,255,255,.06–.18)`.
- **Type**: Barlow (400–700) for UI; Barlow Condensed (500–700) for numerals/hero. Scale: 8.5–11 px caps with 1–2.5 px letter-spacing for labels; 12–13 px body; 15–24 px condensed numerals; hero 40–64 px. Google Fonts.
- **Spacing**: 18 px page gutter; 14–16 px card padding; 7–8 px chip gaps; radii 4–6 px (chips), 8–10 px (cards), 999 px (nav pill).
- **Layout minimums**: 44 px+ touch targets on nav/tabs/rows; `env(safe-area-inset-*)` padding top/bottom.

## Assets
- `icon.png` — 180×180 app icon (also used as `apple-touch-icon`).
- `manifest.webmanifest`, `sw.js` — PWA install + offline shell (network-first for navigations and `grid_v2.json`, cache-first otherwise; CacheStorage key `tideguide-v2`).
- Wave glyph, flag glyph, wind arrows: tiny inline SVGs (see prototype markup).

## Files
- `TIDEGUIDE v2.dc.html` — the prototype: full markup + all logic (data layer, windowing, ring/chart geometry, flag math). **Start here**; a handover-manifest comment at the top of the markup documents CDN/endpoints/storage/timers/fields.
- `support.js` — prototype runtime only; do not port.
- `data/grid_v2.json` — real sample grid (20 Jul–23 Aug 2026). Sanity anchors: 20 Jul 17:30 `Don't row` → 18:00 `Caution`; 20 Jul 06:30 `best_turn: "Kew"`; first `tide_only` day 3 Aug; 15 Aug 06:00 `hw_gate: "RED"` on a tide-only day; snapshot flag BLACK.
- `TIDEGUIDE.html` — self-contained offline bundle of the same app (open directly to see everything working).
- `manifest.webmanifest`, `sw.js`, `icon.png` — deploy beside the app + `data/`.

## Known gaps
AMBER `hw_gate` styling is untested (no AMBER slot in the sample grid); gate thresholds and the "~14 days" horizon wording are hardcoded in Info copy rather than read from `hw_gate_putney_m`/`wind_horizon`.
