#!/usr/bin/env python3
"""
prefetch_tides — year-ahead London Bridge tide extrema from PLA's own
predictions. The tide backend since 2026-08-02.

PLA's bot-wall 403s GitHub-runner IPs, but tide predictions are astronomy:
stable for years ahead and byte-identical on re-pull. So this script — run
from a residential IP (Rob's Mac: launchd com.tideway.tides monthly, or by
hand) — pulls the minute listings for today-1 .. today+DAYS into a local
scratch (gitignored, resumable: a day already on disk is never refetched),
distills them with the SAME load_listing/find_extrema the pipeline has
always used, and writes data/tides_extrema.json (~100 KB, committed). CI
consumes that file via tideway_lib.load_lb_extrema and needs no tide
network at all.

Safety: if any day in the window cannot be fetched, the script aborts
WITHOUT writing the output — a gap in the minute series would let
find_extrema fabricate edge events. Before writing it also proves the
events alternate HW/LW at sane spacing and that every extremum derivable
from the committed live minute cache matches to the minute and centimetre.
"""
import argparse
import glob
import json
import os
import time
from datetime import date, datetime, timedelta

from tideway_lib import fetch_gauge, load_listing, find_extrema
from fetch_tides import LB, pla_tz

SCRATCH = "../local-data/prefetch-tides"   # Mac-only, gitignored via local-data/
OUT = "data/tides_extrema.json"


def fetch_missing(days):
    os.makedirs(SCRATCH, exist_ok=True)
    today = date.today()
    wanted = [today + timedelta(days=n) for n in range(-1, days + 1)]
    todo = [d for d in wanted
            if not os.path.exists(f"{SCRATCH}/lb_{d.isoformat()}.json")]
    print(f"prefetch {wanted[0]} .. {wanted[-1]}: "
          f"{len(wanted)} days, {len(todo)} to fetch", flush=True)
    failed = []
    for i, d in enumerate(todo, 1):
        ok = False
        for attempt in (1, 2):
            try:
                fetch_gauge(LB, d.year, d.month, d.day, tz=pla_tz(d),
                            span=1,
                            out_path=f"{SCRATCH}/lb_{d.isoformat()}.json")
                ok = True
                break
            except Exception as e:
                print(f"    attempt {attempt} failed for {d}: {e}", flush=True)
                time.sleep(5)
        if not ok:
            failed.append(d)
        if i % 25 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] fetched to {d}", flush=True)
        time.sleep(1.0)   # be polite: ~1 req/s, and only once per horizon
    return wanted, failed


def distill(wanted):
    keep = {f"lb_{d.isoformat()}.json" for d in wanted}
    paths = sorted(p for p in glob.glob(f"{SCRATCH}/lb_*.json")
                   if os.path.basename(p) in keep)
    events = find_extrema(load_listing(paths))[1:-1]   # trim edge artifacts

    for a, b in zip(events, events[1:]):
        gap_h = (b[0] - a[0]).total_seconds() / 3600
        assert a[2] != b[2], f"non-alternating events: {a} / {b}"
        assert 2.0 <= gap_h <= 9.5, f"implausible gap {gap_h:.1f} h: {a} -> {b}"

    # cross-check: the committed live minute cache must distill to the
    # same extrema through this new path (code-equivalence proof)
    n_checked = 0
    cache = sorted(glob.glob("data/tides/lb_*.json"))
    if cache:
        for e in find_extrema(load_listing(cache))[1:-1]:
            near = [x for x in events if x[2] == e[2]
                    and abs((x[0] - e[0]).total_seconds()) <= 60]
            assert near, f"cache event unmatched in prefetch: {e}"
            assert abs(near[0][1] - e[1]) <= 0.011, \
                f"height drift: cache {e} vs prefetch {near[0]}"
            n_checked += 1

    out = {"generated": datetime.now().isoformat(timespec="seconds"),
           "source": f"PLA gauge {LB} minute predictions (prefetch_tides.py)",
           "span": [events[0][0].isoformat(timespec="minutes"),
                    events[-1][0].isoformat(timespec="minutes")],
           "events": [{"t": e[0].isoformat(timespec="minutes"),
                       "h": round(e[1], 2), "type": e[2]} for e in events]}
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(f"{OUT}: {len(out['events'])} events, "
          f"{out['span'][0]} .. {out['span'][1]}, "
          f"{n_checked} verified against the live minute cache")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=370,
                    help="days ahead to cover (default 370)")
    a = ap.parse_args()
    wanted, failed = fetch_missing(a.days)
    if failed:
        print(f"ABORT — {len(failed)} day(s) unfetchable "
              f"(first: {failed[0]}); {OUT} left untouched")
        raise SystemExit(1)
    distill(wanted)
