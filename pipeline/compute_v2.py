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
                         load_wind, wind_at, ang_diff)

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

# NEW v2 gate — Putney Embankment covering (launch/landing only)
HW_GATE_PUTNEY_M = 5.85   # predicted Putney height (m CD) that covers the hard
LAT, LNG = 51.467, -0.216

SLOT_START = 4 * 60 + 30   # 04:30
SLOT_END = 20 * 60 + 30    # 20:30
SLOT_STEP = 30


# ------------------------------------------------------------------- loading
def load_tides():
    paths = [p for p in
             glob.glob("raw/tides/lb_*.json") + glob.glob("../lb_days/lb_*.json")
             if "fresh" not in p and "test" not in p]
    series = load_listing(paths)
    extrema = find_extrema(series)
    return to_putney(extrema)


def load_wind_series(wind_file=None):
    if wind_file:
        return load_wind(wind_file)
    series = {}
    for src in ("raw/wind/archive_putney.json", "raw/wind/recent_putney.json",
                "raw/wind/forecast_putney.json"):
        if os.path.exists(src):
            for w in load_wind(src):
                series[w[0]] = w      # later files overwrite (forecast freshest)
    return sorted(series.values(), key=lambda w: w[0])


def load_ensemble():
    """[(dt, [member speeds])] or None."""
    path = "raw/wind/ensemble_putney.json"
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
    """One 30-min slot, v1 logic verbatim + the v2 HW gate fields."""
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

    spd, gust, wdir = wind_at(wind, dt)
    stream_running = key_to_turn > STREAM_RUNNING_MIN
    cur_to = FLOOD_TO if flooding else EBB_TO
    wot = stream_running and spd >= WOT_MIN_SPD and ang_diff(wdir, cur_to) <= WOT_ANGLE
    windl = "GREEN" if spd <= WIND_GREEN else ("AMBER" if spd <= WIND_AMBER else "RED")
    if gust >= GUST_RED:
        windl = "RED"
    if wot and spd >= WIND_AMBER and windl == "AMBER":
        windl = "RED"

    # ---- NEW: embankment gate (launch/landing only — not a rowing light)
    hw_gate = h >= HW_GATE_PUTNEY_M

    if tide == "RED" or windl == "RED":
        overall = "Don't row"
    elif tide == "AMBER" or windl == "AMBER":
        overall = "Caution"
    else:
        overall = "ROW"
    if hw_gate and overall != "Don't row":
        overall = "No launch (embankment)"   # afloat is fine; boating isn't

    notes = []
    if tide == "RED":
        notes.append("low water - do not boat (black-flag rule)")
    if hw_gate:
        notes.append(f"embankment covered (~{h:.1f} m Putney) - no launching/landing")
    if wot:
        notes.append("wind against tide")
    if gust >= 25:
        notes.append(f"gusty ({gust:.0f} mph)")
    slot_min = dt.hour * 60 + dt.minute
    if slot_min < srise:
        notes.append("before sunrise (dark)")
    if slot_min > sset:
        notes.append("after sunset (dark)")

    return {
        "time": dt.strftime("%H:%M"),
        "height_putney_m": round(h, 2),
        "tide_status": status, "flooding": flooding,
        "wind_mph": round(spd), "gust_mph": round(gust),
        "wind_dir": int(wdir),
        "wind_over_tide": wot, "hw_gate": hw_gate,
        "tide_light": tide, "wind_light": windl, "overall": overall,
        "notes": "; ".join(notes),
    }


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
            if wind_end and dt > wind_end:
                break   # honest horizon: tide-only beyond wind data
            row = slot_row(dt, pev, wind, srise, sset)
            if row is None:
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

    return {"generated": datetime.now().isoformat(timespec="seconds"),
            "model": "v2-layer1",
            "hw_gate_putney_m": HW_GATE_PUTNEY_M,
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
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        ok = check()
        raise SystemExit(0 if ok else 1)
    start = date.fromisoformat(a.start) if a.start else date.today()
    out = build(start, a.days)
    with open("grid_v2.json", "w") as f:
        json.dump(out, f, indent=1)
    ndays = len([k for k, v in out["grid"].items() if v])
    print(f"grid_v2.json written: {ndays} days with data from {start}")
