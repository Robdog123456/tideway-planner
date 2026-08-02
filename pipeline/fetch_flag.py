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
EXTREMA = "data/tides_extrema.json"
LONDON = ZoneInfo("Europe/London")

# --- surge calibration: Richmond observed (mAOD) vs predicted LB (CD) ---
# expected_mAOD = predicted_CD + SURGE_OFFSET_M, valid ONLY near HW (the
# Richmond half-lock plateaus LW, so deltas are HW-gated). Fitted
# 2026-08-02 over 54 HW pairs spanning a full spring-neap cycle
# (pred 5.77-7.36 m CD): RMS 10.1 cm; a linear model came out slope 1.033
# with the same RMS, so the constant is the right shape, not a shortcut.
# Observed-peak lag vs predicted HW: median +5 min — no lag term needed.
SURGE_OFFSET_M = -2.685
SURGE_HW_GATE_H = 2.5   # observed-peak search window around a predicted HW
SURGE_MAX_AGE_H = 13    # newest usable HW peak (one tidal cycle + margin)
SURGE_MIN_N = 4         # fewer window readings -> peak can't be trusted
SURGE_SANITY_M = 3.0    # |delta| beyond this = sensor fault, discard


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


def compute_surge(readings, now):
    """Observed-vs-predicted delta at the last completed HW peak, or None.

    Peak-to-peak, matching exactly how SURGE_OFFSET_M was calibrated:
    pointwise limb comparison measures the SHAPE mismatch between
    Richmond's curve and the LB prediction (up to ~0.7 m on a calm day),
    not surge — only the HW peaks are comparable. A completed peak (max
    reading interior to its search window, water already falling) up to
    one tidal cycle old serves as a persistence nowcast, mirroring how
    the ebb-flag itself uses a trailing window."""
    if not os.path.exists(EXTREMA):
        return None
    with open(EXTREMA) as f:
        hws = [(datetime.fromisoformat(e["t"]), e["h"])
               for e in json.load(f)["events"] if e["type"] == "HW"]
    naive = sorted((t.astimezone(LONDON).replace(tzinfo=None), v)
                   for t, v in readings)
    now_naive = now.astimezone(LONDON).replace(tzinfo=None)

    peaks = []                       # (hw_t, delta) for completed HW peaks
    for hw_t, hw_h in hws:
        age_h = (now_naive - hw_t).total_seconds() / 3600
        if not (-SURGE_HW_GATE_H <= age_h <= 36):
            continue
        win = [(t, v) for t, v in naive
               if abs((t - hw_t).total_seconds()) <= SURGE_HW_GATE_H * 3600
               and t <= now_naive]
        if len(win) < SURGE_MIN_N:
            continue
        pt, pv = max(win, key=lambda x: x[1])
        if pt in (win[0][0], win[-1][0]):
            continue                 # truncated peak — not completed yet
        peaks.append((hw_t, pv - (hw_h + SURGE_OFFSET_M)))

    if not peaks:
        return None
    hw_t, delta = peaks[-1]
    if (now_naive - hw_t).total_seconds() / 3600 > SURGE_MAX_AGE_H:
        return None
    if abs(delta) > SURGE_SANITY_M:
        print(f"WARNING: surge delta {delta:+.2f} m implausible — discarded")
        return None
    trend = "steady"
    if len(peaks) >= 2:
        slope = delta - peaks[-2][1]
        trend = ("rising" if slope > 0.05
                 else "falling" if slope < -0.05 else "steady")
    return {"delta_m": round(delta, 2), "trend": trend,
            "hw_at": hw_t.isoformat(timespec="minutes")}


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
    surge = compute_surge(readings, now)
    if surge:
        snapshot["surge"] = surge   # app-ignored key; compute_v2 turns it
                                    # into display-only notes fragments
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(snapshot, f, indent=1)
    cur = snapshot["current"]
    nxt = snapshot["next"]
    print(f"flag: {cur['flag'] if cur else '?'} "
          f"(set {cur['set_at'] if cur else '?'}, "
          f"12h min {cur['min_12h_m'] if cur else '?'} m) | "
          f"next est {nxt['flag_estimate'] if nxt else '?'} at "
          f"{nxt['at'] if nxt else '?'} | latest {latest_v} m {latest_t} | "
          f"surge {surge['delta_m'] if surge else 'n/a (outside HW window)'}")


if __name__ == "__main__":
    main()
