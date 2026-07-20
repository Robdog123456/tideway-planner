#!/usr/bin/env python3
"""
fetch_tides — pull PLA London Bridge minute predictions for every session date
in manifest.csv (plus the day before, so an early-morning session still has its
preceding HW/LW in the series).

The PLA endpoint returns ~2 days of per-minute heights starting at the
requested date, so fetching (date - 1) covers (date - 1) AND (date).
Files land in raw/tides/lb_<YYYY-MM-DD>.json and are never re-fetched if
already present (the on-disk ../lb_days/ July files are reused the same way).

Run from model/:  python3 fetch_tides.py
"""
import csv
import glob
import os
import time
from datetime import date, timedelta

from tideway_lib import fetch_gauge

RAW = "raw/tides"
LB = "0113"


def session_dates(manifest="raw/strava/manifest.csv"):
    """Unique session dates (kept rows only), as date objects."""
    out = set()
    with open(manifest) as f:
        for row in csv.DictReader(f):
            if row["status"].strip() == "kept":
                out.add(date.fromisoformat(row["date"]))
    return sorted(out)


def have(day):
    """True if a file already covers this fetch-day (here or in ../lb_days)."""
    name = f"lb_{day.isoformat()}.json"
    return (os.path.exists(f"{RAW}/{name}")
            or os.path.exists(f"../lb_days/{name}"))


def main():
    dates = session_dates()
    print(f"{len(dates)} unique session dates: {dates[0]} .. {dates[-1]}")

    # fetch-day = the day BEFORE each session date (2-day span covers both)
    fetch_days = sorted({d - timedelta(days=1) for d in dates})
    todo = [d for d in fetch_days if not have(d)]
    print(f"{len(fetch_days)} fetch-days, {len(todo)} to download")

    for i, d in enumerate(todo, 1):
        out = f"{RAW}/lb_{d.isoformat()}.json"
        data = fetch_gauge(LB, d.year, d.month, d.day, tz=2, span=1,
                           out_path=out)
        n = len(data.get("listing", []))
        print(f"  [{i}/{len(todo)}] {d} -> {n} minutes")
        if n < 2000:
            print(f"    WARNING: short listing for {d}")
        time.sleep(1.0)  # be polite to the PLA server

    total = len(glob.glob(f"{RAW}/lb_*.json"))
    print(f"done — {total} LB files in {RAW}/ (+ {len(glob.glob('../lb_days/lb_*.json'))} in ../lb_days)")


if __name__ == "__main__":
    main()
