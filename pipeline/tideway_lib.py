#!/usr/bin/env python3
"""
tideway_lib — shared helpers for the Tideway row-window model (v2).

Factored out of ../compute.py (the calibrated v1 engine) so the v2 model,
the fetchers and the analysis scripts all use ONE implementation of:
  - PLA gauge fetch (bot-wall: needs a browser User-Agent + Referer)
  - minute-listing load / HW-LW extrema detection
  - London Bridge -> Putney secondary-port conversion (PLA average offsets)
  - wind series interpolation
plus new v2 geometry helpers (haversine distance, bearings, chainage).

Heights are metres above chart datum LOCAL to each gauge. Times follow the
`tz` requested from the PLA endpoint (tz=2 -> BST). All model times are
Europe/London wall-clock.
"""
import json
import math
import urllib.request
from datetime import datetime, timedelta

# ---------------------------------------------------------------- constants
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PLA_BASE = "https://tidepredictions.pla.co.uk"
GAUGES = {"london_bridge": "0113", "chelsea": "0113A", "richmond": "0116"}

# PLA average secondary-port offsets, London Bridge -> Putney (validated v1)
PUTNEY_HW = (31, -1.0)   # minutes, metres
PUTNEY_LW = (98, -0.5)

LRC = (51.4688, -0.2193)  # London Rowing Club, Putney Embankment


# ---------------------------------------------------------------- PLA fetch
def fetch_gauge(gauge, year, month, day, tz=2, span=1, out_path=None):
    """Fetch PLA per-minute predictions. Returns the parsed JSON dict.

    tz: 1=GMT, 2=BST.  span: 1=one day (the response still carries ~2 days).
    The `table` field in the response is a stale fixed block — never use it;
    derive HW/LW from `listing` via find_extrema().
    """
    url = f"{PLA_BASE}/gauge_data/{gauge}/{year}/{month}/{day}/{tz}/{span}/"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": PLA_BASE + "/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    if out_path:
        with open(out_path, "w") as f:
            json.dump(data, f)
    return data


# ------------------------------------------------------------- series tools
def load_listing(paths):
    """Load one or more gauge_data JSON files -> sorted, deduped [(dt, h)]."""
    seen = {}
    for p in paths:
        with open(p) as f:
            d = json.load(f)
        for r in d["listing"]:
            dt = datetime.strptime(r["date"] + " " + r["time"], "%d/%m/%Y %H:%M")
            seen[dt] = float(r["height"])
    return sorted(seen.items())


def find_extrema(series):
    """HW/LW turning points from a minute series. Port of compute.py v1.

    30-min centred slope window; a sign change closes an event; the extreme
    is the max/min within +-75 min; events <3 h apart of the same type merge.
    Returns [[dt, height, 'HW'|'LW'], ...].
    """
    n = len(series)
    W = 15

    def slope(i):
        a = max(0, i - W)
        b = min(n - 1, i + W)
        return series[b][1] - series[a][1]

    raw = []
    last_sign = 0
    for i in range(n):
        s = slope(i)
        sign = 1 if s > 0.005 else (-1 if s < -0.005 else 0)
        if sign == 0:
            continue
        if last_sign and sign != last_sign:
            typ = "HW" if last_sign > 0 else "LW"
            a = max(0, i - 75)
            b = min(n - 1, i + 75)
            j = (max(range(a, b + 1), key=lambda k: series[k][1]) if typ == "HW"
                 else min(range(a, b + 1), key=lambda k: series[k][1]))
            raw.append([series[j][0], series[j][1], typ])
        last_sign = sign

    cleaned = []
    for e in raw:
        if cleaned and (e[0] - cleaned[-1][0]).total_seconds() < 3 * 3600:
            if e[2] == cleaned[-1][2]:
                if (e[2] == "HW" and e[1] > cleaned[-1][1]) or \
                   (e[2] == "LW" and e[1] < cleaned[-1][1]):
                    cleaned[-1] = e
            continue
        cleaned.append(e)
    return cleaned


def to_putney(extrema):
    """London Bridge HW/LW events -> Putney (PLA average offsets)."""
    ev = []
    for dt, h, typ in extrema:
        dm, dh = PUTNEY_HW if typ == "HW" else PUTNEY_LW
        ev.append([dt + timedelta(minutes=dm), round(h + dh, 2), typ])
    return sorted(ev, key=lambda e: e[0])


def interp_height(series, dt):
    """Linear interpolation of a minute series at datetime dt (None if outside)."""
    if not series or dt < series[0][0] or dt > series[-1][0]:
        return None
    lo, hi = 0, len(series) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if series[mid][0] <= dt:
            lo = mid
        else:
            hi = mid
    t0, h0 = series[lo]
    t1, h1 = series[hi]
    if t1 == t0:
        return h0
    f = (dt - t0).total_seconds() / (t1 - t0).total_seconds()
    return h0 + f * (h1 - h0)


# ---------------------------------------------------------------- wind tools
def load_wind(path):
    """Open-Meteo hourly JSON -> [(dt, speed, gust, direction)] (mph, deg-from)."""
    with open(path) as f:
        w = json.load(f)["hourly"]
    return [(datetime.strptime(t, "%Y-%m-%dT%H:%M"),
             w["wind_speed_10m"][i], w["wind_gusts_10m"][i],
             w["wind_direction_10m"][i])
            for i, t in enumerate(w["time"])]


def wind_at(wind, dt):
    """Linear interpolation (speed/gust; direction taken from nearest hour)."""
    if dt <= wind[0][0]:
        return wind[0][1], wind[0][2], wind[0][3]
    if dt >= wind[-1][0]:
        return wind[-1][1], wind[-1][2], wind[-1][3]
    for k in range(len(wind) - 1):
        t0, s0, g0, d0 = wind[k]
        t1, s1, g1, d1 = wind[k + 1]
        if t0 <= dt <= t1:
            f = (dt - t0).total_seconds() / (t1 - t0).total_seconds()
            return (s0 + f * (s1 - s0), g0 + f * (g1 - g0),
                    d0 if f < 0.5 else d1)
    return wind[-1][1], wind[-1][2], wind[-1][3]


# ------------------------------------------------------------- geometry (v2)
def haversine_m(p, q):
    """Great-circle distance in metres between (lat, lng) points."""
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    a = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def bearing_deg(p, q):
    """Initial bearing degrees (0=N, clockwise) from p to q, (lat, lng)."""
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = (math.cos(la1) * math.sin(la2) -
         math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def ang_diff(a, b):
    """Smallest absolute angular difference in degrees (0-180)."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d
