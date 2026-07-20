#!/usr/bin/env python3
"""
compute_v2 — Tideway row-window engine, version 2.

Layer 1 (this file, always available)
  - The calibrated v1 per-slot model, ported EXACTLY (tide light = LRC
    black-flag low-water band; wind light = SUSTAINED speed, gusts info-only;
    verdict rules unchanged) — see ../compute.py for the original.
  - Generalised dates (any day with tide + wind data on disk, not a baked week).
  - NEW: the Putney Embankment high-water gate (launching/landing blocked when
    the predicted Putney height covers the Embankment; rowing through HW afloat
    is unaffected). Threshold from putneysw15/PLA: ~6.9 m at London Bridge
    (= ~5.9 m Putney); we gate at 5.85 m Putney with the margin in MARGIN_MIN.
  - Real sunrise/sunset (NOAA approximation) instead of the July-only formula.
  - Ensemble confidence: P(sustained wind <= amber/red thresholds) from the
    40-member ICON ensemble, where available.

Layer 2 (session/reach model) plugs in via reaches.json + stream_model.json
(produced by analyze_tracks.py + fit_stream.py) — if absent, layer-1 output
still works.

Usage:
  python3 compute_v2.py --start 2026-07-20 --days 7          # build grid_v2.json
  python3 compute_v2.py --check                              # regression vs v1
"""
import argparse
import glob
import json
import math
import os
from datetime import datetime, timedelta, date

from tideway_lib import (load_listing, find_extrema, to_putney,
                         load_wind, wind_at, ang_diff,
                         HW_AMBER_M, HW_RED_M)

# ------------------------------------------------------------- configuration
# v1 calibrated values — DO NOT CHANGE without re-running the backtest.
FLOOD_TO = 250        # stream to-heading (deg) on the flood (upstream, ~WSW)
EBB_TO = 70           # stream to-heading on the ebb (downstream, ~ENE)
WIND_GREEN = 8        # sustained mph
WIND_AMBER = 13
GUST_RED = 32
WOT_MIN_SPD = 6       # wind-over-tide flagged from this sustained speed
WOT_ANGLE = 50        # deg from stream to-heading counted as opposing
STREAM_RUNNING_MIN = 45   # minutes clear of the turn = stream running

# v2 gate — Putney Embankment covering (launch/landing only): two-tier
# HW_AMBER_M / HW_RED_M imported from tideway_lib (confirmed by Rob 2026-07-20)
LAT, LNG = 51.467, -0.216

SLOT_START = 4 * 60 + 30   # 04:30
SLOT_END = 20 * 60 + 30    # 20:30
SLOT_STEP = 30


# ------------------------------------------------------------------- loading
def load_tides():
    # raw/ + ../lb_days = historical (backtest, local only);
    # data/ = the rolling live cache the scheduled pipeline maintains.
    paths = [p for p in
             glob.glob("raw/tides/lb_*.json") + glob.glob("../lb_days/lb_*.json")
             + glob.glob("data/tides/lb_*.json")
             if "fresh" not in p and "test" not in p]
    series = load_listing(paths)
    extrema = find_extrema(series)
    return to_putney(extrema)


def load_wind_series(wind_file=None):
    if wind_file:
        return load_wind(wind_file)
    series = {}
    for src in ("raw/wind/archive_putney.json", "raw/wind/recent_putney.json",
                "raw/wind/forecast_putney.json",
                "data/wind/forecast_putney.json"):   # live file loads last = wins
        if os.path.exists(src):
            for w in load_wind(src):
                series[w[0]] = w      # later files overwrite (forecast freshest)
    return sorted(series.values(), key=lambda w: w[0])


def load_ensemble():
    """[(dt, [member speeds])] or None. Live cache preferred over historical."""
    path = ("data/wind/ensemble_putney.json"
            if os.path.exists("data/wind/ensemble_putney.json")
            else "raw/wind/ensemble_putney.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        h = json.load(f)["hourly"]
    members = [k for k in h if k.startswith("wind_speed_10m")]
    out = []
    for i, t in enumerate(h["time"]):
        vals = [h[m][i] for m in members if h[m][i] is not None]
        if vals:
            out.append((datetime.strptime(t, "%Y-%m-%dT%H:%M"), vals))
    return out or None


# ------------------------------------------------------------------ sun times
def sun_times(d):
    """(sunrise, sunset) local wall-clock minutes for date d at Putney.
    NOAA-style approximation, +-3 min — plenty for a 'dark' note."""
    n = d.toordinal() - date(d.year, 1, 1).toordinal() + 1
    lat = math.radians(LAT)
    gamma = 2 * math.pi / 365 * (n - 1 + 0.5)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma)
                       - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma)
                       - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    ha = math.acos(max(-1, min(1,
        math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(decl))
        - math.tan(lat) * math.tan(decl))))
    def local(h_sign):
        utc_min = 720 - 4 * (LNG + math.degrees(ha) * h_sign) - eqtime
        # UK: BST (UTC+1) roughly Apr–Oct; good enough for a dark flag
        bst = 3 < d.month < 11
        return utc_min + (60 if bst else 0)
    return local(1), local(-1)


# ------------------------------------------------------- v1 per-slot lights
def brackets(events, dt):
    prev = nxt = None
    for e in events:
        if e[0] <= dt:
            prev = e
        elif nxt is None:
            nxt = e
            break
    return prev, nxt


def hm(mins):
    s = "-" if mins < 0 else ""
    mins = abs(int(round(mins)))
    return f"{s}{mins // 60}:{mins % 60:02d}"


def slot_row(dt, pev, wind, srise, sset):
    """One 30-min slot, v1 logic verbatim + the v2 two-tier HW gate.

    wind=None means the slot lies BEYOND the wind forecast horizon: the row
    is emitted tide-only (wind fields null, no overall verdict) so the month
    view can show tides honestly without ever pretending to know the wind.
    """
    prev, nxt = brackets(pev, dt)
    if not prev or not nxt:
        return None
    flooding = (prev[2] == "LW" and nxt[2] == "HW")
    tau = (dt - prev[0]).total_seconds() / (nxt[0] - prev[0]).total_seconds()
    h = prev[1] + (nxt[1] - prev[1]) * (1 - math.cos(math.pi * tau)) / 2

    if flooding:
        since_lw = (dt - prev[0]).total_seconds() / 60.0
        to_hw = (nxt[0] - dt).total_seconds() / 60.0
        tide = "RED" if since_lw < 120 else ("AMBER" if since_lw < 150 else "GREEN")
        status = f"Flooding (LW+{hm(since_lw)} / HW-{hm(to_hw)})"
        key_to_turn = min(since_lw, to_hw)
    else:
        since_hw = (dt - prev[0]).total_seconds() / 60.0
        to_lw = (nxt[0] - dt).total_seconds() / 60.0
        tide = "RED" if to_lw <= 60 else ("AMBER" if to_lw <= 120 else "GREEN")
        status = f"Ebbing (HW+{hm(since_hw)} / LW-{hm(to_lw)})"
        key_to_turn = min(since_hw, to_lw)
    nearest = prev if (dt - prev[0]) <= (nxt[0] - dt) else nxt
    if key_to_turn <= 25:
        status = "High water (slack)" if nearest[2] == "HW" else "Low water (slack)"

    # ---- embankment gate, two-tier (launch/landing only — not a rowing light)
    hw_gate = "RED" if h >= HW_RED_M else ("AMBER" if h >= HW_AMBER_M else None)

    if wind is None:
        # beyond the forecast horizon: tides are knowable, wind is not
        spd = gust = wdir = None
        wot = False
        windl = None
        overall = None
    else:
        spd, gust, wdir = wind_at(wind, dt)
        stream_running = key_to_turn > STREAM_RUNNING_MIN
        cur_to = FLOOD_TO if flooding else EBB_TO
        wot = stream_running and spd >= WOT_MIN_SPD and ang_diff(wdir, cur_to) <= WOT_ANGLE
        windl = "GREEN" if spd <= WIND_GREEN else ("AMBER" if spd <= WIND_AMBER else "RED")
        if gust >= GUST_RED:
            windl = "RED"
        if wot and spd >= WIND_AMBER and windl == "AMBER":
            windl = "RED"

        if tide == "RED" or windl == "RED":
            overall = "Don't row"
        elif tide == "AMBER" or windl == "AMBER":
            overall = "Caution"
        else:
            overall = "ROW"
        # gate tiers mirror the session simulator: RED blocks boating,
        # AMBER only worsens a clean verdict to Caution (wet feet, not danger)
        if hw_gate == "RED" and overall != "Don't row":
            overall = "No launch (embankment)"   # afloat is fine; boating isn't
        elif hw_gate == "AMBER" and overall == "ROW":
            overall = "Caution"

    notes = []
    if tide == "RED":
        notes.append("low water - do not boat (black-flag rule)")
    if hw_gate == "RED":
        notes.append(f"embankment covered (~{h:.1f} m Putney) - no launching/landing")
    elif hw_gate == "AMBER":
        notes.append(f"embankment may flood (~{h:.1f} m Putney) - time the launch, wet feet")
    if wot:
        notes.append("wind against tide")
    if gust is not None and gust >= 25:
        notes.append(f"gusty ({gust:.0f} mph)")
    slot_min = dt.hour * 60 + dt.minute
    if slot_min < srise:
        notes.append("before sunrise (dark)")
    if slot_min > sset:
        notes.append("after sunset (dark)")

    row = {
        "time": dt.strftime("%H:%M"),
        "height_putney_m": round(h, 2),
        "tide_status": status, "flooding": flooding,
        "wind_mph": None if spd is None else round(spd),
        "gust_mph": None if gust is None else round(gust),
        "wind_dir": None if wdir is None else int(wdir),
        "wind_over_tide": wot, "hw_gate": hw_gate,
        "tide_light": tide, "wind_light": windl, "overall": overall,
        "notes": "; ".join(notes),
    }
    if wind is None:
        row["tide_only"] = True
    return row


# ------------------------------------------------------------- confidence
def window_confidence(ens, t0, t1):
    """P(sustained <= AMBER max) and P(<= GREEN max) across [t0, t1]."""
    if not ens:
        return None
    hours = [(dt, vals) for dt, vals in ens if t0 <= dt <= t1]
    if not hours:
        return None
    n = min(len(v) for _, v in hours)
    ok13 = ok8 = 0
    for m in range(n):
        worst = max(vals[m] for _, vals in hours)
        ok13 += worst <= WIND_AMBER
        ok8 += worst <= WIND_GREEN
    return {"p_rowable": round(ok13 / n, 2), "p_calm": round(ok8 / n, 2),
            "members": n}


# ------------------------------------------------------------------- build
def load_session_layer():
    """Layer 2 (per-reach session simulator) if its inputs exist on disk."""
    if not (os.path.exists("reaches.json") and os.path.exists("stream_model.json")):
        return None
    import session_model as sm
    pev, wind = sm.load_events_and_wind()
    return {"sm": sm, "pev": pev, "wind": wind,
            "curves": sm.load_stream(), "reaches": sm.load_reach_geometry()}


def build(start, days, wind_file=None, with_sessions=True):
    pev = load_tides()
    wind = load_wind_series(wind_file)
    ens = load_ensemble()
    wind_end = wind[-1][0] if wind else None
    L2 = load_session_layer() if with_sessions else None

    grid = {}
    hwlw = {}
    for e in pev:
        hwlw.setdefault(e[0].strftime("%Y-%m-%d"), []).append(
            {"type": e[2], "time": e[0].strftime("%H:%M"), "height_m": e[1]})

    for i in range(days):
        d = start + timedelta(days=i)
        srise, sset = sun_times(d)
        rows = []
        for slot in range(SLOT_START, SLOT_END + 1, SLOT_STEP):
            dt = datetime(d.year, d.month, d.day, slot // 60, slot % 60)
            # honest horizon: beyond the wind data the slot is tide-only —
            # no wind fields, no verdict, no session simulation
            beyond_wind = wind_end is None or dt > wind_end
            row = slot_row(dt, pev, None if beyond_wind else wind, srise, sset)
            if row is None:
                continue
            if beyond_wind:
                rows.append(row)
                continue
            conf = window_confidence(ens, dt, dt + timedelta(minutes=90))
            if conf:
                row["confidence_90min"] = conf
            if L2:
                sm = L2["sm"]
                sessions = {}
                best = None
                for turn_c, name in sm.TURN_MENU:
                    sim = sm.simulate(L2["pev"], L2["wind"], L2["curves"],
                                      L2["reaches"], dt, turn_chain=turn_c)
                    sessions[name] = {
                        "verdict": sim["verdict"],
                        "duration_min": sim["duration_min"],
                        "worst_light": sim["worst"]["light"],
                        "worst_reach": sim["worst"]["reach"],
                    }
                    if sim["verdict"] != "Don't row" and sim["duration_min"] <= 135:
                        best = name
                row["sessions"] = sessions
                row["best_turn"] = best
            rows.append(row)

        if L2 and rows:
            # first slot (after the day's first rowable one) where the ULBC
            # session verdict worsens = "conditions deteriorate from HH:MM"
            rank = {"Row": 0, "Row (care)": 1, "Don't row": 2}
            seen_rowable = False
            prev = None
            for r in rows:
                v = rank.get(r.get("sessions", {}).get("ULBC", {})
                             .get("verdict"), None)
                if v is None:
                    continue
                if v < 2:
                    seen_rowable = True
                if seen_rowable and prev is not None and v > prev:
                    for rr in rows:
                        rr.setdefault("day_deteriorates_at", r["time"])
                    break
                prev = v
        grid[d.isoformat()] = rows

    # flag snapshot (fetch_flag.py) — offline fallback for the PWA's live panel
    flag = None
    if os.path.exists("data/flag.json"):
        with open("data/flag.json") as f:
            flag = json.load(f)

    return {"generated": datetime.now().isoformat(timespec="seconds"),
            "model": "v2",
            "hw_gate_putney_m": {"amber": HW_AMBER_M, "red": HW_RED_M},
            "wind_horizon": wind_end.isoformat(timespec="minutes")
                            if wind_end else None,
            "flag": flag,
            "grid": grid, "putney_hwlw": hwlw}


# ---------------------------------------------------------------- regression
def check():
    """v1 regression: tide light must match ../grid.json exactly on the
    overlap week; wind light must match when fed v1's own wind.json."""
    with open("../grid.json") as f:
        v1 = json.load(f)["grid"]
    out = build(date(2026, 7, 15), 7, wind_file="../wind.json",
                with_sessions=False)
    v2 = out["grid"]
    tide_diffs = wind_diffs = slots = 0
    for day, rows1 in v1.items():
        rows2 = {r["time"]: r for r in v2.get(day, [])}
        for r1 in rows1:
            r2 = rows2.get(r1["time"])
            if not r2:
                continue
            slots += 1
            if r1["tide_light"] != r2["tide_light"]:
                tide_diffs += 1
                print(f"TIDE DIFF {day} {r1['time']}: "
                      f"v1={r1['tide_light']} v2={r2['tide_light']}")
            if r1["wind_light"] != r2["wind_light"]:
                wind_diffs += 1
                print(f"WIND DIFF {day} {r1['time']}: "
                      f"v1={r1['wind_light']} v2={r2['wind_light']}")
    print(f"regression: {slots} slots compared, "
          f"{tide_diffs} tide diffs, {wind_diffs} wind diffs")
    return tide_diffs == 0 and wind_diffs == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--out", type=str, default="grid_v2.json")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        ok = check()
        raise SystemExit(0 if ok else 1)
    start = date.fromisoformat(a.start) if a.start else date.today()
    out = build(start, a.days)
    with open(a.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))   # minified — jq to inspect
    ndays = len([k for k, v in out["grid"].items() if v])
    tide_only = sum(1 for v in out["grid"].values() for r in v
                    if r.get("tide_only"))
    print(f"{a.out} written: {ndays} days from {start}, "
          f"{tide_only} tide-only slots beyond the wind horizon")
