#!/usr/bin/env python3
"""
derive_reach_lw — one-off calibration: how much later is LW (and HW) at the
top of the corridor than at Putney?

Method: fetch PLA minute predictions for London Bridge (0113) and Richmond
(0116) — plus Chelsea (0113A) on one day as a method check — for a spring, a
neap and a mid-range day picked from data/tides_extrema.json; distill each to
HW/LW events with the SAME find_extrema used everywhere else; pair events
across gauges (same type, nearest within 4 h) and report the time offsets.

The reach-local LW clock then interpolates linearly in chainage between
Putney (LB +98 min, the validated v1 offset) and the Richmond gauge.

Run from pipeline/ on the Mac (PLA 403s CI runners — same constraint as
mac_tide_push.sh). Responses are cached in raw/tides_reach/ so re-runs make
NO network calls. Writes data/reach_lw_offsets.json with full provenance.

Richmond caveat checked explicitly: gauge 0116 sits at Richmond Lock, where
the half-tide sluices hold the level upstream. If the predicted curve showed
a flat artificial LW hold, the LW pairing would be invalid — the script
prints each gauge's LW-neighbourhood curvature so this is inspected, not
assumed.
"""
import json
import os
from datetime import datetime, timedelta

from tideway_lib import (GAUGES, fetch_gauge, find_extrema, haversine_m,
                         PUTNEY_LW, PUTNEY_HW)

CACHE = "raw/tides_reach"
OUT = "data/reach_lw_offsets.json"

# Along-river chainage of the gauges on the model's Putney-origin axis.
# Putney = 0 by definition. Richmond Lock: centreline ends at Isleworth Ait
# (11.86 km, 51.4718 -0.3171); the lock (51.4630 -0.3245) is one gentle bend
# further — straight-line distance + 10% sinuosity allowance, documented as
# an estimate (+-0.7 km moves the Kew-end clock by ~+-2 min, well inside the
# band widths).
CENTRELINE_END = (51.4718, -0.3171)
CENTRELINE_END_CHAIN = 11856.0
RICHMOND_LOCK = (51.4630, -0.3245)


def pick_days(n_days=21):
    """Spring (max daily range), neap (min) and mid day from the next n_days."""
    with open("data/tides_extrema.json") as f:
        ev = [(datetime.fromisoformat(e["t"]), e["h"], e["type"])
              for e in json.load(f)["events"]]
    today = datetime.now().date()
    daily = {}
    for t, h, typ in ev:
        d = t.date()
        if today + timedelta(days=1) <= d <= today + timedelta(days=n_days):
            hs = daily.setdefault(d, [])
            hs.append(h)
    ranges = sorted((max(hs) - min(hs), d) for d, hs in daily.items()
                    if len(hs) >= 3)
    neap, spring = ranges[0][1], ranges[-1][1]
    mid = ranges[len(ranges) // 2][1]
    return {"spring": spring, "neap": neap, "mid": mid}


def gauge_events(gauge_key, day):
    """Minute predictions for one gauge/day (cached) -> HW/LW events."""
    os.makedirs(CACHE, exist_ok=True)
    path = f"{CACHE}/{gauge_key}_{day.isoformat()}.json"
    if not os.path.exists(path):
        print(f"  fetching PLA {gauge_key} {day} ...")
        fetch_gauge(GAUGES[gauge_key], day.year, day.month, day.day,
                    tz=2, span=1, out_path=path)
    with open(path) as f:
        d = json.load(f)
    series = sorted(
        (datetime.strptime(r["date"] + " " + r["time"], "%d/%m/%Y %H:%M"),
         float(r["height"]))
        for r in {(r["date"], r["time"]): r for r in d["listing"]}.values())
    return find_extrema(series), series


def lw_shape(series, events):
    """Curvature check near each LW: rise 30 min either side of the minimum.
    A natural LW rises a few cm+ both sides; a sluice-held flat does not."""
    out = []
    idx = {t: i for i, (t, _) in enumerate(series)}
    for t, h, typ in events:
        if typ != "LW" or t not in idx:
            continue
        i = idx[t]
        lo, hi = max(0, i - 30), min(len(series) - 1, i + 30)
        out.append({"t": t.strftime("%m-%d %H:%M"),
                    "rise_before_cm": round((series[lo][1] - h) * 100),
                    "rise_after_cm": round((series[hi][1] - h) * 100)})
    return out


def pair_offsets(base_ev, up_ev, typ):
    """For each base-gauge event of `typ`, nearest same-type upstream event
    in (-1 h, +4 h); returns offset minutes (upstream minus base)."""
    outs = []
    for t, h, ty in base_ev:
        if ty != typ:
            continue
        best = None
        for tu, hu, tyu in up_ev:
            if tyu != typ:
                continue
            d = (tu - t).total_seconds() / 60.0
            if -60 < d < 240 and (best is None or abs(d) < abs(best)):
                best = d
        if best is not None:
            outs.append(round(best, 1))
    return outs


def main():
    days = pick_days()
    print("calibration days:", {k: v.isoformat() for k, v in days.items()})

    per_day = {}
    shapes = {}
    for label, day in days.items():
        lb_ev, _ = gauge_events("london_bridge", day)
        ri_ev, ri_series = gauge_events("richmond", day)
        shapes[f"richmond_{label}"] = lw_shape(ri_series, ri_ev)
        per_day[label] = {
            "day": day.isoformat(),
            "lw_offset_min": pair_offsets(lb_ev, ri_ev, "LW"),
            "hw_offset_min": pair_offsets(lb_ev, ri_ev, "HW"),
        }
        if label == "mid":     # method check on one day only (call discipline)
            ch_ev, ch_series = gauge_events("chelsea", day)
            shapes["chelsea_mid"] = lw_shape(ch_series, ch_ev)
            per_day["chelsea_check"] = {
                "day": day.isoformat(),
                "lw_offset_min": pair_offsets(lb_ev, ch_ev, "LW"),
                "hw_offset_min": pair_offsets(lb_ev, ch_ev, "HW"),
            }

    lw_all = sorted(x for k, v in per_day.items() if k != "chelsea_check"
                    for x in v["lw_offset_min"])
    hw_all = sorted(x for k, v in per_day.items() if k != "chelsea_check"
                    for x in v["hw_offset_min"])
    # Richmond Lock half-tide hold: on neap troughs the sluice profile is
    # nearly flat (rise ~0-3 cm/30 min — see lw_shape_check), so find_extrema
    # timestamps the artificial hold, not the tidal LW. Every sub-100-min
    # sample came from held troughs (all four on the neap day); the natural
    # cluster sits at 123-134 (+ two late flat-trough stragglers). Use the
    # median of samples >= 100 min as the NATURAL propagation offset — it
    # also matches PLA's published LB->Richmond differences (~+2h10 LW,
    # ~+1h HW).
    lw_natural = sorted(x for x in lw_all if x >= 100)
    med = lambda xs: xs[len(xs) // 2] if xs else None
    rich_chain = (CENTRELINE_END_CHAIN
                  + 1.1 * haversine_m(CENTRELINE_END, RICHMOND_LOCK))

    out = {
        "derived": datetime.now().isoformat(timespec="seconds"),
        "method": "PLA minute predictions LB vs Richmond, find_extrema, "
                  "paired same-type events; spring/neap/mid days",
        "days": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                 for k, v in days.items()},
        "richmond_chainage_m_est": round(rich_chain),
        "putney_offset_vs_lb_min": {"LW": PUTNEY_LW[0], "HW": PUTNEY_HW[0]},
        "richmond_offset_vs_lb_min": {"LW": med(lw_natural), "HW": med(hw_all),
                                      "lw_samples_all": lw_all,
                                      "lw_samples_natural": lw_natural,
                                      "hw_samples": hw_all,
                                      "lw_note": "samples <100 min excluded: "
                                      "Richmond Lock half-tide hold flattens "
                                      "neap troughs (see lw_shape_check)"},
        "lw_slope_min_per_m": round((med(lw_natural) - PUTNEY_LW[0])
                                    / rich_chain, 6),
        "per_day": per_day,
        "lw_shape_check": shapes,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: out[k] for k in
                      ("richmond_chainage_m_est", "putney_offset_vs_lb_min",
                       "richmond_offset_vs_lb_min")}, indent=1))
    print("chelsea method check:", per_day.get("chelsea_check"))
    print("richmond LW shape:", shapes.get("richmond_mid")
          or next(iter(shapes.values())))
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
