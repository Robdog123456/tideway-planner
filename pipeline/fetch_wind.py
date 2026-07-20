#!/usr/bin/env python3
"""
fetch_wind — Open-Meteo wind (speed / gust / direction, mph) at the two
corridor points the model uses:

  putney  51.467, -0.216   (LRC / Putney reach — used below 3.5 km chainage)
  barnes  51.472, -0.256   (Corney reach / Barnes bend — used above)

Two modes:

  python3 fetch_wind.py           # HISTORICAL (backtest support, unchanged):
                                  # ERA5 archive + recent tail for every
                                  # session date -> raw/wind/ (local-data)

  python3 fetch_wind.py --live    # LIVE (the scheduled pipeline):
                                  #   forecast: past 2 days + 14 days ahead,
                                  #             both points -> data/wind/
                                  #   ensemble: 40-member ICON, 7 days,
                                  #             putney -> data/wind/
                                  # forecast failure = exit 1 (run fails,
                                  # last good grid stays published);
                                  # ensemble failure = warning only
                                  # (it only powers the confidence badges).

All endpoints are free and keyless.
"""
import argparse
import csv
import json
import os
import time
import urllib.request
from datetime import date, timedelta

POINTS = {"putney": (51.467, -0.216), "barnes": (51.472, -0.256)}
HOURLY = "wind_speed_10m,wind_gusts_10m,wind_direction_10m"
COMMON = f"hourly={HOURLY}&wind_speed_unit=mph&timezone=Europe%2FLondon"
LIVE = "data/wind"


def get_json(url, attempts=2):
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"    attempt {attempt} failed: {e}")
            time.sleep(5)
    return None


# ---------------------------------------------------------------- live mode
def live():
    os.makedirs(LIVE, exist_ok=True)

    # 1) deterministic forecast, both points — the model's wind series
    for name, (lat, lng) in POINTS.items():
        url = (f"https://api.open-meteo.com/v1/forecast?"
               f"latitude={lat}&longitude={lng}&{COMMON}"
               f"&past_days=2&forecast_days=14")
        d = get_json(url)
        if not d or not d.get("hourly", {}).get("time"):
            print(f"FORECAST FAILED for {name} — failing the run")
            raise SystemExit(1)
        with open(f"{LIVE}/forecast_{name}.json", "w") as f:
            json.dump(d, f)
        h = d["hourly"]["time"]
        print(f"  {name}: forecast {h[0]} .. {h[-1]} ({len(h)} hours)")

    # 2) 40-member ICON ensemble, putney only — the confidence badges
    lat, lng = POINTS["putney"]
    url = (f"https://ensemble-api.open-meteo.com/v1/ensemble?"
           f"latitude={lat}&longitude={lng}"
           f"&hourly=wind_speed_10m&models=icon_seamless&forecast_days=7"
           f"&wind_speed_unit=mph&timezone=Europe%2FLondon")
    d = get_json(url)
    if d and d.get("hourly", {}).get("time"):
        with open(f"{LIVE}/ensemble_putney.json", "w") as f:
            json.dump(d, f)
        members = [k for k in d["hourly"] if k.startswith("wind_speed_10m")]
        print(f"  putney: ensemble {len(members)} members, "
              f"{len(d['hourly']['time'])} hours")
    else:
        print("  WARNING: ensemble fetch failed — confidence badges will be "
              "missing this cycle (not fatal)")


# ---------------------------------------------- historical mode (unchanged)
def session_dates(manifest="raw/strava/manifest.csv"):
    out = set()
    with open(manifest) as f:
        for row in csv.DictReader(f):
            if row["status"].strip() == "kept":
                out.add(date.fromisoformat(row["date"]))
    return sorted(out)


def historical():
    dates = session_dates()
    lo, hi = dates[0], dates[-1]
    cutoff = date.today() - timedelta(days=6)  # archive publication lag
    print(f"sessions {lo} .. {hi}; archive up to {cutoff}")
    for name, (lat, lng) in POINTS.items():
        base = f"latitude={lat}&longitude={lng}&{COMMON}"
        a_end = min(hi, cutoff)
        d = get_json(f"https://archive-api.open-meteo.com/v1/archive?{base}"
                     f"&start_date={lo}&end_date={a_end}")
        with open(f"raw/wind/archive_{name}.json", "w") as f:
            json.dump(d, f)
        print(f"  {name}: archive {lo}..{a_end} -> "
              f"{len(d['hourly']['time'])} hours")
        d = get_json(f"https://api.open-meteo.com/v1/forecast?{base}"
                     f"&past_days=7&forecast_days=1")
        with open(f"raw/wind/recent_{name}.json", "w") as f:
            json.dump(d, f)
        print(f"  {name}: recent {d['hourly']['time'][0]} .. "
              f"{d['hourly']['time'][-1]}")
    print("done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="forecast + ensemble for the scheduled pipeline")
    a = ap.parse_args()
    live() if a.live else historical()
