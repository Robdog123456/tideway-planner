#!/usr/bin/env python3
"""
session_model — layer 2 of the v2 engine: simulate a whole out-and-back
session through space and time and score it per reach.

The upgrade over the v1 single-point model:
  - the boat MOVES: launch at t0, ride/fight the fitted stream up the corridor,
    turn, come back — conditions are evaluated where the boat actually is,
    when it is there;
  - wind is judged against each REACH's own axis (the river winds ~120 deg
    between Putney and Kew), so "wind-over-tide" fires only in reaches where
    the wind truly opposes the stream — v1 used one global axis;
  - the Putney Embankment gate applies to launch and landing only, two-tier
    (AMBER: road may flood, wet feet; RED: above anything Rob has boated).

Calibration floors (do not regress):
  - v1 sustained-wind thresholds unchanged (8/13, gusts >= 32 RED);
  - 15 Jul 2026 06:30-07:45 anchor must score rowable (it does: NE wind
    opposes the ebb only in the E-W middle reaches at 9.6 mph -> AMBER);
  - HW gate RED at 6.3 m Putney (empirical max boated: 6.25 m, gate_check.csv).

CLI:  python3 session_model.py --backtest     # replay all kept sessions
"""
import bisect
import csv
import glob
import json
import math
from datetime import datetime, timedelta

from tideway_lib import (load_listing, find_extrema, to_putney,
                         load_wind, wind_at, ang_diff, bearing_deg)


def fast_wind_at(series_and_index, dt):
    """wind_at with bisect (the grid build makes ~100k lookups)."""
    series, times = series_and_index
    i = bisect.bisect_right(times, dt)
    if i <= 0:
        w = series[0]
        return w[1], w[2], w[3]
    if i >= len(series):
        w = series[-1]
        return w[1], w[2], w[3]
    t0, s0, g0, d0 = series[i - 1]
    t1, s1, g1, d1 = series[i]
    f = (dt - t0).total_seconds() / (t1 - t0).total_seconds()
    return s0 + (s1 - s0) * f, g0 + (g1 - g0) * f, (d0 if f < 0.5 else d1)

# tuned/empirical parameters (see backtest-report.md)
V_ROB = 3.3               # m/s through-the-water default (median session V)
HW_AMBER_M = 5.90         # Putney: parts of the road may flood
HW_RED_M = 6.30           # Putney: above max Rob has ever boated (6.25 m)
WIND_GREEN, WIND_AMBER, GUST_RED = 8, 13, 32
WOT_MIN_SPD, WOT_ANGLE = 6, 50
STREAM_MIN_MPS = 0.15     # stream counted as "running" for WoT
TURN_MENU = [(3010, "Hammersmith"), (4633, "Corney"), (6200, "ULBC"),
             (7390, "Chiswick Br"), (9085, "Kew")]
STEP_S = 120              # simulation step


# ------------------------------------------------------------------ loading
def load_reach_geometry():
    with open("reaches.json") as f:
        d = json.load(f)
    line, chain = d["centreline"], d["chainage"]

    def point_at(c):
        i = min(range(len(chain)), key=lambda k: abs(chain[k] - c))
        return line[i]

    reaches = []
    for r in d["reaches"]:
        if r["to_m"] - r["from_m"] < 200:
            continue                       # zero-length tail (richmond)
        p0, p1 = point_at(r["from_m"]), point_at(r["to_m"])
        reaches.append({**r, "bearing_up": bearing_deg(p0, p1),
                        "bearing_down": bearing_deg(p1, p0)})
    return reaches


def load_stream():
    with open("stream_model.json") as f:
        d = json.load(f)["model"]
    # reach -> sorted [(phase_centre, s)]
    curves = {}
    for reach, bins in d.items():
        pts = sorted((float(k), v["s_mps"]) for k, v in bins.items()
                     if v["n"] >= 2)      # ignore single-pass bins (noisy)
        if len(pts) >= 4:
            curves[reach] = pts
    # corridor mean as fallback for sparse reaches
    allpts = {}
    for pts in curves.values():
        for p, s in pts:
            allpts.setdefault(p, []).append(s)
    curves["_mean"] = sorted((p, sum(v) / len(v)) for p, v in allpts.items())
    return curves


def stream_at(curves, reach, phase):
    pts = curves.get(reach) or curves["_mean"]
    if phase <= pts[0][0]:
        return pts[0][1]
    if phase >= pts[-1][0]:
        return pts[-1][1]
    for (p0, s0), (p1, s1) in zip(pts, pts[1:]):
        if p0 <= phase <= p1:
            f = (phase - p0) / (p1 - p0)
            return s0 + f * (s1 - s0)
    return 0.0


def load_events_and_wind():
    paths = [p for p in
             glob.glob("raw/tides/lb_*.json") + glob.glob("../lb_days/lb_*.json")
             if "fresh" not in p and "test" not in p]
    pev = to_putney(find_extrema(load_listing(paths)))
    wind = {}
    for pt in ("putney", "barnes"):
        series = {}
        for src in (f"raw/wind/archive_{pt}.json", f"raw/wind/recent_{pt}.json",
                    f"raw/wind/forecast_{pt}.json"):
            try:
                for w in load_wind(src):
                    series[w[0]] = w
            except FileNotFoundError:
                pass
        s = sorted(series.values(), key=lambda w: w[0])
        wind[pt] = (s, [w[0] for w in s])       # (series, time index)
    return pev, wind


# ------------------------------------------------------------------ helpers
def putney_height(pev, dt):
    prev = nxt = None
    for e in pev:
        if e[0] <= dt:
            prev = e
        elif nxt is None:
            nxt = e
            break
    if not prev or not nxt:
        return None
    tau = (dt - prev[0]).total_seconds() / (nxt[0] - prev[0]).total_seconds()
    return prev[1] + (nxt[1] - prev[1]) * (1 - math.cos(math.pi * tau)) / 2


def phase_vs_hw(pev, dt):
    best = None
    for edt, h, typ in pev:
        if typ != "HW":
            continue
        d = (dt - edt).total_seconds() / 3600.0
        if best is None or abs(d) < abs(best):
            best = d
    return best


def tide_light_at(pev, dt):
    """v1 low-water band, unchanged (club black-flag policy)."""
    prev = nxt = None
    for e in pev:
        if e[0] <= dt:
            prev = e
        elif nxt is None:
            nxt = e
            break
    if not prev or not nxt:
        return None
    if prev[2] == "LW":
        since_lw = (dt - prev[0]).total_seconds() / 60
        return "RED" if since_lw < 120 else ("AMBER" if since_lw < 150 else "GREEN")
    to_lw = (nxt[0] - dt).total_seconds() / 60
    return "RED" if to_lw <= 60 else ("AMBER" if to_lw <= 120 else "GREEN")


LIGHT_RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}


# ----------------------------------------------------------------- simulate
def simulate(pev, wind, curves, reaches, t0, turn_chain=None, duration_s=None,
             v_rob=V_ROB):
    """Integrate the out-and-back. Returns dict with samples + worst light."""
    def reach_of(c):
        for r in reaches:
            if r["from_m"] <= c <= r["to_m"]:
                return r
        return reaches[-1] if c > reaches[-1]["to_m"] else reaches[0]

    t, c, direction = t0, 0.0, +1
    samples = []
    worst = ("GREEN", None, None)
    while True:
        el = (t - t0).total_seconds()
        if el > 3.5 * 3600:
            break
        r = reach_of(c)
        ph = phase_vs_hw(pev, t)
        s = stream_at(curves, r["name"], ph) if ph is not None else 0.0
        sog = max(0.8, v_rob + direction * s)

        wpt = "putney" if c < 3500 else "barnes"
        spd, gust, wdir = (fast_wind_at(wind[wpt], t)
                           if wind[wpt][0] else (0, 0, 0))
        # stream to-heading: + = upstream (bearing_up), - = downstream
        stream_to = r["bearing_up"] if s >= 0 else r["bearing_down"]
        wot = (abs(s) >= STREAM_MIN_MPS and spd >= WOT_MIN_SPD
               and ang_diff(wdir, stream_to) <= WOT_ANGLE)
        light = ("GREEN" if spd <= WIND_GREEN
                 else "AMBER" if spd <= WIND_AMBER else "RED")
        if gust >= GUST_RED:
            light = "RED"
        if wot and spd >= WIND_AMBER and light == "AMBER":
            light = "RED"
        samples.append({"t": t.strftime("%H:%M"), "chain_m": round(c),
                        "reach": r["name"], "dir": "up" if direction > 0 else "down",
                        "stream_mps": round(s, 2), "wind_mph": round(spd, 1),
                        "wind_dir": round(wdir), "wot": wot, "light": light})
        if LIGHT_RANK[light] > LIGHT_RANK[worst[0]]:
            worst = (light, r["name"], t.strftime("%H:%M"))

        # advance
        c += direction * sog * STEP_S
        t += timedelta(seconds=STEP_S)
        if direction > 0:
            hit_turn = (turn_chain is not None and c >= turn_chain) or \
                       (duration_s is not None and el >= duration_s / 2)
            if hit_turn:
                direction = -1
        elif c <= 0:
            break

    dur_min = (t - t0).total_seconds() / 60
    max_chain = max(s["chain_m"] for s in samples) if samples else 0

    h0 = putney_height(pev, t0)
    h1 = putney_height(pev, t)
    tl0, tl1 = tide_light_at(pev, t0), tide_light_at(pev, t)

    def hw_tier(h):
        if h is None:
            return None
        return "RED" if h >= HW_RED_M else ("AMBER" if h >= HW_AMBER_M else "GREEN")

    gates = {"lw_launch": tl0, "lw_land": tl1,
             "hw_launch": hw_tier(h0), "hw_land": hw_tier(h1),
             "h_launch_m": round(h0, 2) if h0 else None,
             "h_land_m": round(h1, 2) if h1 else None}

    gate_lights = [g for g in (tl0, tl1, gates["hw_launch"], gates["hw_land"]) if g]
    overall_rank = max([LIGHT_RANK[worst[0]]] +
                       [LIGHT_RANK[g] for g in gate_lights])
    verdict = ["Row", "Row (care)", "Don't row"][overall_rank]
    return {"launch": t0.strftime("%H:%M"), "duration_min": round(dur_min),
            "max_chain_m": max_chain, "verdict": verdict,
            "worst": {"light": worst[0], "reach": worst[1], "at": worst[2]},
            "gates": gates, "samples": samples}


# ----------------------------------------------------------------- backtest
def backtest():
    pev, wind = load_events_and_wind()
    curves = load_stream()
    reaches = load_reach_geometry()

    print(f"{'date':<11}{'launch':<7}{'turn_m':>7} {'verdict':<12}"
          f"{'worst(reach@t)':<24}{'gate flags'}")
    results = []
    with open("raw/strava/manifest.csv") as f:
        kept = [r for r in csv.DictReader(f) if r["status"] == "kept"]
    for r in kept:
        t0 = datetime.fromisoformat(r["start_local"])
        dur = float(r["elapsed_s"])
        sim = simulate(pev, wind, curves, reaches, t0, duration_s=dur)
        g = sim["gates"]
        flags = []
        if g["lw_launch"] == "RED" or g["lw_land"] == "RED":
            flags.append("LW-band(policy)")
        if g["hw_launch"] == "RED" or g["hw_land"] == "RED":
            flags.append("HW-RED")
        if g["hw_launch"] == "AMBER" or g["hw_land"] == "AMBER":
            flags.append("HW-amber")
        if sim["worst"]["light"] == "RED":
            flags.append("wind-RED")
        results.append((r, sim, flags))
        w = sim["worst"]
        print(f"{r['date']:<11}{sim['launch']:<7}{sim['max_chain_m']:>7} "
              f"{sim['verdict']:<12}"
              f"{w['light']}({w['reach']}@{w['at']})".ljust(24) +
              f"{','.join(flags) or '-'}")

    n = len(results)
    ok = sum(1 for _, s, f in results
             if s["verdict"] != "Don't row" or f == ["LW-band(policy)"])
    windred = sum(1 for _, s, _ in results if s["worst"]["light"] == "RED")
    lwpol = sum(1 for _, _, f in results if "LW-band(policy)" in f)
    hwred = sum(1 for _, _, f in results if "HW-RED" in f)
    print(f"\n{n} sessions replayed: {ok} rowable-or-policy-only, "
          f"{windred} wind-RED, {lwpol} LW-band policy conflicts, {hwred} HW-RED")

    # the 15 Jul anchor (no GPS upload — synthetic replay)
    anchor = simulate(pev, wind, curves, reaches,
                      datetime(2026, 7, 15, 6, 30), duration_s=75 * 60)
    print(f"\nANCHOR 15 Jul 06:30 75min: verdict={anchor['verdict']} "
          f"worst={anchor['worst']}")
    return results, anchor


if __name__ == "__main__":
    backtest()
