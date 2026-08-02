#!/usr/bin/env python3
"""
fetch_tides — pull PLA London Bridge per-minute tide predictions.

Two modes:

  python3 fetch_tides.py            # HISTORICAL (backtest support, unchanged):
                                    # every session date in manifest.csv,
                                    # files -> raw/tides/ (local-data only)

  python3 fetch_tides.py --live     # LIVE (the scheduled pipeline):
                                    # a rolling window today-1 .. today+35,
                                    # one file per day -> data/tides/

Live mode is what GitHub Actions runs every 6 hours. Since 2026-08-02 the
horizon normally comes entirely from data/tides_extrema.json (a year of
PLA extrema prefetched by prefetch_tides.py on the Mac — PLA 403s
GitHub-runner IPs), so a CI run fetches NOTHING and just prunes + reports
coverage. The fetch path below remains for days outside the prefetched
span. It is deliberately boring and cache-friendly:
  - PLA predictions are astronomical and STABLE (a re-pull is byte-identical),
    so a day already on disk is never fetched again — after the first backfill
    a normal run makes only 1-2 requests (the new day at the horizon).
  - each fetch-day is requested in ITS OWN timezone (tz=2 BST / tz=1 GMT,
    decided by Europe/London on that date) so the late-October clock change
    doesn't shift the listing by an hour mid-window.
  - old files are pruned (before yesterday) to keep the committed cache small.
  - the run FAILS (exit 1) only if the on-disk window covers less than
    MIN_DAYS_HARD days (the grid itself is about to run dry). A window below
    MIN_DAYS_AHEAD is a WARNING only: publish must continue off the cached
    tides (PLA blocked GitHub-runner IPs on 2026-07-31 and froze the whole
    app for 2.5 days because this gate sat in front of wind+grid+deploy).
    The tide-horizon-watch job in build.yml runs --coverage-only, which DOES
    exit 1 below MIN_DAYS_AHEAD — that reddens the run (failure email)
    without blocking the deploy that already happened.
"""
import argparse
import csv
import glob
import json
import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tideway_lib import fetch_gauge

RAW = "raw/tides"        # historical (gitignored, backtest only)
LIVE = "data/tides"      # rolling live cache (committed)
EXTREMA = "data/tides_extrema.json"   # year-ahead prefetch (prefetch_tides.py)
LB = "0113"              # London Bridge gauge
MIN_DAYS_AHEAD = 28      # watch horizon: below this the run goes red (email)
MIN_DAYS_HARD = 7        # hard gate: below this the fetch step itself fails
LONDON = ZoneInfo("Europe/London")


def pla_tz(day):
    """PLA tz parameter for a date: 2 while the UK is on BST, else 1 (GMT)."""
    offset = datetime(day.year, day.month, day.day, 12,
                      tzinfo=LONDON).utcoffset()
    return 2 if offset == timedelta(hours=1) else 1


def extrema_span():
    """(start, end) datetimes of the prefetched extrema file, or None."""
    if not os.path.exists(EXTREMA):
        return None
    with open(EXTREMA) as f:
        s = json.load(f)["span"]
    return datetime.fromisoformat(s[0]), datetime.fromisoformat(s[1])


def in_extrema_span(d, span):
    """True if the prefetched events bracket every slot of date d."""
    if not span:
        return False
    day0 = datetime(d.year, d.month, d.day)
    return span[0] <= day0 and day0 + timedelta(days=1) <= span[1]


def fetch_day(day, out_path):
    """One polite, once-retried fetch of a single day's minute listing."""
    for attempt in (1, 2):
        try:
            data = fetch_gauge(LB, day.year, day.month, day.day,
                               tz=pla_tz(day), span=1, out_path=out_path)
            return len(data.get("listing", []))
        except Exception as e:
            print(f"    attempt {attempt} failed for {day}: {e}")
            time.sleep(5)
    return None


# ---------------------------------------------------------------- live mode
def live(days_ahead):
    os.makedirs(LIVE, exist_ok=True)
    today = date.today()
    wanted = [today + timedelta(days=n) for n in range(-1, days_ahead + 1)]

    span = extrema_span()
    todo = [d for d in wanted
            if not os.path.exists(f"{LIVE}/lb_{d.isoformat()}.json")
            and not in_extrema_span(d, span)]
    print(f"live window {wanted[0]} .. {wanted[-1]}: {len(wanted)} days, "
          f"{len(todo)} to fetch"
          + (f" (extrema cover to {span[1]:%Y-%m-%d})" if span else ""))

    for i, d in enumerate(todo, 1):
        n = fetch_day(d, f"{LIVE}/lb_{d.isoformat()}.json")
        print(f"  [{i}/{len(todo)}] {d} -> "
              f"{n if n is not None else 'FAILED'} minutes")
        if n is not None and n < 1000:
            print(f"    WARNING: short listing for {d}")
        time.sleep(1.0)  # be polite to the PLA server

    # prune anything before yesterday (its tides are history)
    for p in glob.glob(f"{LIVE}/lb_*.json"):
        d = date.fromisoformat(os.path.basename(p)[3:13])
        if d < today - timedelta(days=1):
            os.remove(p)
            print(f"  pruned {p}")

    report_coverage(today)


def uncovered(today, days_ahead):
    """Days in the next `days_ahead` not covered by the prefetched extrema,
    their own minute file, or their predecessor's (each file carries ~2 days
    of minutes)."""
    have = {os.path.basename(p)[3:13] for p in glob.glob(f"{LIVE}/lb_*.json")}
    span = extrema_span()
    return len(have), [d for d in (today + timedelta(days=n)
                                   for n in range(days_ahead))
                       if not in_extrema_span(d, span)
                       and d.isoformat() not in have
                       and (d - timedelta(days=1)).isoformat() not in have]


def report_coverage(today, strict=False):
    """Soft warning below MIN_DAYS_AHEAD; exit 1 below MIN_DAYS_HARD —
    or below MIN_DAYS_AHEAD when strict (the tide-horizon-watch job)."""
    nfiles, missing = uncovered(today, MIN_DAYS_AHEAD)
    _, missing_hard = uncovered(today, MIN_DAYS_HARD)
    span = extrema_span()
    print(f"coverage: {nfiles} minute files, extrema "
          + (f"to {span[1]:%Y-%m-%d}" if span else "ABSENT")
          + f", {len(missing)} uncovered days in the next {MIN_DAYS_AHEAD}")
    if missing_hard:
        print(f"COVERAGE GATE FAILED (hard, <{MIN_DAYS_HARD} d) — "
              f"first uncovered day: {missing_hard[0]}")
        raise SystemExit(1)
    if missing:
        print(f"COVERAGE WARNING — window below {MIN_DAYS_AHEAD} d "
              f"(first gap {missing[0]}); publishing continues on the "
              f"cached tides, tide-horizon-watch turns the run red")
        if strict:
            raise SystemExit(1)


# ---------------------------------------------- historical mode (unchanged)
def session_dates(manifest="raw/strava/manifest.csv"):
    """Unique session dates (kept rows only), as date objects."""
    out = set()
    with open(manifest) as f:
        for row in csv.DictReader(f):
            if row["status"].strip() == "kept":
                out.add(date.fromisoformat(row["date"]))
    return sorted(out)


def have_hist(day):
    name = f"lb_{day.isoformat()}.json"
    return (os.path.exists(f"{RAW}/{name}")
            or os.path.exists(f"../lb_days/{name}"))


def historical():
    dates = session_dates()
    print(f"{len(dates)} unique session dates: {dates[0]} .. {dates[-1]}")
    fetch_days = sorted({d - timedelta(days=1) for d in dates})
    todo = [d for d in fetch_days if not have_hist(d)]
    print(f"{len(fetch_days)} fetch-days, {len(todo)} to download")
    for i, d in enumerate(todo, 1):
        n = fetch_day(d, f"{RAW}/lb_{d.isoformat()}.json")
        print(f"  [{i}/{len(todo)}] {d} -> {n} minutes")
        time.sleep(1.0)
    print(f"done — {len(glob.glob(f'{RAW}/lb_*.json'))} LB files in {RAW}/")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="rolling forward window for the scheduled pipeline")
    ap.add_argument("--days", type=int, default=35,
                    help="live mode: days ahead to cover (default 35)")
    ap.add_argument("--coverage-only", action="store_true",
                    help="no network: report cache coverage, exit 1 below "
                         f"the {MIN_DAYS_AHEAD} d watch horizon")
    a = ap.parse_args()
    if a.coverage_only:
        report_coverage(date.today(), strict=True)
    elif a.live:
        live(a.days)
    else:
        historical()
