#!/usr/bin/env python3
"""
derive_reach_bands — empirical calibration for the per-reach low-water bands.

For every kept reach-pass in passes.csv (235 passes, 29 sessions), compute the
transit time RELATIVE TO THE REACH-LOCAL LW CLOCK (Putney LW + the propagation
lag derived in derive_reach_lw.py) and report, per reach, how close to local
LW Rob has actually rowed each one — the observed envelope, in the same spirit
as the HW gate's "above anything ever boated".

Reads: passes.csv, data/reach_lw_offsets.json, the historical LB tide archives
(raw/tides + ../lb_days — same globs as the backtest). No network.
"""
import csv
import glob
import json
from datetime import datetime

from tideway_lib import load_lb_extrema, to_putney, PUTNEY_LW


def local_extra_min(chain_m, slope):
    return chain_m * slope


def main():
    with open("data/reach_lw_offsets.json") as f:
        cal = json.load(f)
    slope = cal["lw_slope_min_per_m"]

    paths = [p for p in
             glob.glob("raw/tides/lb_*.json") + glob.glob("../lb_days/lb_*.json")
             + glob.glob("data/tides/lb_*.json")
             if "fresh" not in p and "test" not in p]
    pev = [e for e in to_putney(load_lb_extrema(paths, extrema_file=None))
           if e[2] == "LW"]

    per_reach = {}
    with open("passes.csv") as f:
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row["t_mid"])
            near = min(pev, key=lambda e: abs((t - e[0]).total_seconds()))
            vs_putney_lw = (t - near[0]).total_seconds() / 60.0
            vs_local_lw = vs_putney_lw - local_extra_min(
                float(row["chain_mid_m"]), slope)
            per_reach.setdefault(row["reach"], []).append(
                (round(vs_local_lw), row["date"], row["direction"]))

    print(f"pass count: {sum(len(v) for v in per_reach.values())}   "
          f"slope {slope*1000:.2f} min/km\n")
    print(f"{'reach':<12}{'n':>4}  {'closest BEFORE local LW':<26}"
          f"{'closest AFTER local LW':<26}all transits within LW+-150")
    order = ["putney", "st_pauls", "corney", "mortlake", "kew", "syon"]
    for reach in order:
        xs = sorted(per_reach.get(reach, []))
        before = [x for x in xs if x[0] < 0]
        after = [x for x in xs if x[0] >= 0]
        cb = max(before) if before else None       # closest to LW from below
        ca = min(after) if after else None         # closest to LW from above
        inband = sorted(x[0] for x in xs if abs(x[0]) <= 150)
        print(f"{reach:<12}{len(xs):>4}  "
              f"{str(cb):<26}{str(ca):<26}{inband}")


if __name__ == "__main__":
    main()
