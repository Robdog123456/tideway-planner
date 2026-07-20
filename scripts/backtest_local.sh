#!/bin/bash
# backtest_local.sh — the pre-push gate. Run this before pushing ANY model change.
#
# It proves two things, in order:
#   1. --check    : the calibrated v1 lights are reproduced exactly (0 diffs)
#   2. --backtest : all 29 real sessions still score rowable-or-policy-only
#
# Needs local-data/ (raw GPS + historical tides/wind), so it only runs on
# Rob's Mac — CI runs the --check half only.
set -e
cd "$(dirname "$0")/../pipeline"

echo "== gate 1/2: v1 regression (compute_v2.py --check) =="
python3 compute_v2.py --check

echo
echo "== gate 2/2: 29-session backtest (session_model.py --backtest) =="
python3 session_model.py --backtest

echo
echo "ALL GATES GREEN"
