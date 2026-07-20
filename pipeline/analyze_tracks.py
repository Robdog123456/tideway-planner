#!/usr/bin/env python3
"""
analyze_tracks — turn the raw Strava streams into per-reach, per-pass rows
ready for the stream/wind fits.

Steps
  1. Build a reference centreline from the longest kept track (resampled 50 m),
     giving every GPS point a CHAINAGE (metres upstream of Putney Bridge along
     the river).
  2. Snap landmark boundaries to the centreline -> reaches.
  3. Split each activity into upstream/downstream PASSES (chainage
     monotonicity), then per (pass x reach): median moving SOG, mid-time,
     mean course bearing.
  4. Join tide (Putney HW/LW from the PLA files, lagged with chainage) and
     wind (archive/recent hourly, nearest corridor point).
  5. Write passes.csv + reaches.json.

Run from model/:  python3 analyze_tracks.py
"""
import csv
import glob
import json
import os
from datetime import datetime, timedelta

from tideway_lib import (load_listing, find_extrema, to_putney,
                         haversine_m, bearing_deg, load_wind, wind_at)

# ---------------------------------------------------------------- landmarks
# Reach boundaries (approximate river positions; snapped to the centreline).
# Chainage runs UPSTREAM from Putney Bridge.
LANDMARKS = [
    ("putney_bridge",     51.4679, -0.2128),
    ("hammersmith_bridge", 51.4877, -0.2296),
    ("chiswick_eyot_e",   51.4857, -0.2447),
    ("barnes_bridge",     51.4722, -0.2528),
    ("chiswick_bridge",   51.4704, -0.2679),
    ("kew_bridge",        51.4869, -0.2874),
    ("isleworth_ait",     51.4712, -0.3227),
    ("richmond_lock",     51.4629, -0.3163),
]
REACHES = [  # name, from_landmark, to_landmark  (worst-water weight set later)
    ("putney",      "putney_bridge",      "hammersmith_bridge"),
    ("st_pauls",    "hammersmith_bridge", "chiswick_eyot_e"),
    ("corney",      "chiswick_eyot_e",    "barnes_bridge"),
    ("mortlake",    "barnes_bridge",      "chiswick_bridge"),
    ("kew",         "chiswick_bridge",    "kew_bridge"),
    ("syon",        "kew_bridge",         "isleworth_ait"),
    ("richmond",    "isleworth_ait",      "richmond_lock"),
]

# local tide lag vs Putney, minutes per metre of chainage (PLA: Putney->Kew
# HW +30->+50 over ~9.3 km; LW +100 -> +165)
KEW_CHAIN_M = 9300.0
HW_LAG_PER_M = (50 - 30) * 60.0 / KEW_CHAIN_M      # seconds per metre
LW_LAG_PER_M = (165 - 100) * 60.0 / KEW_CHAIN_M

MIN_MOVING = 1.0          # m/s; below this = paused / spinning / waiting
PASS_MIN_LEN = 400.0      # m of chainage progress to count as a pass segment


# ------------------------------------------------------------- centreline
def build_centreline(tracks):
    """Resample the longest track's upstream leg at ~50 m as the centreline."""
    best = max(tracks.values(), key=lambda t: t["distance_m"])
    pts = best["streams"]["location"]
    # walk until the furthest-from-start point (the turn) = upstream leg
    start = pts[0]
    far_i = max(range(len(pts)), key=lambda i: haversine_m(start, pts[i]))
    leg = pts[:far_i + 1]
    line = [leg[0]]
    for p in leg[1:]:
        if haversine_m(line[-1], p) >= 50.0:
            line.append(p)
    return line


def chain_of(line, chain, p):
    """Chainage of the centreline vertex nearest to p (plus distance to it)."""
    best_i = min(range(len(line)), key=lambda i: haversine_m(line[i], p))
    return chain[best_i], haversine_m(line[best_i], p)


# ------------------------------------------------------------------- tides
def load_putney_events():
    paths = (glob.glob("raw/tides/lb_*.json") +
             glob.glob("../lb_days/lb_*.json"))
    paths = [p for p in paths if "fresh" not in p]
    series = load_listing(paths)
    return to_putney(find_extrema(series)), series


def phase_hours(events, dt, chain_m):
    """Signed hours since the LOCAL HW nearest in time (negative = before HW),
    plus the bracketing HW/LW context. Local = Putney event + chainage lag."""
    best = None
    for edt, h, typ in events:
        lag = HW_LAG_PER_M if typ == "HW" else LW_LAG_PER_M
        local = edt + timedelta(seconds=lag * chain_m)
        d = (dt - local).total_seconds() / 3600.0
        if typ == "HW" and (best is None or abs(d) < abs(best)):
            best = d
    return best


# -------------------------------------------------------------------- main
def main():
    tracks = {}
    with open("raw/strava/manifest.csv") as f:
        manifest = [r for r in csv.DictReader(f) if r["status"] == "kept"]
    for row in manifest:
        path = f"raw/strava/{row['id']}.json"
        if os.path.exists(path):
            with open(path) as g:
                tracks[row["id"]] = json.load(g)
    print(f"{len(tracks)} kept tracks loaded")

    line = build_centreline(tracks)
    chain = [0.0]
    for a, b in zip(line, line[1:]):
        chain.append(chain[-1] + haversine_m(a, b))
    # shift chainage zero to Putney Bridge
    pb = LANDMARKS[0]
    pb_chain, _ = chain_of(line, chain, (pb[1], pb[2]))
    chain = [c - pb_chain for c in chain]
    print(f"centreline: {len(line)} pts, {chain[0]:.0f}..{chain[-1]:.0f} m")

    marks = {}
    for name, lat, lng in LANDMARKS:
        c, off = chain_of(line, chain, (lat, lng))
        marks[name] = c
        print(f"  {name:<18} chainage {c:7.0f} m  (snap {off:4.0f} m)")

    reaches = []
    for name, a, b in REACHES:
        lo, hi = sorted((marks[a], marks[b]))
        reaches.append({"name": name, "from_m": lo, "to_m": hi})
    with open("reaches.json", "w") as f:
        json.dump({"reaches": reaches,
                   "centreline": line, "chainage": chain}, f)

    events, _ = load_putney_events()
    print(f"{len(events)} Putney HW/LW events loaded")

    wind = {}
    for pt in ("putney", "barnes"):
        series = []
        for src in (f"raw/wind/archive_{pt}.json", f"raw/wind/recent_{pt}.json"):
            if os.path.exists(src):
                series += load_wind(src)
        wind[pt] = sorted({w[0]: w for w in series}.values(),
                          key=lambda w: w[0])

    def reach_of(c):
        for r in reaches:
            if r["from_m"] <= c < r["to_m"]:
                return r["name"]
        return None

    rows = []
    for aid, t in tracks.items():
        st = datetime.fromisoformat(t["start_local"])
        loc = t["streams"]["location"]
        tim = t["streams"]["time"]
        vel = t["streams"]["velocity_smooth"]
        ch = [chain_of(line, chain, p)[0] for p in loc]

        # passes: sign of smoothed chainage progress
        seg_start = 0
        segs = []
        for i in range(2, len(ch)):
            if seg_start is not None and i - seg_start >= 3:
                d_all = ch[i] - ch[seg_start]
                d_rec = ch[i] - ch[i - 2]
                if abs(d_all) > PASS_MIN_LEN and d_all * d_rec < 0:
                    segs.append((seg_start, i - 2))
                    seg_start = i - 2
        segs.append((seg_start, len(ch) - 1))

        for a, b in segs:
            if abs(ch[b] - ch[a]) < PASS_MIN_LEN:
                continue
            direction = "up" if ch[b] > ch[a] else "down"
            # bucket points into reaches
            buckets = {}
            for i in range(a, b + 1):
                r = reach_of(ch[i])
                if r and vel[i] >= MIN_MOVING:
                    buckets.setdefault(r, []).append(i)
            for r, idx in buckets.items():
                if len(idx) < 5:
                    continue
                sogs = sorted(vel[i] for i in idx)
                sog = sogs[len(sogs) // 2]
                mid_i = idx[len(idx) // 2]
                t_mid = st + timedelta(seconds=tim[mid_i])
                c_mid = ch[mid_i]
                brg = bearing_deg(loc[idx[0]], loc[idx[-1]])
                ph = phase_hours(events, t_mid, max(c_mid, 0.0))
                wpt = "putney" if c_mid < 3500 else "barnes"
                ws = wg = wd = ""
                if wind.get(wpt):
                    ws, wg, wd = wind_at(wind[wpt], t_mid)
                rows.append({
                    "activity": aid, "date": st.date().isoformat(),
                    "pass": f"{a}-{b}", "direction": direction, "reach": r,
                    "chain_mid_m": round(c_mid), "t_mid": t_mid.isoformat(),
                    "phase_hr_vs_HW": round(ph, 2) if ph is not None else "",
                    "sog_med": round(sog, 3), "n_pts": len(idx),
                    "course_deg": round(brg),
                    "wind_mph": round(ws, 1) if ws != "" else "",
                    "gust_mph": round(wg, 1) if wg != "" else "",
                    "wind_dir": round(wd) if wd != "" else "",
                })

    with open("passes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"passes.csv: {len(rows)} reach-passes from {len(tracks)} tracks")


if __name__ == "__main__":
    main()
