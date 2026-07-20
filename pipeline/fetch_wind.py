#!/usr/bin/env python3
"""
fetch_wind — hourly wind history (speed / gust / direction, mph) covering every
session date in manifest.csv, at two corridor points:

  putney  51.467, -0.216   (LRC / Putney reach)
  barnes  51.472, -0.256   (Corney reach / Barnes bend)

Two Open-Meteo endpoints, both free and keyless:
  - archive API  : anything older than ~6 days (ERA5, ~5-day publication lag),
                   one call per point covering the whole span.
  - forecast API : the recent tail, via past_days=7 (validated against the
                   15 Jul calibration session: 9.6 mph sustained, gust 19.7,
                   dir 46 degrees — matches the on-water report).

Files land in raw/wind/. Run from model/:  python3 fetch_wind.py
"""
import csv
import json
import urllib.request
from datetime import date, timedelta

POINTS = {"putney": (51.467, -0.216), "barnes": (51.472, -0.256)}
HOURLY = "wind_speed_10m,wind_gusts_10m,wind_direction_10m"
COMMON = f"hourly={HOURLY}&wind_speed_unit=mph&timezone=Europe%2FLondon"


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def session_dates(manifest="raw/strava/manifest.csv"):
    out = set()
    with open(manifest) as f:
        for row in csv.DictReader(f):
            if row["status"].strip() == "kept":
                out.add(date.fromisoformat(row["date"]))
    return sorted(out)


def main():
    dates = session_dates()
    lo, hi = dates[0], dates[-1]
    cutoff = date.today() - timedelta(days=6)  # archive publication lag
    print(f"sessions {lo} .. {hi}; archive up to {cutoff}")

    for name, (lat, lng) in POINTS.items():
        base = f"latitude={lat}&longitude={lng}&{COMMON}"

        # 1) archive: one call for the whole historical span
        a_end = min(hi, cutoff)
        url = (f"https://archive-api.open-meteo.com/v1/archive?{base}"
               f"&start_date={lo}&end_date={a_end}")
        d = get_json(url)
        path = f"raw/wind/archive_{name}.json"
        with open(path, "w") as f:
            json.dump(d, f)
        print(f"  {name}: archive {lo}..{a_end} -> "
              f"{len(d['hourly']['time'])} hours -> {path}")

        # 2) forecast tail: past 7 days + today (covers post-cutoff sessions)
        url = (f"https://api.open-meteo.com/v1/forecast?{base}"
               f"&past_days=7&forecast_days=1")
        d = get_json(url)
        path = f"raw/wind/recent_{name}.json"
        with open(path, "w") as f:
            json.dump(d, f)
        print(f"  {name}: recent {d['hourly']['time'][0]} .. "
              f"{d['hourly']['time'][-1]} -> {path}")

    print("done")


if __name__ == "__main__":
    main()
