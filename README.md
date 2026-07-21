# Tideway row-window planner

**Can I row now — and how far can I go?** A self-updating planner for sculling
out of Putney: tide + wind traffic lights, per-turn session verdicts from a
calibrated per-reach model, the computed PLA ebb-tide flag, and the live
Richmond level — on an iPhone home screen, correct unattended for a month+.

> The model is personal (calibrated on my own GPS + my club's rules).
> **Don't use it to make your own boating decisions.**

## How it works — the whole picture

```
every 6 h (GitHub Actions, build.yml)
  fetch_tides.py --live   PLA London Bridge minute predictions, rolling 35 d
  fetch_wind.py  --live   Open-Meteo 14 d forecast + 40-member ICON ensemble
  fetch_flag.py           EA Richmond gauge → computed ebb-tide flag snapshot
  compute_v2.py --check   GATE: calibrated v1 lights must reproduce exactly
  compute_v2.py --days 35 → web/data/grid_v2.json  (35 d, minified)
  sanity check → commit data → deploy web/ to GitHub Pages

on the phone (web/, a plain-JS PWA — no build step, no framework)
  grid_v2.json            the verdicts (authority: the Python model, never JS)
  EA API (live, CORS)     current Richmond level + flag, refreshed client-side
  service worker          app shell + last good grid work offline
```

Two honesty rules are load-bearing:

- **Tides are knowable a month out; wind is not.** Days beyond the ~14 d wind
  horizon carry `tide_only` slots — no wind fields, no verdict. The month view
  never pretends to know the wind in four weeks.
- **Verdict authority stays in Python.** The live EA/Open-Meteo data the phone
  fetches is display-only context; it never re-scores a slot.

## The model (frozen, calibration-gated)

- v1 core (tide light = LRC black-flag LW band; wind light = SUSTAINED mph
  8/13, gusts info-only under 32) — preserved exactly, `--check` proves it
  on every run.
- v2 session layer (`session_model.py`): the boat is simulated through six
  reaches (axes 338°→231°); wind-over-tide fires per reach; stream fitted
  from GPS. Backtest: 29/29 real sessions rowable-or-policy-only.
- Embankment gate, two-tier at Putney (launch/landing only): AMBER ≥ 5.90 m
  (road may flood, wet feet), RED ≥ 6.30 m (above max ever boated). Shared
  constant in `tideway_lib.py`.
- The LW band is **club-rule policy**, not physics — the app says so.
- The ebb flag is **computed, not judged**: at 06:00/18:00 the lowest
  Richmond reading of the preceding 12 h sets it
  (≥2.6 RED · 1.7–2.6 YELLOW · 0–1.7 GREEN · <0 BLACK).

Full method + evidence: the backtest report in the rowing-coach vault
(`29_water-weather-environment/`, report dated 2026-07-20).

## Running it yourself (local)

```bash
cd pipeline
python3 fetch_tides.py --live      # 35 d of PLA minutes → data/tides/
python3 fetch_wind.py  --live      # forecast + ensemble → data/wind/
python3 fetch_flag.py              # EA snapshot → data/flag.json
python3 compute_v2.py --days 35 --out ../web/data/grid_v2.json
cd ../web && python3 -m http.server 8000   # → http://localhost:8000
```

Everything is Python 3 stdlib (≥3.9 for `zoneinfo`). No pip installs.

## The gates (run before pushing ANY model change)

```bash
scripts/backtest_local.sh
```

That is: `compute_v2.py --check` (v1 regression — must be 0 diffs on 231
slots) then `session_model.py --backtest` (29/29 rowable-or-policy-only,
0 wind-RED, 0 HW-RED, and the 15 Jul 2026 06:30 anchor must stay
"Row (care), worst AMBER @ putney"). The backtest needs `local-data/`
(raw GPS — deliberately NOT in this public repo), so it only runs on my Mac.
CI runs the `--check` half on every scheduled build.

## When something breaks (the unattended-month runbook)

| Symptom | Meaning | What to do |
|---|---|---|
| Email: "build-grid workflow failed" | A fetch or gate failed. The site keeps serving the **last good grid** — nothing is broken for the user. | Open the Actions tab → the failed run → read which step went red. One-off (API blip): ignore, next run self-heals. Persistent: see below. |
| Tides step failing repeatedly | PLA endpoint changed or blocked the runner | The app stays correct ~28 more days on cached tides. Fix the fetcher locally, run the gates, push. |
| Wind step failing repeatedly | Open-Meteo outage/URL change | Grid keeps publishing? No — wind failure fails the run by design; last grid serves. Wind older than ~13 h shows the staleness banner in-app. |
| App shows "grid is N h old" banner | Two+ runs missed | Check the Actions tab; run **Run workflow** by hand (workflow_dispatch). |
| Flag panel says "snapshot" | Phone can't reach the EA API | Fine — it falls back to the pipeline's snapshot automatically. |
| Schedule silently stopped | GitHub disables schedules after 60 d of no repo activity | Shouldn't happen (every run commits data), but the fix is one manual Run workflow. |

## Repo map

```
pipeline/    the model + fetchers (Python, stdlib). tideway_lib.py = shared core
  data/      rolling caches the pipeline commits (tides, flag)
web/         the PWA (index.html, app.js, styles.css, sw.js, manifest, icons)
lb_days/     July 2026 reference tides — the --check regression window. Don't delete.
grid.json, wind.json, compute.py   frozen v1 reference for --check. Don't edit.
build_xlsx.py  on-demand Excel workbook (needs openpyxl; not in the pipeline)
scripts/     backtest_local.sh — the pre-push gate
local-data/  (gitignored) raw GPS + historical pulls; lives only on my Mac
```
