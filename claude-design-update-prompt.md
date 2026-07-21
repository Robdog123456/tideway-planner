# Claude Design prompt — upgrade TIDEGUIDE to the live v2 pipeline
# Usage: open the SAME Claude Design chat/project that built TIDEGUIDE.html
# (or a fresh one — then ALSO attach the current TIDEGUIDE.html so it can see
# the design). ATTACH the current grid_v2.json (from tideway-planner/web/data/).
# Paste everything below the line, send.
# When it's done: download the single HTML file and give it back to Claude Code —
# it gets dropped into the PWA wrapper (service worker, manifest, icons are
# already built) and deployed. Don't hand-edit the wrapper tags out.

---

You built TIDEGUIDE for me — the dark ring-hero rowing app I use on my iPhone. I
love the design. **Keep it exactly as it is: same ring, same tabs, same table,
same colors, same voice.** This is NOT a redesign. One thing changes and a few
things are added:

**THE BIG CHANGE — the data is live now.** The baked-in week is gone. The app
now sits on GitHub Pages next to a JSON file that a pipeline rebuilds every 6
hours from a calibrated per-reach model (v2 — it simulates my boat through six
reaches of the corridor). At runtime the app must:

1. `fetch("data/grid_v2.json", {cache:"no-store"})` on load (same-origin,
   relative path — it's hosted in the same folder).
2. Save the last good copy to `localStorage`; if the fetch fails (boathouse
   basement, airplane mode), render from the saved copy and show a small
   "OFFLINE — showing saved grid" banner in the existing banner style.
3. If `generated` is older than 13 hours, show a banner: "GRID IS {N}H OLD —
   pipeline may be failing". (Two missed 6-hour runs = something's wrong.)
4. "Now" still comes from the phone clock. Times in the file are London
   wall-clock strings — parse them as local time, no timezone maths.

**THE NEW DATA CONTRACT (grid_v2.json — the attached file is a real one)**

Top level:
- `generated` — ISO stamp of the build (for the staleness banner + a small
  "grid generated 17:21 · 20 Jul" footer note).
- `wind_horizon` — ISO time where the wind forecast ends (~14 days out).
- `hw_gate_putney_m` — `{"amber": 5.9, "red": 6.3}` (see gate below).
- `putney_hwlw` — per date: `[{type:"HW"|"LW", time:"08:20", height_m:5.8}]` —
  this is what the HW/LW cards and the tide curve should be drawn from.
- `flag` — a snapshot of the ebb-tide flag (see FLAG below), may be null.
- `grid` — per date ("2026-07-20"), an array of 30-min slots 04:30→20:30.

Each slot (wind-known days — the first ~14):
- `time` "17:30", `height_putney_m` 2.2, `tide_status` "Ebbing (HW+…/LW−…)",
  `flooding` true/false
- `wind_mph` 6, `gust_mph` 16, `wind_dir` 210 (degrees FROM),
  `wind_over_tide` bool
- `tide_light` / `wind_light` — "GREEN"|"AMBER"|"RED" (same two-light model
  as before, unchanged meaning)
- `overall` — EXACT strings: `"ROW"`, `"Caution"`, `"Don't row"`,
  `"No launch (embankment)"` — map them to the existing verdict displays
  (Caution = the old "Row (care)").
- `notes` — semicolon-joined, e.g. "low water - do not boat (black-flag
  rule); before sunrise (dark)"
- `hw_gate` — null | "AMBER" | "RED". NEW, two tiers, launch/landing only:
  AMBER ≥5.90 m Putney = parts of the Embankment road may flood, time the
  launch, wet feet. RED ≥6.30 m = above anything I've ever boated — the
  verdict comes through as "No launch (embankment)". Being afloat through
  HW is fine — say so, don't paint it like a storm.
- `confidence_90min` — `{p_rowable:0.85, p_calm:0.42, members:40}` where the
  40-member ensemble reaches (≤7 days). Show p_rowable as a small confidence
  badge on windows/days — beyond it, no badge, no pretending.
- `sessions` — NEW, the v2 model. Per turn-point, the verdict for a whole
  out-and-back session LAUNCHING AT THIS SLOT:
  `{"Hammersmith":{verdict:"Row"|"Row (care)"|"Don't row", duration_min:36,
  worst_light:"GREEN"|"AMBER"|"RED", worst_reach:"putney"|…}, "Corney":…,
  "ULBC":…, "Chiswick Br":…, "Kew":…}` plus `best_turn` — the furthest turn
  still rowable in ≤135 min (null if none). THIS ANSWERS "how far can I go
  right now" — give it a proper panel on the day view ("HOW FAR CAN I GO"),
  chips per turn in verdict colors, best turn highlighted, worst reach named.
- `day_deteriorates_at` — "12:30" when the day worsens later: surface it in
  the hero status ("deteriorates from 12:30").

Slots BEYOND the wind horizon (days ~15–35) instead carry:
- `tide_only: true`, wind fields null, `overall` null — tides are knowable a
  month out, wind is not, and the app must never fake a verdict there. Render
  these dimmed, tide-light colors only, labelled "TIDE ONLY".

**WEEK → MONTH.** The grid now covers ~35 days. The week view becomes a MONTH
view in the same visual language (day rows + colored strips): first ~14 days
full verdict colors, the rest dimmed tide-only with their green-tide windows
listed. Confidence badges only where `confidence_90min` exists.

**THE FLAG — new panel.** The PLA ebb-tide flag is computed, not judged: at
06:00/18:00 the LOWEST Richmond reading of the preceding 12 h sets it
(≥2.6 m RED · 1.7–2.6 YELLOW · 0–1.7 GREEN · <0 BLACK). The grid's `flag`
block is a pipeline snapshot: `{fetched, latest:{time, level_m},
current:{flag, set_at, min_12h_m}, next:{flag_estimate, at, trailing_min_m}}`.
ALSO fetch it live when online (CORS is open):
`https://environment.data.gov.uk/flood-monitoring/id/stations/0009/readings?parameter=level&_sorted&since={ISO 36h ago}`
→ items[{dateTime, value}] in metres — compute the same bands client-side and
prefer the live result, falling back to the snapshot (label which one is
shown). Panel shows: current flag chip, latest level + time, "next setting
18:00 — trending GREEN", and a link to pla.co.uk/ebb-tide-flag-warning to
cross-check. Actions line under it: BLACK = don't boat at low tide · RED =
CRSA clearance, low flood only · GREEN = caution/good lookout.
The flag is display-only context — it never changes a slot verdict.

**"WHAT MUST I DO" — small static panel, LRC rules:** check the flag on the
day · outing log before boating · phone in the pouch · four white lights after
dark. I row unaccompanied as a Tideway Expert sculler on my own risk
assessment — the app informs, it never authorises.

**KEEP (non-negotiable):**
- The design. Ring hero, 33 arcs, verdict headline, stat pair, HW/LW cards,
  tide curve, windows chips, slot table, tabs, all of it.
- One single self-contained HTML file.
- These wrapper tags in <head> exactly (the PWA shell around you is already
  built — service worker, manifest, icons):
  `<link rel="manifest" href="manifest.webmanifest">`
  `<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">`
  the apple-mobile-web-app meta tags, `viewport-fit=cover`, theme-color
  `#0D1B2A`, and before `</body>`:
  `<script>if("serviceWorker" in navigator)navigator.serviceWorker.register("sw.js");</script>`
- If you load ANY external library (React CDN etc.), list every external URL
  in an HTML comment at the very top of the file (`<!-- CDN: … -->`) so the
  service worker can pre-cache them for offline. Fewer is better; none is best.

**SANITY CHECKS (against the attached grid_v2.json — today is 2026-07-20):**
- 20 Jul, 17:30 slot: "Don't row" (low-water band; LW 15:38), rowable again
  at 18:00 — the hero should say so.
- 20 Jul sessions at 06:30: everything rowable, best_turn "Kew".
- 15 Aug 06:00: height 6.37 m → `hw_gate:"RED"`, overall "No launch
  (embankment)" — check the gate renders.
- Any day after ~2 Aug: `tide_only` slots — dimmed, no verdicts, no wind.
- The flag block will likely read BLACK (dry July, very low LWs) — that's
  correct, not a bug: BLACK = don't boat AT LOW TIDE.

Don't rebuild the model in JavaScript. The JSON is the single source of truth
for every light and verdict; the app renders it.

---
---

# THE HANDOVER — paste this as the FINAL message once the design is settled
# (Claude Code integrates the file into an existing PWA wrapper — service
# worker, manifest, icons, GitHub Pages deploy — and runs acceptance tests
# on it. The handover below is what makes that possible without guesswork.)

---

The design is settled. Now hand it over. **Everything goes in ONE artifact —
the HTML file itself** — so nothing is lost when I download it. Deliver:

**1. The file.** A single, complete, self-contained HTML document
(`<!doctype html>` … `</html>`), production state: no debug logging, no
commented-out experiments, no TODO stubs. It will be saved as `index.html`
and served from the same folder as `data/grid_v2.json`.

**2. The manifest comment — the FIRST thing in the file, before <html>:**

```html
<!--
TIDEGUIDE v2 — HANDOVER MANIFEST
CDN: <every external URL the page loads at runtime — scripts, styles,
     fonts — one per line; or the single word "none">
ENDPOINTS: <every URL fetched at runtime; must be exactly
     "data/grid_v2.json" and the EA readings URL — anything else, justify it>
STORAGE: <every localStorage/sessionStorage key you read or write>
     (the wrapper already uses "tideway-grid" — either reuse it exactly for
     the saved grid, or pick keys that don't collide)
TIMERS: <every setInterval/polling loop and its cadence>
FIELDS READ: <every grid_v2.json field the app consumes, dot-notation,
     one line — this is how schema drift gets caught before it bites>
FIELDS IGNORED: <fields present in the attached grid you chose not to use>
CHANGELOG: <what changed vs the original TIDEGUIDE — new panels, renamed
     tabs, new states, removed features — terse list>
LIMITATIONS: <anything from the brief you did not implement, and why>
-->
```

**3. Self-test results — appended to that same comment block:** run the five
sanity checks from the brief against the attached grid_v2.json and report
each as PASS/FAIL with one line of evidence:
- 20 Jul 17:30 → "Don't row", hero says rowable 18:00
- 20 Jul 06:30 sessions → all rowable, best_turn Kew
- 15 Aug 06:00 → hw_gate RED, "No launch (embankment)" renders
- any post-2-Aug day → tide-only display (dimmed, no verdicts, no wind)
- flag panel: renders the snapshot when the EA fetch is unavailable, and
  labels which source is showing

**4. Wrapper compliance (I will verify these mechanically before deploy):**
- the four wrapper elements present verbatim: manifest link, apple-touch-icon
  link, apple-mobile-web-app meta tags, sw.js registration before `</body>`
- fetch of the grid is RELATIVE (`data/grid_v2.json`) with
  `{cache:"no-store"}` — no absolute domains, no hardcoded localhost
- a failed grid fetch falls back to the saved copy (banner: offline), a
  missing saved copy shows a clear first-load-needs-connection message —
  the app must never render a blank page
- times parsed as LOCAL wall-clock (no `Date.parse` of bare "HH:MM" through
  UTC, no timezone libraries)
- no console errors on load, online or offline

**My acceptance tests after integration** (fail = it comes back to you):
load online → verdicts match the JSON; kill the network → reload renders
from the saved grid with the offline banner; stale `generated` → staleness
banner; the five sanity checks above re-run against the live file; network
tab shows no requests beyond the declared ENDPOINTS + CDN list.
