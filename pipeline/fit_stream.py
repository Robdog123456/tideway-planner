#!/usr/bin/env python3
"""
fit_stream — estimate the tidal stream per reach per tide-phase from Rob's own
GPS passes (passes.csv, produced by analyze_tracks.py).

Model
  sog(pass) = V(session) + dir * s(reach, phase_bin)

  V(session)        Rob's through-the-water pace that day (unknown per session
                    — absorbs boat, effort, weather drag)
  dir               +1 heading upstream, -1 heading downstream
  s(reach, bin)     stream speed SIGNED POSITIVE UPSTREAM (flood +, ebb -),
                    binned by hours since local HW

Because both unknowns enter linearly, alternating least squares converges in a
few iterations without any numeric libraries: fix s -> each V is the mean of
(sog - dir*s); fix V -> each s is the mean of dir*(sog - V).

Outputs
  stream_model.json   {reach: {bin: {s_mps, n}}, sessions: {id: V}, iters, rmse}
  fig/stream_curves.png (if matplotlib is available)
"""
import csv
import json
import os
from collections import defaultdict

PHASE_BINS = [(-6.5 + i, -5.5 + i) for i in range(13)]   # -6.5..+6.5 h vs HW


def bin_of(ph):
    for i, (a, b) in enumerate(PHASE_BINS):
        if a <= ph < b:
            return i
    return None


def main():
    rows = []
    with open("passes.csv") as f:
        for r in csv.DictReader(f):
            if not r["phase_hr_vs_HW"] or not r["sog_med"]:
                continue
            b = bin_of(float(r["phase_hr_vs_HW"]))
            if b is None or int(r["n_pts"]) < 5:
                continue
            rows.append({
                "sess": r["activity"], "reach": r["reach"],
                "dir": 1.0 if r["direction"] == "up" else -1.0,
                "bin": b, "sog": float(r["sog_med"]),
            })
    print(f"{len(rows)} usable reach-passes")

    V = defaultdict(lambda: 3.3)      # m/s starting guess
    S = defaultdict(float)            # (reach, bin) -> stream, +ve upstream

    for it in range(60):
        # V step
        acc = defaultdict(list)
        for r in rows:
            acc[r["sess"]].append(r["sog"] - r["dir"] * S[(r["reach"], r["bin"])])
        newV = {k: sum(v) / len(v) for k, v in acc.items()}
        # S step
        acc = defaultdict(list)
        for r in rows:
            acc[(r["reach"], r["bin"])].append(r["dir"] * (r["sog"] - newV[r["sess"]]))
        newS = {k: sum(v) / len(v) for k, v in acc.items()}
        shift = max([abs(newV[k] - V[k]) for k in newV] +
                    [abs(newS[k] - S[k]) for k in newS])
        V, S = defaultdict(lambda: 3.3, newV), defaultdict(float, newS)
        if shift < 1e-4:
            break

    resid = [r["sog"] - V[r["sess"]] - r["dir"] * S[(r["reach"], r["bin"])]
             for r in rows]
    rmse = (sum(x * x for x in resid) / len(resid)) ** 0.5

    counts = defaultdict(int)
    for r in rows:
        counts[(r["reach"], r["bin"])] += 1

    model = defaultdict(dict)
    for (reach, b), s in sorted(S.items()):
        centre = (PHASE_BINS[b][0] + PHASE_BINS[b][1]) / 2
        model[reach][f"{centre:+.0f}"] = {
            "s_mps": round(s, 3), "n": counts[(reach, b)]}

    out = {"model": model,
           "sessions_V_mps": {k: round(v, 3) for k, v in sorted(V.items())},
           "iterations": it + 1, "rmse_mps": round(rmse, 3),
           "sign_convention": "positive = stream pushing UPSTREAM (flood)"}
    with open("stream_model.json", "w") as f:
        json.dump(out, f, indent=1)
    print(f"stream_model.json written (iters={it+1}, rmse={rmse:.3f} m/s)")
    print(f"session V range: {min(V.values()):.2f}..{max(V.values()):.2f} m/s")

    for reach in model:
        pts = ", ".join(f"{k}h:{v['s_mps']:+.2f}({v['n']})"
                        for k, v in sorted(model[reach].items(),
                                           key=lambda kv: float(kv[0])))
        print(f"  {reach:<10} {pts}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        os.makedirs("fig", exist_ok=True)
        fig, ax = plt.subplots(figsize=(9, 5))
        for reach, bins in model.items():
            xs = sorted(float(k) for k in bins)
            ys = [bins[f"{x:+.0f}"]["s_mps"] for x in xs]
            ax.plot(xs, ys, marker="o", label=reach)
        ax.axhline(0, color="#999", lw=0.7)
        ax.set_xlabel("hours vs local HW")
        ax.set_ylabel("stream m/s (+ = upstream/flood)")
        ax.set_title("Tideway stream by reach and tide phase — from Rob's GPS")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig("fig/stream_curves.png", dpi=150)
        print("fig/stream_curves.png written")
    except ImportError:
        print("matplotlib unavailable — skipped figure")


if __name__ == "__main__":
    main()
