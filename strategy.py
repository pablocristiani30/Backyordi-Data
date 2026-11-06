#!/usr/bin/env python3
"""
Merge distribution stats into the dataset and pick an XI that is aware of
whether I am the favourite or the underdog this week.

The rule, stated plainly:
  If I am projected to WIN comfortably, I want the narrowest range of outcomes.
  Variance can only hurt me - I am already ahead, so a boom adds a win I was
  getting anyway while a bust hands over a win I should have had.
  If I am projected to LOSE, the opposite. A safe floor just loses quietly.
  I need variance, because only the upper tail of the range beats them.

  risk_score = expected_points - k * standard_deviation
      k = +0.4  when clear favourite   (penalise spread)
      k =  0.0  when the tie is close  (just maximise points)
      k = -0.4  when clear underdog    (reward spread)
"""
import json, os, itertools
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(HERE, "fpl_data.json")))
hist = json.load(open(os.path.join(HERE, "player_history.json")))

MIN_START, MAX_START = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}, {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}


def enrich(p):
    h = hist.get(str(p["id"]))
    if not h:
        p["risk"] = None
        return p
    p["risk"] = {k: h[k] for k in (
        "series", "minutes_series", "mean", "sd", "cv", "median", "floor_p20",
        "ceiling_p80", "best", "worst", "start_rate", "appearance_rate",
        "blank_gws", "avg_mins", "mean_when_starting", "sd_when_starting",
        "floor_when_starting", "ceiling_when_starting", "haul_rate", "dud_rate",
        "hauls_8plus", "duds_2orless", "last3_mean", "trend")}
    return p


for key in ("my_squad", "free_agents"):
    data[key] = [enrich(p) for p in data[key]]
if data.get("opponent"):
    data["opponent"]["squad"] = [enrich(p) for p in data["opponent"]["squad"]]
for eid in data["all_squads"]:
    data["all_squads"][eid]["squad"] = [enrich(p) for p in data["all_squads"][eid]["squad"]]

tf = data["team_fixtures"]


def gw_fixture(p):
    f = tf.get(p["team"], [])
    return f[0] if f else None


def expected_and_sd(p):
    """Expected points and spread for THIS gameweek, fixture-adjusted."""
    r = p.get("risk")
    if not r or not r.get("series"):
        return p.get("proj", 0.0), 3.0
    f = gw_fixture(p)
    tilt = 1 + (3.0 - f["fdr"]) * 0.12 if f else 0.6
    # availability: injury flag multiplies on top of observed start rate
    if p["status"] in ("i", "s", "u"):
        avail = 0.0
    elif p["status"] == "d":
        avail = (p["chance"] if p["chance"] is not None else 50) / 100
    else:
        avail = 1.0
    p_start = r["start_rate"] * avail
    mu_start = r["mean_when_starting"] * tilt
    sd_start = r["sd_when_starting"] or 2.0
    # mixture of "starts" and "doesn't start" (treated as ~0.6 pts, sd 1)
    mu = p_start * mu_start + (1 - p_start) * 0.6
    var = (p_start * (sd_start ** 2 + mu_start ** 2)
           + (1 - p_start) * (1.0 + 0.36)) - mu ** 2
    return round(mu, 2), round(max(var, 0.25) ** 0.5, 2)


for p in data["my_squad"]:
    p["gw_mu"], p["gw_sd"] = expected_and_sd(p)
if data.get("opponent"):
    for p in data["opponent"]["squad"]:
        p["gw_mu"], p["gw_sd"] = expected_and_sd(p)


def pick_xi(squad, k):
    by = defaultdict(list)
    for p in squad:
        by[p["pos"]].append(p)
    for pos in by:
        by[pos].sort(key=lambda x: -(x["gw_mu"] - k * x["gw_sd"]))
    best = None
    for d in range(MIN_START["DEF"], MAX_START["DEF"] + 1):
        for m in range(MIN_START["MID"], MAX_START["MID"] + 1):
            for f in range(MIN_START["FWD"], MAX_START["FWD"] + 1):
                if 1 + d + m + f != 11:
                    continue
                if len(by["DEF"]) < d or len(by["MID"]) < m or len(by["FWD"]) < f or not by["GKP"]:
                    continue
                xi = by["GKP"][:1] + by["DEF"][:d] + by["MID"][:m] + by["FWD"][:f]
                score = sum(x["gw_mu"] - k * x["gw_sd"] for x in xi)
                if best is None or score > best["score"]:
                    best = {"score": round(score, 2), "formation": f"{d}-{m}-{f}", "xi": xi,
                            "mu": round(sum(x["gw_mu"] for x in xi), 1),
                            "sd": round(sum(x["gw_sd"] ** 2 for x in xi) ** 0.5, 1)}
    return best


opp_mu = sum(p["gw_mu"] for p in sorted(data["opponent"]["squad"],
             key=lambda x: -x["gw_mu"])[:11]) if data.get("opponent") else 0
neutral = pick_xi(data["my_squad"], 0.0)
margin = neutral["mu"] - opp_mu
combined_sd = (neutral["sd"] ** 2 + 8.0 ** 2) ** 0.5

if margin > 1.0 * combined_sd:
    posture, k = "clear favourite", 0.4
elif margin < -1.0 * combined_sd:
    posture, k = "clear underdog", -0.4
else:
    posture, k = "close tie", 0.0

chosen = pick_xi(data["my_squad"], k)

data["risk_model"] = {
    "posture": posture,
    "k": k,
    "my_projected": neutral["mu"],
    "opp_projected": round(opp_mu, 1),
    "margin": round(margin, 1),
    "combined_sd": round(combined_sd, 1),
    "explanation": {
        "clear favourite": "Projected to win by more than a typical weekly swing. Minimise spread - take the high floor, bench the lottery tickets.",
        "close tie": "Too close to call. Just maximise expected points.",
        "clear underdog": "Projected to lose by more than a typical weekly swing. A safe floor loses quietly - take variance, start the boom-or-bust players.",
    }[posture],
}
data["my_xi"] = {"formation": chosen["formation"], "proj": chosen["mu"],
                 "sd": chosen["sd"], "ids": [p["id"] for p in chosen["xi"]],
                 "risk_adjusted_score": chosen["score"]}

# ---- recompute key fixtures against the XI we actually chose.
# build_data.py ranked them against its own unadjusted XI, so without this the
# app shows fixtures for players who are no longer starting.
opp_xi_ids = set(data["opponent"]["xi"]["ids"]) if data.get("opponent") else set()
my_xi_ids = set(data["my_xi"]["ids"])
by_id = {p["id"]: p for p in data["my_squad"]}
opp_by_id = {p["id"]: p for p in (data["opponent"]["squad"] if data.get("opponent") else [])}

for f in data["key_fixtures"]:
    sides = {f["home"], f["away"]}
    mine = [by_id[i] for i in my_xi_ids if by_id[i]["team"] in sides]
    theirs = [opp_by_id[i] for i in opp_xi_ids if opp_by_id[i]["team"] in sides]
    f["my_points"] = round(sum(p["gw_mu"] for p in mine), 1)
    f["opp_points"] = round(sum(p["gw_mu"] for p in theirs), 1)
    f["swing"] = round(f["my_points"] + f["opp_points"], 1)
    f["net"] = round(f["my_points"] - f["opp_points"], 1)
    f["my_players"] = [{"name": p["name"], "pos": p["pos"], "proj": p["gw_mu"]}
                       for p in sorted(mine, key=lambda x: -x["gw_mu"])]
    f["opp_players"] = [{"name": p["name"], "pos": p["pos"], "proj": p["gw_mu"]}
                        for p in sorted(theirs, key=lambda x: -x["gw_mu"])]
data["key_fixtures"] = [f for f in data["key_fixtures"] if f["swing"] > 0]
data["key_fixtures"].sort(key=lambda x: -x["swing"])


# ---- "what do I actually want to happen in this match?"
# A fixture is not simply good or bad for me. I may hold a defender on one side
# and a striker on the other, and my opponent holds players too. Work out, per
# team, whether a clean sheet or goals helps me, and net it into one sentence.
def _side_breakdown(players, team):
    on = [p for p in players if p["team"] == team]
    return {
        "defensive": [p["name"] for p in on if p["pos"] in ("GKP", "DEF")],
        "attacking": [p["name"] for p in on if p["pos"] in ("MID", "FWD")],
        "points": round(sum(p["gw_mu"] for p in on), 1),
    }


for f in data["key_fixtures"]:
    mine_full = [by_id[i] for i in my_xi_ids if by_id[i]["team"] in (f["home"], f["away"])]
    theirs_full = [opp_by_id[i] for i in opp_xi_ids if opp_by_id[i]["team"] in (f["home"], f["away"])]
    sides = {}
    for side_key, team in (("home", f["home"]), ("away", f["away"])):
        m = _side_breakdown(mine_full, team)
        t = _side_breakdown(theirs_full, team)
        # Net interest in this team keeping a clean sheet vs scoring goals
        cs_net = len(m["defensive"]) - len(t["defensive"])
        goal_net = len(m["attacking"]) - len(t["attacking"])
        sides[side_key] = {
            "team": team, "mine": m, "theirs": t,
            "want_clean_sheet": cs_net, "want_goals": goal_net,
        }
    wants = []
    for sk in ("home", "away"):
        s = sides[sk]
        if s["want_clean_sheet"] > 0:
            wants.append(f"{s['team']} clean sheet")
        elif s["want_clean_sheet"] < 0:
            wants.append(f"{s['team']} to concede")
        if s["want_goals"] > 0:
            wants.append(f"{s['team']} goals")
        elif s["want_goals"] < 0:
            wants.append(f"{s['team']} kept quiet")
    f["sides"] = sides
    f["root_for"] = " · ".join(wants) if wants else "Nothing riding on this one"
    f["verdict"] = ("Good for you" if f["net"] > 2 else
                    "Bad for you" if f["net"] < -2 else "Cuts both ways")

json.dump(data, open(os.path.join(HERE, "fpl_data.json"), "w"), separators=(",", ":"))

print(f"POSTURE: {posture}  (margin {margin:+.1f} vs typical swing {combined_sd:.1f})  k={k}")
print(f"Me {neutral['mu']} (sd {neutral['sd']})   Opponent {opp_mu:.1f}")
print()
print(f"NEUTRAL XI   {neutral['formation']}  mu {neutral['mu']}  sd {neutral['sd']}")
print(f"CHOSEN XI    {chosen['formation']}  mu {chosen['mu']}  sd {chosen['sd']}")
nids = {p['id'] for p in neutral['xi']}
cids = {p['id'] for p in chosen['xi']}
if nids != cids:
    for p in chosen['xi']:
        if p['id'] not in nids:
            print(f"   risk model ADDS    {p['name']} (mu {p['gw_mu']}, sd {p['gw_sd']})")
    for p in neutral['xi']:
        if p['id'] not in cids:
            print(f"   risk model REMOVES {p['name']} (mu {p['gw_mu']}, sd {p['gw_sd']})")
else:
    print("   same XI either way")
print()
print(f"{'':4}{'player':<13}{'mu':>6}{'sd':>6}{'floor':>7}{'ceil':>6}{'dud%':>6}{'strt':>6}")
for p in sorted(data['my_squad'], key=lambda x: -(x['gw_mu'] - k*x['gw_sd'])):
    r = p.get('risk') or {}
    mark = 'XI ' if p['id'] in cids else '   '
    print(f"{mark:4}{p['name']:<13}{p['gw_mu']:>6}{p['gw_sd']:>6}{r.get('floor_p20',0):>7}"
          f"{r.get('ceiling_p80',0):>6}{r.get('dud_rate',0):>6}{r.get('start_rate',0):>6}")
print()
print(f"file now {os.path.getsize(os.path.join(HERE,'fpl_data.json'))/1024:.0f} KB")
