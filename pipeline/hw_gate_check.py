#!/usr/bin/env python3
"""
hw_gate_check — empirical cross-check of the Putney Embankment high-water gate.

For every kept session: predicted Putney height at LAUNCH (start_local) and at
LANDING (start + elapsed), from the PLA-derived Putney HW/LW events (piecewise
cosine, as in compute_v2). The gate proposal (block launch/land at >= 5.85 m
Putney) must conflict with ZERO sessions Rob actually boated — any conflict
means either the threshold is wrong or he launched through a covered hard.

Writes gate_check.csv and prints the distribution + any conflicts.
"""
import csv
import glob
import json
import math
from datetime import datetime, timedelta

from tideway_lib import load_listing, find_extrema, to_putney

GATE_M = 5.85


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


def main():
    paths = [p for p in
             glob.glob("raw/tides/lb_*.json") + glob.glob("../lb_days/lb_*.json")
             if "fresh" not in p and "test" not in p]
    pev = to_putney(find_extrema(load_listing(paths)))
    print(f"{len(pev)} Putney events from {len(paths)} tide files")

    rows = []
    with open("raw/strava/manifest.csv") as f:
        for r in csv.DictReader(f):
            if r["status"] != "kept":
                continue
            st = datetime.fromisoformat(r["start_local"])
            en = st + timedelta(seconds=int(float(r["elapsed_s"])))
            h0 = putney_height(pev, st)
            h1 = putney_height(pev, en)
            if h0 is None or h1 is None:
                rows.append({"id": r["id"], "date": r["date"],
                             "launch": st.strftime("%H:%M"),
                             "land": en.strftime("%H:%M"),
                             "h_launch_m": "", "h_land_m": "",
                             "conflict": "NO_TIDE_DATA"})
                continue
            conflict = "GATE_CONFLICT" if max(h0, h1) >= GATE_M else ""
            rows.append({"id": r["id"], "date": r["date"],
                         "launch": st.strftime("%H:%M"),
                         "land": en.strftime("%H:%M"),
                         "h_launch_m": round(h0, 2), "h_land_m": round(h1, 2),
                         "conflict": conflict})

    with open("gate_check.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    hs = [max(r["h_launch_m"], r["h_land_m"]) for r in rows
          if r["h_launch_m"] != ""]
    hs.sort()
    print(f"{len(hs)} sessions with tide data")
    print(f"launch/land height max-of-session: min {hs[0]:.2f}, median "
          f"{hs[len(hs)//2]:.2f}, max {hs[-1]:.2f} m Putney")
    n_conf = sum(1 for r in rows if r["conflict"] == "GATE_CONFLICT")
    n_miss = sum(1 for r in rows if r["conflict"] == "NO_TIDE_DATA")
    print(f"gate {GATE_M} m: {n_conf} conflicts, {n_miss} missing tide data")
    for r in rows:
        if r["conflict"] == "GATE_CONFLICT":
            print("  CONFLICT:", r)


if __name__ == "__main__":
    main()
