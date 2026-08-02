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
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tideway_lib import fetch_gauge, load_listing, find_extrema
from fetch_tides import LB, pla_tz

SCRATCH = "../local-data/prefetch-tides"   # Mac-only, gitignored via local-data/
OUT = "data/tides_extrema.json"
LONDON = ZoneInfo("Europe/London")


def load_minutes(paths):
    """Minute series from PLA day files, normalised to London wall-clock.

    Beyond the current calendar year PLA leaves the tabular `listing` empty
    but still serves the same minute predictions in `graph_data.graphs`
    (day-series of {x: epoch, y: height}, epochs encoded as wall-clock in
    the REQUESTED tz pretending to be UTC). So: listing when populated,
    graphs otherwise. Each file contributes only its own named day (every
    day has its own file here, unlike the live cache's 2-day spillover),
    timestamps are decoded to physical time via the day's request tz
    (pla_tz), then rendered to naive Europe/London wall-clock — which keeps
    the series continuous across both DST seams instead of fabricating a
    jump (and therefore fake extrema) at the changeover midnights.
    """
    seen = {}
    for p in paths:
        day = date.fromisoformat(os.path.basename(p)[3:13])
        off = timedelta(hours=1 if pla_tz(day) == 2 else 0)
        with open(p) as f:
            d = json.load(f)
        if d.get("listing"):
            pts = ((datetime.strptime(r["date"] + " " + r["time"],
                                      "%d/%m/%Y %H:%M"), float(r["height"]))
                   for r in d["listing"])
        else:
            pts = ((datetime.fromtimestamp(pt["x"], timezone.utc)
                    .replace(tzinfo=None), float(pt["y"]))
                   for series in d.get("graph_data", {}).get("graphs", {})
                   .values() for pt in series)
        for naive, h in pts:
            if naive.date() != day:
                continue                     # own-day filter (tz-mix guard)
            phys = naive - off               # request-tz wall-clock -> UTC
            wall = phys.replace(tzinfo=timezone.utc).astimezone(LONDON) \
                       .replace(tzinfo=None)
            seen[wall] = h
    return sorted(seen.items())


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
    events = find_extrema(load_minutes(paths))[1:-1]   # trim edge artifacts

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
