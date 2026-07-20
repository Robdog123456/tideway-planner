#!/usr/bin/env python3
"""
Tideway single-scull row-window engine.
Inputs : lb_days/lb_2026-07-*.json  (PLA London Bridge gauge_data, per-day, minute heights)
         wind.json                   (Open-Meteo hourly wind for Putney, mph)
Method : derive HW/LW from LB minute data -> convert to Putney (PLA offsets)
         -> 30-min grid 04:30-20:30 for 15-21 Jul -> tide light + wind light + overall.
Output : grid.json  (+ printed HW/LW cross-check and per-day green-window summary)

Putney (Bridge) secondary-port offsets vs London Bridge (PLA average constants):
  HW time +31 min, HW height -1.0 m ; LW time +98 min, LW height -0.5 m.
Heights are metres above chart datum. Putney height at a slot = piecewise-cosine between
the Putney-adjusted HW/LW events (accuracy ~+-0.3 m); the tide LIGHT is driven by the
Putney tide-clock (HW/LW times), which come straight from the offset conversion.
"""
import json, glob, math
from datetime import datetime, timedelta

# ---------- load LB minute data (stitched, deduped, 1-min) ----------
def load_minutes():
    seen = {}
    for f in sorted(glob.glob('lb_days/lb_2026-07-*.json')):
        for r in json.load(open(f))['listing']:
            dt = datetime.strptime(r['date'] + ' ' + r['time'], '%d/%m/%Y %H:%M')
            seen[dt] = float(r['height'])
    return sorted(seen.items())  # [(dt, h_LB)]

# ---------- derive HW/LW turning points from the minute series ----------
def find_extrema(series):
    n = len(series); W = 15  # 30-min centred slope window
    def slope(i):
        a = max(0, i - W); b = min(n - 1, i + W)
        return series[b][1] - series[a][1]
    raw = []; last_sign = 0
    for i in range(n):
        s = slope(i)
        sign = 1 if s > 0.005 else (-1 if s < -0.005 else 0)
        if sign == 0:
            continue
        if last_sign and sign != last_sign:
            typ = 'HW' if last_sign > 0 else 'LW'
            a = max(0, i - 75); b = min(n - 1, i + 75)
            j = max(range(a, b + 1), key=lambda k: series[k][1]) if typ == 'HW' \
                else min(range(a, b + 1), key=lambda k: series[k][1])
            raw.append([series[j][0], series[j][1], typ])
        last_sign = sign
    cleaned = []
    for e in raw:
        if cleaned and (e[0] - cleaned[-1][0]).total_seconds() < 3 * 3600:
            if e[2] == cleaned[-1][2]:
                if (e[2] == 'HW' and e[1] > cleaned[-1][1]) or (e[2] == 'LW' and e[1] < cleaned[-1][1]):
                    cleaned[-1] = e
            continue
        cleaned.append(e)
    return cleaned  # [[dt, h_LB, 'HW'/'LW']]

# ---------- London Bridge -> Putney ----------
def to_putney(extrema):
    ev = []
    for dt, h, typ in extrema:
        if typ == 'HW':
            ev.append([dt + timedelta(minutes=31), round(h - 1.0, 2), 'HW'])
        else:
            ev.append([dt + timedelta(minutes=98), round(h - 0.5, 2), 'LW'])
    return sorted(ev, key=lambda e: e[0])

# ---------- wind ----------
def load_wind():
    w = json.load(open('wind.json'))['hourly']
    out = []
    for i, t in enumerate(w['time']):
        out.append((datetime.strptime(t, '%Y-%m-%dT%H:%M'),
                    w['wind_speed_10m'][i], w['wind_gusts_10m'][i], w['wind_direction_10m'][i]))
    return out

def wind_at(wind, dt):
    if dt <= wind[0][0]: return wind[0][1], wind[0][2], wind[0][3]
    if dt >= wind[-1][0]: return wind[-1][1], wind[-1][2], wind[-1][3]
    for k in range(len(wind) - 1):
        t0, s0, g0, d0 = wind[k]; t1, s1, g1, d1 = wind[k + 1]
        if t0 <= dt <= t1:
            f = (dt - t0).total_seconds() / (t1 - t0).total_seconds()
            spd = s0 + (s1 - s0) * f; gust = g0 + (g1 - g0) * f
            dnear = d0 if f < 0.5 else d1
            return spd, gust, dnear
    return wind[-1][1], wind[-1][2], wind[-1][3]

def angdiff(a, b):
    return abs((a - b + 180) % 360 - 180)

# ---------- grid ----------
FLOOD_TO = 250   # current heading (deg) on the flood (upstream, WSW) - approx course axis
EBB_TO   = 70    # current heading on the ebb (downstream, ENE)

def brackets(events, dt):
    prev = nxt = None
    for e in events:
        if e[0] <= dt: prev = e
        elif nxt is None: nxt = e; break
    return prev, nxt

def hhmm(dt): return dt.strftime('%H:%M')
def hm(mins):
    s = '-' if mins < 0 else ''
    mins = abs(int(round(mins)))
    return f'{s}{mins//60}:{mins%60:02d}'

def sunrise_min(day):   # approx BST sunrise, Putney, mid-Jul (gets later ~1.3 min/day)
    return 5 * 60 + 4 + int((day - 15) * 1.3)

def build():
    series = load_minutes()
    extrema = find_extrema(series)
    pev = to_putney(extrema)
    wind = load_wind()

    # cross-check derived LB extrema vs the official PLA table (first day)
    tbl = json.load(open('lb_days/lb_2026-07-15.json'))['table']['0']['rows']['0']
    print('=== HW/LW cross-check, 15 Jul, London Bridge (derived vs PLA table) ===')
    d15 = [e for e in extrema if e[0].strftime('%d/%m') == '15/07']
    for e in d15:
        print(f'  derived {e[2]} {hhmm(e[0])} {e[1]:.2f}m')
    print('  table  :', ', '.join(f"{'HW' if r['Type']==1 else 'LW'} {r['Time'][:2]}:{r['Time'][2:]} {r['Height']}m" for r in tbl))

    days = [15, 16, 17, 18, 19, 20, 21]
    grid = {}
    for day in days:
        rows = []
        srise = sunrise_min(day)
        for slot in range(4 * 60 + 30, 20 * 60 + 30 + 1, 30):
            dt = datetime(2026, 7, day, slot // 60, slot % 60)
            prev, nxt = brackets(pev, dt)
            if not prev or not nxt:
                continue
            flooding = (prev[2] == 'LW' and nxt[2] == 'HW')
            # height (piecewise cosine between Putney events)
            tau = (dt - prev[0]).total_seconds() / (nxt[0] - prev[0]).total_seconds()
            h = prev[1] + (nxt[1] - prev[1]) * (1 - math.cos(math.pi * tau)) / 2
            # tide-clock + tide light (low-water avoidance band)
            if flooding:
                since_lw = (dt - prev[0]).total_seconds() / 60.0     # prev is LW
                to_hw = (nxt[0] - dt).total_seconds() / 60.0
                if since_lw < 120:   tide = 'RED'
                elif since_lw < 150: tide = 'AMBER'
                else:                tide = 'GREEN'
                status = f'Flooding (LW+{hm(since_lw)} / HW-{hm(to_hw)})'
                key_to_turn = min(since_lw, to_hw)
            else:  # ebbing (prev HW, next LW)
                since_hw = (dt - prev[0]).total_seconds() / 60.0
                to_lw = (nxt[0] - dt).total_seconds() / 60.0
                if to_lw <= 60:    tide = 'RED'
                elif to_lw <= 120: tide = 'AMBER'
                else:              tide = 'GREEN'
                status = f'Ebbing (HW+{hm(since_hw)} / LW-{hm(to_lw)})'
                key_to_turn = min(since_hw, to_lw)
            nearest = prev if (dt - prev[0]) <= (nxt[0] - dt) else nxt
            if key_to_turn <= 25:
                status = 'High water (slack)' if nearest[2] == 'HW' else 'Low water (slack)'
            # wind  (SUSTAINED speed is the driver, calibrated to ground truth:
            # Wed 15 Jul 06:30-07:45 was FINE at sustained ~10 mph / gusts 18-21 / NE on the
            # ebb -> ~10 mph sustained = AMBER, gusts alone don't close it, and light
            # wind-over-tide only steepens conditions once it is already properly breezy.)
            spd, gust, wdir = wind_at(wind, dt)
            stream_running = key_to_turn > 45
            cur_to = FLOOD_TO if flooding else EBB_TO
            wot = stream_running and spd >= 6 and angdiff(wdir, cur_to) <= 50
            if spd <= 8:      windl = 'GREEN'
            elif spd <= 13:   windl = 'AMBER'
            else:             windl = 'RED'
            if gust >= 32:    windl = 'RED'                                   # violent gusts regardless of mean
            if wot and spd >= 13 and windl == 'AMBER': windl = 'RED'         # WoT bites only when already breezy
            # overall
            if tide == 'RED' or windl == 'RED':      overall = "Don't row"
            elif tide == 'AMBER' or windl == 'AMBER': overall = 'Caution'
            else:                                     overall = 'ROW'
            # notes
            notes = []
            if tide == 'RED':  notes.append('low water - do not boat (black-flag rule)')
            if wot:            notes.append('wind against tide')
            if gust >= 25:     notes.append(f'gusty ({gust:.0f} mph)')
            if slot < srise:   notes.append('before sunrise (dark)')
            rows.append({
                'time': f'{slot//60:02d}:{slot%60:02d}',
                'height_putney_m': round(h, 2),
                'tide_status': status,
                'flooding': flooding,
                'wind_mph': round(spd), 'gust_mph': round(gust),
                'wind_dir': int(wdir), 'wind_dir_txt': deg_txt(wdir),
                'wind_over_tide': wot,
                'tide_light': tide, 'wind_light': windl, 'overall': overall,
                'notes': '; '.join(notes),
            })
        grid[f'2026-07-{day:02d}'] = rows

    # Putney HW/LW per day for summary/method
    putney_hwlw = {}
    for e in pev:
        k = e[0].strftime('%Y-%m-%d')
        putney_hwlw.setdefault(k, []).append({'type': e[2], 'time': hhmm(e[0]), 'height_m': e[1]})

    json.dump({'grid': grid, 'putney_hwlw': putney_hwlw}, open('grid.json', 'w'), indent=1)
    summary(grid, putney_hwlw, days)

def deg_txt(d):
    dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
    return dirs[int((d % 360) / 22.5 + 0.5) % 16]

def runs(rows, pred):
    out = []; start = None; last = None
    for r in rows:
        if pred(r):
            if start is None: start = r['time']
            last = r['time']
        else:
            if start is not None: out.append((start, last)); start = None
    if start is not None: out.append((start, last))
    return out

def fmt(wins):
    # widen each slot to its 30-min block end so a lone 05:30 reads 05:30-06:00
    def end(t):
        h, m = int(t[:2]), int(t[3:]); m += 30
        return f'{h + m//60:02d}:{m%60:02d}'
    return ', '.join(f'{a}-{end(b)}' for a, b in wins) if wins else '-'

def summary(grid, putney_hwlw, days):
    print('\n=== PER-DAY WINDOWS (Putney, BST) ===')
    dname = {15:'Wed',16:'Thu',17:'Fri',18:'Sat',19:'Sun',20:'Mon',21:'Tue'}
    for day in days:
        k = f'2026-07-{day:02d}'; rows = grid[k]
        tide_str = '  '.join(f"{e['type']} {e['time']}({e['height_m']}m)" for e in putney_hwlw.get(k, []))
        rowable = fmt(runs(rows, lambda r: r['overall'] != "Don't row"))
        ideal   = fmt(runs(rows, lambda r: r['overall'] == 'ROW'))
        print(f'{dname[day]} {day} Jul | tides: {tide_str}')
        print(f'         Rowable (green+amber): {rowable}')
        print(f'         Ideal   (calm/green) : {ideal}')

if __name__ == '__main__':
    build()
