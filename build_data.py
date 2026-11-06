#!/usr/bin/env python3
"""
Backyordi (FPL Draft league 50495) data generator.

Pulls every endpoint, computes all the analytics, and writes ONE json file
that the Lovable front end renders directly. All maths lives here, so the
app does no calculation - it just draws what this produces.

Run:  python3 build_data.py
Out:  fpl_data.json
"""
import json, urllib.request, datetime, os
from collections import defaultdict

LEAGUE_ID = 50495
MY_ENTRY = 265384          # Easy FC - Pablo Cristiani
HORIZON = 5                # gameweeks to look ahead
UA = {"User-Agent": "Mozilla/5.0"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fpl_data.json")

DRAFT = "https://draft.premierleague.com/api/"
CLASSIC = "https://fantasy.premierleague.com/api/"


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# ----------------------------------------------------------------- fetch
game = get(DRAFT + "game")
bs = get(DRAFT + "bootstrap-static")
ld = get(DRAFT + f"league/{LEAGUE_ID}/details")
es = get(DRAFT + f"league/{LEAGUE_ID}/element-status")["element_status"]
cb = get(CLASSIC + "bootstrap-static/")
fixtures = get(CLASSIC + "fixtures/")

NEXT_GW = game["next_event"] or game["current_event"]
PLAYED = max(game["current_event"], 1)

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
SCORING = bs["settings"]["scoring"]
SQUAD = bs["settings"]["squad"]

dteams = {t["id"]: t for t in bs["teams"]}
cteams = {t["id"]: t for t in cb["teams"]}
cname2id = {t["name"]: t["id"] for t in cb["teams"]}
cshort = {t["id"]: t["short_name"] for t in cb["teams"]}
d2c = {d: cname2id.get(t["name"]) for d, t in dteams.items()}

owner = {e["element"]: e["owner"] for e in es}
avail = {e["element"]: e["status"] for e in es}

entries = {le["entry_id"]: le for le in ld["league_entries"]}
eid2entry = {le["id"]: le["entry_id"] for le in ld["league_entries"]}
standings = {eid2entry[s["league_entry"]]: s for s in ld["standings"]}

# ------------------------------------------------------- fixture horizon
fut = defaultdict(list)
for f in fixtures:
    ev = f.get("event")
    if ev is None or ev < NEXT_GW or ev >= NEXT_GW + HORIZON:
        continue
    fut[f["team_h"]].append({"gw": ev, "opp": f["team_a"], "home": True,
                             "fdr": f["team_h_difficulty"]})
    fut[f["team_a"]].append({"gw": ev, "opp": f["team_h"], "home": False,
                             "fdr": f["team_a_difficulty"]})
for t in fut:
    fut[t].sort(key=lambda x: x["gw"])


def fixture_list(cid):
    return [{"gw": f["gw"], "opp": cshort.get(f["opp"], "?"),
             "home": f["home"], "fdr": f["fdr"]} for f in fut.get(cid, [])]


def fdr_avg(cid):
    fl = fut.get(cid, [])
    if not fl:
        return 5.0
    diffs = [f["fdr"] for f in fl]
    blanks = HORIZON - len(fl)
    return (sum(diffs) + 5 * blanks) / HORIZON


# ------------------------------------------------------------- players
players = {}
for e in bs["elements"]:
    cid = d2c.get(e["team"])
    mins = e["minutes"]
    per90 = (lambda v: round(v / mins * 90, 2) if mins >= 90 else 0.0)
    p = {
        "id": e["id"],
        "name": e["web_name"],
        "full_name": f"{e['first_name']} {e['second_name']}".strip(),
        "team": dteams[e["team"]]["short_name"],
        "team_name": dteams[e["team"]]["name"],
        "pos": POS[e["element_type"]],
        "pos_id": e["element_type"],
        "points": e["total_points"],
        "ppg": float(e["points_per_game"] or 0),
        "minutes": mins,
        "starts": e.get("starts", 0),
        "start_rate": round(e.get("starts", 0) / PLAYED, 2) if PLAYED else 0,
        "mins_per_gw": round(mins / PLAYED, 1) if PLAYED else 0,
        "form": float(e["form"] or 0),
        "goals": e["goals_scored"],
        "assists": e["assists"],
        "bonus": e["bonus"],
        "bps": e["bps"],
        "clean_sheets": e["clean_sheets"],
        "saves": e.get("saves", 0),
        "xg": round(float(e.get("expected_goals") or 0), 2),
        "xa": round(float(e.get("expected_assists") or 0), 2),
        "xgi": round(float(e.get("expected_goal_involvements") or 0), 2),
        "xgc": round(float(e.get("expected_goals_conceded") or 0), 2),
        "defcon": e.get("defensive_contribution", 0) or 0,
        "tackles": e.get("tackles", 0),
        "recoveries": e.get("recoveries", 0),
        "cbi": e.get("clearances_blocks_interceptions", 0),
        "ict": float(e.get("ict_index") or 0),
        "influence": float(e.get("influence") or 0),
        "creativity": float(e.get("creativity") or 0),
        "threat": float(e.get("threat") or 0),
        "yellow": e["yellow_cards"],
        "red": e["red_cards"],
        "status": e["status"],
        "news": e.get("news", "") or "",
        "chance": e.get("chance_of_playing_next_round"),
        "pens_order": e.get("penalties_order"),
        "set_pieces": e.get("corners_and_indirect_freekicks_text", "") or "",
        "owner": owner.get(e["id"]),
        "available": avail.get(e["id"]) == "a",
        "pts90": per90(e["total_points"]),
        "xgi90": per90(float(e.get("expected_goal_involvements") or 0)),
        "defcon90": per90(e.get("defensive_contribution", 0) or 0),
        "fdr": round(fdr_avg(cid), 2),
        "fixtures": fixture_list(cid),
        "cid": cid,
    }
    # defensive-contribution threshold for this position
    thr = SCORING.get(f"defensive_contribution_limit_{p['pos']}", 0)
    p["defcon_threshold"] = thr
    p["defcon_hit_rate"] = round(min(p["defcon90"] / thr, 1.5), 2) if thr else 0.0
    players[e["id"]] = p


# Positional mean scoring rate, used to shrink individual rates toward.
_pool = defaultdict(list)
for _p in players.values():
    if _p["minutes"] >= 270:
        _pool[_p["pos"]].append(_p["pts90"])
POS_MEAN_PTS90 = {k: sum(v) / len(v) for k, v in _pool.items() if v}

# Backtested on gameweeks 5-9 of this season (~1000 player-gameweeks), comparing
# rank correlation with actual points. Results:
#   minutes x tilt x heavily-shrunk rate   rho 0.30
#   minutes x tilt alone                   rho 0.30
#   last-4-gameweek average                rho 0.22
#   pts90 x minutes x tilt  (old formula)  rho 0.23
#   raw pts90 alone                        rho 0.11
# The per-90 scoring rate over a handful of games is mostly noise: dividing by a
# small minutes denominator inflates anyone who got one lucky goal. Shrinking it
# hard toward the positional mean recovers ~28% of ranking accuracy. K is the
# minutes of evidence needed before a player's own rate carries half the weight;
# anything from ~1400 up performed identically, so this is not finely tuned.
SHRINK_K = 1800


def projection(p):
    """Expected minutes x fixture tilt x a heavily shrunk scoring rate x availability.

    Deliberately simple and hand-checkable. Every term is backtested above.
    """
    if p["minutes"] < 180:
        return 0.0
    exp_min = min(p["mins_per_gw"], 90)
    tilt = 1 + (3.0 - p["fdr"]) * 0.08
    pm = POS_MEAN_PTS90.get(p["pos"], 4.0)
    w = p["minutes"] / (p["minutes"] + SHRINK_K)
    rate = w * p["pts90"] + (1 - w) * pm
    if p["status"] in ("i", "s", "u"):
        a = 0.0
    elif p["status"] == "d":
        a = (p["chance"] if p["chance"] is not None else 50) / 100
    else:
        a = 1.0
    return round((exp_min / 90) * tilt * rate * a, 2)


for p in players.values():
    p["proj"] = projection(p)

# --------------------------------------------------------------- squads
squads = {}
for entry_id in entries:
    sq = [p for p in players.values() if p["owner"] == entry_id]
    sq.sort(key=lambda x: (x["pos_id"], -x["proj"]))
    squads[entry_id] = sq


def best_xi(squad):
    """Pick the highest-projecting legal XI: 1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD."""
    by = defaultdict(list)
    for p in squad:
        by[p["pos"]].append(p)
    for k in by:
        by[k].sort(key=lambda x: -x["proj"])
    best, best_pts = None, -1
    for d in range(3, 6):
        for m in range(2, 6):
            for f in range(1, 4):
                if 1 + d + m + f != 11:
                    continue
                if len(by["DEF"]) < d or len(by["MID"]) < m or len(by["FWD"]) < f or not by["GKP"]:
                    continue
                xi = by["GKP"][:1] + by["DEF"][:d] + by["MID"][:m] + by["FWD"][:f]
                tot = sum(x["proj"] for x in xi)
                if tot > best_pts:
                    best_pts, best = tot, {"formation": f"{d}-{m}-{f}", "xi": xi,
                                           "proj": round(tot, 1)}
    return best or {"formation": "-", "xi": [], "proj": 0}


xis = {eid: best_xi(sq) for eid, sq in squads.items()}

# ------------------------------------------------------- this week's tie
my_opp = None
for m in ld["matches"]:
    if m["event"] != NEXT_GW:
        continue
    a, b = eid2entry.get(m["league_entry_1"]), eid2entry.get(m["league_entry_2"])
    if a == MY_ENTRY:
        my_opp = b
    elif b == MY_ENTRY:
        my_opp = a

# --------------------------------------------- key fixtures to watch
# A real PL match matters to me in proportion to the projected points my XI
# scores in it PLUS the projected points my opponent's XI scores in it.
my_xi_ids = {p["id"] for p in xis[MY_ENTRY]["xi"]}
opp_xi_ids = {p["id"] for p in xis[my_opp]["xi"]} if my_opp else set()

key_fixtures = []
for f in fixtures:
    if f.get("event") != NEXT_GW:
        continue
    th, ta = f["team_h"], f["team_a"]
    mine = [p for p in players.values() if p["id"] in my_xi_ids and p["cid"] in (th, ta)]
    theirs = [p for p in players.values() if p["id"] in opp_xi_ids and p["cid"] in (th, ta)]
    mp = sum(p["proj"] for p in mine)
    tp = sum(p["proj"] for p in theirs)
    if mp == 0 and tp == 0:
        continue
    key_fixtures.append({
        "home": cshort.get(th), "away": cshort.get(ta),
        "home_name": cteams[th]["name"], "away_name": cteams[ta]["name"],
        "kickoff": f.get("kickoff_time"),
        "my_points": round(mp, 1),
        "opp_points": round(tp, 1),
        "swing": round(mp + tp, 1),
        "net": round(mp - tp, 1),
        "my_players": [{"name": p["name"], "pos": p["pos"], "proj": p["proj"]} for p in sorted(mine, key=lambda x: -x["proj"])],
        "opp_players": [{"name": p["name"], "pos": p["pos"], "proj": p["proj"]} for p in sorted(theirs, key=lambda x: -x["proj"])],
    })
key_fixtures.sort(key=lambda x: -x["swing"])

# ------------------------------------------------------- free agents
free_agents = [p for p in players.values() if p["owner"] is None and p["available"]]
free_agents.sort(key=lambda x: -x["proj"])

my_squad = squads[MY_ENTRY]
weakest = {}
for pos in ("GKP", "DEF", "MID", "FWD"):
    grp = [p for p in my_squad if p["pos"] == pos]
    if grp:
        weakest[pos] = min(grp, key=lambda x: x["proj"])

waiver_targets = []
for p in free_agents:
    w = weakest.get(p["pos"])
    if not w:
        continue
    gap = round(p["proj"] - w["proj"], 2)
    if gap > 0:
        waiver_targets.append({
            "add": p["id"], "add_name": p["name"], "add_team": p["team"], "pos": p["pos"],
            "drop": w["id"], "drop_name": w["name"], "drop_team": w["team"],
            "gap": gap, "add_proj": p["proj"], "drop_proj": w["proj"],
            "add_minutes": p["minutes"], "add_pts90": p["pts90"],
            "add_xgi90": p["xgi90"], "add_defcon90": p["defcon90"], "add_fdr": p["fdr"],
        })
waiver_targets.sort(key=lambda x: -x["gap"])

# ---------------------------------------------------------- trade finder
# Who on another roster upgrades my weakest starter, and what hole do THEY have
# that I could fill? A trade needs to help both sides or it gets rejected.
my_by_pos = defaultdict(list)
for p in my_squad:
    my_by_pos[p["pos"]].append(p)
for k in my_by_pos:
    my_by_pos[k].sort(key=lambda x: -x["proj"])

trade_ideas = []
for eid, sq in squads.items():
    if eid == MY_ENTRY:
        continue
    their_by_pos = defaultdict(list)
    for p in sq:
        their_by_pos[p["pos"]].append(p)
    for k in their_by_pos:
        their_by_pos[k].sort(key=lambda x: -x["proj"])
    # their surplus = position where they have depth beyond what they can start
    for pos in ("GKP", "DEF", "MID", "FWD"):
        start_min = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}[pos]
        their = their_by_pos[pos]
        mine = my_by_pos[pos]
        if len(their) <= start_min or not mine:
            continue
        # their best benchable asset (beyond their guaranteed starters)
        for cand in their[start_min:]:
            my_worst = mine[-1]
            gain = round(cand["proj"] - my_worst["proj"], 2)
            if gain < 1.0:
                continue
            # what can I offer? my surplus at a position they are thin in
            offers = []
            for opos in ("GKP", "DEF", "MID", "FWD"):
                o_min = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}[opos]
                if len(my_by_pos[opos]) <= o_min:
                    continue
                their_depth = their_by_pos[opos]
                their_worst_starter = their_depth[o_min - 1] if len(their_depth) >= o_min else None
                for mine_spare in my_by_pos[opos][o_min:]:
                    if their_worst_starter and mine_spare["proj"] - their_worst_starter["proj"] > 0.5:
                        offers.append({"name": mine_spare["name"], "pos": opos,
                                       "proj": mine_spare["proj"],
                                       "upgrades_their": their_worst_starter["name"],
                                       "their_gain": round(mine_spare["proj"] - their_worst_starter["proj"], 2)})
            if offers:
                offers.sort(key=lambda x: -x["their_gain"])
                trade_ideas.append({
                    "with_entry": eid,
                    "with_team": entries[eid]["entry_name"],
                    "with_manager": f"{entries[eid]['player_first_name']} {entries[eid]['player_last_name']}",
                    "i_get": {"name": cand["name"], "team": cand["team"], "pos": pos, "proj": cand["proj"]},
                    "i_give": offers[0],
                    "my_gain": gain,
                    "their_gain": offers[0]["their_gain"],
                    "mutual": round(min(gain, offers[0]["their_gain"]), 2),
                })
trade_ideas.sort(key=lambda x: -x["mutual"])
seen = set()
_t = []
for t in trade_ideas:
    k = (t["with_entry"], t["i_get"]["name"])
    if k in seen:
        continue
    seen.add(k)
    _t.append(t)
trade_ideas = _t[:15]

# ----------------------------------------------------------- league table
table = []
for s in sorted(ld["standings"], key=lambda x: x["rank"]):
    eid = eid2entry[s["league_entry"]]
    gp = s["matches_won"] + s["matches_drawn"] + s["matches_lost"]
    table.append({
        "rank": s["rank"], "entry": eid,
        "team": entries[eid]["entry_name"],
        "manager": f"{entries[eid]['player_first_name']} {entries[eid]['player_last_name']}",
        "w": s["matches_won"], "d": s["matches_drawn"], "l": s["matches_lost"],
        "played": gp,
        "points_for": s["points_for"], "points_against": s["points_against"],
        "pf_per_gw": round(s["points_for"] / gp, 1) if gp else 0,
        "pa_per_gw": round(s["points_against"] / gp, 1) if gp else 0,
        "league_points": s["total"],
        "waiver_priority": entries[eid]["waiver_pick"],
        "is_me": eid == MY_ENTRY,
    })
pf_rank = {r["entry"]: i + 1 for i, r in enumerate(sorted(table, key=lambda x: -x["points_for"]))}
for r in table:
    r["scoring_rank"] = pf_rank[r["entry"]]
    r["luck"] = r["rank"] - r["scoring_rank"]   # negative = table flatters them

# ------------------------------------------------------------- remaining
remaining = []
for m in ld["matches"]:
    if m["event"] < NEXT_GW:
        continue
    a, b = eid2entry.get(m["league_entry_1"]), eid2entry.get(m["league_entry_2"])
    if MY_ENTRY in (a, b):
        opp = b if a == MY_ENTRY else a
        remaining.append({"gw": m["event"], "opponent": entries[opp]["entry_name"],
                          "manager": f"{entries[opp]['player_first_name']} {entries[opp]['player_last_name']}",
                          "their_pf_per_gw": round(standings[opp]["points_for"] /
                                                   max(1, standings[opp]["matches_won"] + standings[opp]["matches_drawn"] + standings[opp]["matches_lost"]), 1)})
remaining.sort(key=lambda x: x["gw"])

# ------------------------------------------------------------------ out
def slim(p):
    """Full detail, minus the internal id and the fixture list (now stored once per team)."""
    d = dict(p)
    d.pop("cid", None)
    d.pop("fixtures", None)
    return d


# Fixtures stored ONCE per team instead of repeated on every player object.
team_fixtures = {}
for did, t in dteams.items():
    team_fixtures[t["short_name"]] = fixture_list(d2c.get(did))

MINI_FIELDS = ("id", "name", "team", "pos", "points", "ppg", "minutes", "start_rate",
               "form", "pts90", "xgi90", "defcon90", "defcon_threshold",
               "fdr", "proj", "status", "chance", "news")


def mini(p):
    """Compact record for browsing lists - enough for a table row and a basic card."""
    return {k: p[k] for k in MINI_FIELDS}

data = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "league": {"id": LEAGUE_ID, "name": ld["league"]["name"],
               "scoring": "head-to-head", "trades_enabled": ld["league"]["trades"] == "y",
               "transaction_mode": ld["league"]["transaction_mode"],
               "size": len(entries)},
    "gameweek": {"current": game["current_event"], "next": NEXT_GW,
                 "waivers_processed": game["waivers_processed"],
                 "deadline": next((e["deadline_time"] for e in bs["events"]["data"]
                                   if e["id"] == NEXT_GW), None) if isinstance(bs["events"], dict)
                             else next((e["deadline_time"] for e in bs["events"] if e["id"] == NEXT_GW), None)},
    "me": {"entry": MY_ENTRY, "team": entries[MY_ENTRY]["entry_name"],
           "manager": f"{entries[MY_ENTRY]['player_first_name']} {entries[MY_ENTRY]['player_last_name']}",
           "waiver_priority": entries[MY_ENTRY]["waiver_pick"]},
    "scoring_rules": SCORING,
    "squad_rules": SQUAD,
    "my_squad": [slim(p) for p in my_squad],
    "my_xi": {"formation": xis[MY_ENTRY]["formation"], "proj": xis[MY_ENTRY]["proj"],
              "ids": [p["id"] for p in xis[MY_ENTRY]["xi"]]},
    "opponent": ({"entry": my_opp, "team": entries[my_opp]["entry_name"],
                  "manager": f"{entries[my_opp]['player_first_name']} {entries[my_opp]['player_last_name']}",
                  "rank": standings[my_opp]["rank"],
                  "points_for": standings[my_opp]["points_for"],
                  "squad": [slim(p) for p in squads[my_opp]],
                  "xi": {"formation": xis[my_opp]["formation"], "proj": xis[my_opp]["proj"],
                         "ids": [p["id"] for p in xis[my_opp]["xi"]]}}
                 if my_opp else None),
    "team_fixtures": team_fixtures,
    "all_squads": {str(eid): {"team": entries[eid]["entry_name"],
                              "manager": f"{entries[eid]['player_first_name']} {entries[eid]['player_last_name']}",
                              "rank": standings[eid]["rank"],
                              "xi_proj": xis[eid]["proj"],
                              "formation": xis[eid]["formation"],
                              "xi_ids": [p["id"] for p in xis[eid]["xi"]],
                              "squad": [mini(p) for p in squads[eid]]}
                   for eid in entries},
    "free_agents": [mini(p) for p in free_agents[:70]],
    "waiver_targets": waiver_targets[:30],
    "trade_ideas": trade_ideas,
    "key_fixtures": key_fixtures,
    "table": table,
    "my_remaining_fixtures": remaining,
}

with open(OUT, "w") as fh:
    json.dump(data, fh, separators=(",", ":"))

print(f"wrote {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")
print(f"GW{NEXT_GW} | me {xis[MY_ENTRY]['proj']} ({xis[MY_ENTRY]['formation']}) "
      f"vs {data['opponent']['team'] if my_opp else '?'} {xis[my_opp]['proj'] if my_opp else 0}")
print(f"{len(free_agents)} free agents | {len(waiver_targets)} upgrades | {len(trade_ideas)} trade ideas | {len(key_fixtures)} key fixtures")
