#!/usr/bin/env python3
"""
fetch_flag — compute the PLA ebb-tide flag from the Environment Agency's
live Richmond gauge (station 0009, tidal level in mAOD, 15-min readings).

The flag is COMPUTED, not judged: at 06:00 and 18:00 (local wall clock) the
LOWEST Richmond reading of the PRECEDING 12 hours sets it:

    >= 2.6 m  RED      (strong freshwater flow — CRSA clearance, low flood only)
    >= 1.7 m  YELLOW
    >= 0.0 m  GREEN    (caution / good lookout)
    <  0.0 m  BLACK    (very low water on the ebb — don't boat at low tide)

This script writes data/flag.json:
  - current : the flag as set at the last 06:00/18:00 boundary
  - next    : a PERSISTENCE estimate of the next setting (the trailing 12 h
              minimum right now). Freshwater flow changes slowly, so this is
              honest — but it is an estimate, and the PWA labels it as one.

The PWA computes the same thing client-side from the EA API (CORS-open) when
online; this snapshot is its offline / API-down fallback. A failed fetch is
therefore NOT fatal: we warn and leave the previous snapshot in place.

Run from pipeline/:  python3 fetch_flag.py
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EA = ("https://environment.data.gov.uk/flood-monitoring"
      "/id/stations/0009/readings")
OUT = "data/flag.json"
LONDON = ZoneInfo("Europe/London")


def band(v):
    if v >= 2.6:
        return "RED"
    if v >= 1.7:
        return "YELLOW"
    if v >= 0.0:
        return "GREEN"
    return "BLACK"


def fetch_readings(hours=36):
    """[(aware datetime UTC, metres)] for the last `hours`, oldest first."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours))
    url = (f"{EA}?parameter=level&_sorted"
           f"&since={since.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    with urllib.request.urlopen(url, timeout=30) as r:
        items = json.loads(r.read().decode())["items"]
    out = []
    for it in items:
        t = datetime.strptime(it["dateTime"], "%Y-%m-%dT%H:%M:%SZ")
        out.append((t.replace(tzinfo=timezone.utc), float(it["value"])))
    return sorted(out)


def last_boundary(now_local):
    """The most recent 06:00 or 18:00 (local wall clock) at or before now."""
    six = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    eighteen = now_local.replace(hour=18, minute=0, second=0, microsecond=0)
    if now_local >= eighteen:
        return eighteen
    if now_local >= six:
        return six
    return eighteen - timedelta(days=1)


def window_min(readings, t0, t1):
    vals = [v for t, v in readings if t0 <= t <= t1]
    return min(vals) if vals else None


def main():
    try:
        readings = fetch_readings()
        if not readings:
            raise ValueError("EA returned no readings")
    except Exception as e:
        print(f"WARNING: EA fetch failed ({e}) — keeping previous {OUT}")
        return

    now = datetime.now(LONDON)
    boundary = last_boundary(now)
    cur_min = window_min(readings, boundary - timedelta(hours=12), boundary)
    trail_min = window_min(readings, now - timedelta(hours=12), now)
    latest_t, latest_v = readings[-1]

    next_at = boundary + timedelta(hours=12)
    snapshot = {
        "fetched": now.isoformat(timespec="seconds"),
        "station": "EA 0009 Richmond (tidal level, mAOD)",
        "latest": {"time": latest_t.astimezone(LONDON)
                   .isoformat(timespec="minutes"),
                   "level_m": latest_v},
        "current": None if cur_min is None else {
            "flag": band(cur_min),
            "set_at": boundary.isoformat(timespec="minutes"),
            "min_12h_m": round(cur_min, 3)},
        "next": None if trail_min is None else {
            "flag_estimate": band(trail_min),
            "at": next_at.isoformat(timespec="minutes"),
            "trailing_min_m": round(trail_min, 3)},
    }
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(snapshot, f, indent=1)
    cur = snapshot["current"]
    nxt = snapshot["next"]
    print(f"flag: {cur['flag'] if cur else '?'} "
          f"(set {cur['set_at'] if cur else '?'}, "
          f"12h min {cur['min_12h_m'] if cur else '?'} m) | "
          f"next est {nxt['flag_estimate'] if nxt else '?'} at "
          f"{nxt['at'] if nxt else '?'} | latest {latest_v} m {latest_t}")


if __name__ == "__main__":
    main()
