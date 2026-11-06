#!/usr/bin/env python3
"""
Per-gameweek history -> distribution stats -> a risk-aware projection.

The lesson from the Rice/Dunk call: a single expected-points number hides the
two things that actually decide a start/sit:
  1. minutes risk  - will he be on the pitch at all, and for how long
  2. outcome spread - how wide is the range of plausible scores

So every player gets a mean AND a standard deviation, split into
"when he started" versus "overall", because those differ enormously for
rotation risks and that difference is the whole story.

Writes player_history.json (per-GW series + distribution stats for every player).
"""
import json, os, statistics, urllib.request
from collections import defaultdict

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_history.json")
UA = {"User-Agent": "Mozilla/5.0"}
os.makedirs(RAW, exist_ok=True)


def _finished_gameweeks():
    """How many gameweeks have actually been played. Never hardcode this -
    a fixed range silently stops updating the moment the season moves on."""
    req = urllib.request.Request(
        "https://draft.premierleague.com/api/game", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        g = json.loads(r.read().decode())
    cur = g["current_event"] or 1
    # the current gameweek counts only once it has finished
    return list(range(1, cur + 1)) if g.get("current_event_finished") else list(range(1, cur))


GWS = _finished_gameweeks() or [1]


def load_or_fetch(gw):
    p = os.path.join(RAW, f"live_{gw}.json")
    if os.path.exists(p):
        return json.load(open(p))
    req = urllib.request.Request(
        f"https://draft.premierleague.com/api/event/{gw}/live", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    json.dump(d, open(p, "w"))
    return d


series = defaultdict(list)
for gw in GWS:
    live = load_or_fetch(gw)
    for pid, rec in live["elements"].items():
        s = rec["stats"]
        series[int(pid)].append({
            "gw": gw,
            "pts": s["total_points"],
            "min": s["minutes"],
            "start": s.get("starts", 0),
            "g": s["goals_scored"], "a": s["assists"],
            "bonus": s["bonus"], "bps": s["bps"],
            "cs": s["clean_sheets"], "gc": s["goals_conceded"],
            "defcon": s.get("defensive_contribution", 0),
            "xgi": round(float(s.get("expected_goal_involvements") or 0), 2),
        })


def pct(vals, q):
    if not vals:
        return 0.0
    v = sorted(vals)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def describe(rows):
    pts = [r["pts"] for r in rows]
    started = [r for r in rows if r["start"] == 1]
    sp = [r["pts"] for r in started]
    played = [r for r in rows if r["min"] > 0]
    n = len(rows)

    def sd(v):
        return round(statistics.stdev(v), 2) if len(v) > 1 else 0.0

    mean_all = round(statistics.mean(pts), 2) if pts else 0.0
    mean_start = round(statistics.mean(sp), 2) if sp else 0.0
    return {
        "gws": n,
        "series": [r["pts"] for r in rows],
        "minutes_series": [r["min"] for r in rows],
        # minutes risk
        "starts": len(started),
        "start_rate": round(len(started) / n, 2) if n else 0,
        "appearance_rate": round(len(played) / n, 2) if n else 0,
        "blank_gws": sum(1 for r in rows if r["min"] == 0),
        "avg_mins": round(statistics.mean([r["min"] for r in rows]), 1) if rows else 0,
        # outcome distribution - overall
        "mean": mean_all,
        "sd": sd(pts),
        "cv": round(sd(pts) / mean_all, 2) if mean_all > 0 else 0.0,
        "median": pct(pts, 0.5),
        "floor_p20": pct(pts, 0.20),
        "ceiling_p80": pct(pts, 0.80),
        "best": max(pts) if pts else 0,
        "worst": min(pts) if pts else 0,
        # outcome distribution - conditional on starting
        "mean_when_starting": mean_start,
        "sd_when_starting": sd(sp),
        "floor_when_starting": pct(sp, 0.20),
        "ceiling_when_starting": pct(sp, 0.80),
        # shape
        "hauls_8plus": sum(1 for p in pts if p >= 8),
        "returns_6plus": sum(1 for p in pts if p >= 6),
        "duds_2orless": sum(1 for p in pts if p <= 2),
        "haul_rate": round(sum(1 for p in pts if p >= 8) / n, 2) if n else 0,
        "dud_rate": round(sum(1 for p in pts if p <= 2) / n, 2) if n else 0,
        # trend: last 3 vs first 5
        "last3_mean": round(statistics.mean(pts[-3:]), 2) if len(pts) >= 3 else mean_all,
        "trend": round(statistics.mean(pts[-3:]) - statistics.mean(pts[:-3]), 2)
                 if len(pts) >= 6 else 0.0,
    }


hist = {str(pid): describe(rows) for pid, rows in series.items() if rows}
json.dump(hist, open(OUT, "w"), separators=(",", ":"))
print(f"wrote {OUT} ({os.path.getsize(OUT)/1024:.0f} KB, {len(hist)} players)")
