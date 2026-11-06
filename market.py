#!/usr/bin/env python3
"""
Rebuild the Market data around decisions instead of statistics.

Three problems with what came before:
  1. Every candidate was paired against the single weakest player at its
     position, so the DROP column was a constant and carried no information.
  2. Every gap was +0.0 to +0.3/gw. With a projection whose rank correlation
     with reality is 0.30, that is indistinguishable from zero. The screen
     manufactured urgency it had no evidence for.
  3. Nothing used the league's transaction log, which is the one genuinely
     private-information source available.

What this adds (all new keys - nothing existing is renamed or removed, so the
front end keeps working until it is updated):
  market.actions[]        ranked add/drop cards with a MOVE/WAIT/HOLD verdict
  market.forced[]         fact-based moves (injured, suspended, stopped playing)
  market.rival_activity[] who added and dropped whom, most recent first
  market.contention{}     per player: which managers have claimed or tried
  market.my_claim_history what I have tried and whether it landed
  market.trades[]         1-for-1 swaps, only with managers far from me
  market.summary          plain-language verdict for the top of the screen
"""
import json, os, urllib.request, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LEAGUE = 50495
ME = 265384
UA = {"User-Agent": "Mozilla/5.0"}

# A gap has to clear this to be worth acting on. The backtest put the
# projection's mean absolute error at ~2.2 points per player-gameweek, so a
# sub-1.0 difference is well inside the noise. Deliberately conservative:
# the cost of a bad claim is a slot plus waiver priority.
MOVE_THRESHOLD = 1.0
WAIT_THRESHOLD = 0.5

data = json.load(open(os.path.join(HERE, "fpl_data.json")))


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read().decode())


tx = get(f"https://draft.premierleague.com/api/draft/league/{LEAGUE}/transactions")["transactions"]
bs = json.load(open(os.path.join(HERE, "raw", "draft_bootstrap.json")))
POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
teams = {t["id"]: t["short_name"] for t in bs["teams"]}
elem = {e["id"]: {"name": e["web_name"], "team": teams[e["team"]],
                  "pos": POS[e["element_type"]]} for e in bs["elements"]}

entries = {}
for row in data["table"]:
    entries[row["entry"]] = {"team": row["team"], "manager": row["manager"],
                             "rank": row["rank"]}
my_rank = next(r["rank"] for r in data["table"] if r["is_me"])


def pname(pid):
    e = elem.get(pid)
    return f"{e['name']} ({e['team']})" if e else f"#{pid}"


# ---------------------------------------------------------------- contention
# Who has gone after each player, and did they get him? A player several
# managers have chased is one this user will lose at waiver priority 7 of 8.
contention = defaultdict(lambda: {"attempts": 0, "winners": [], "losers": [], "events": []})
for t in tx:
    pid = t["element_in"]
    c = contention[pid]
    c["attempts"] += 1
    who = entries.get(t["entry"], {}).get("team", str(t["entry"]))
    (c["winners"] if t["result"] == "a" else c["losers"]).append(who)
    c["events"].append(t["event"])

# ------------------------------------------------------------ rival activity
activity = []
for t in sorted(tx, key=lambda x: x["added"], reverse=True):
    if t["result"] != "a":
        continue
    activity.append({
        "when": t["added"],
        "gameweek": t["event"],
        "manager": entries.get(t["entry"], {}).get("team", str(t["entry"])),
        "is_me": t["entry"] == ME,
        "kind": "waiver" if t["kind"] == "w" else "free agency",
        "added": pname(t["element_in"]),
        "added_id": t["element_in"],
        "dropped": pname(t["element_out"]) if t.get("element_out") else None,
        "dropped_id": t.get("element_out"),
    })

my_claims = [{"when": t["added"], "gameweek": t["event"],
              "wanted": pname(t["element_in"]),
              "result": "landed" if t["result"] == "a" else "lost",
              "kind": "waiver" if t["kind"] == "w" else "free agency",
              "beaten_by": contention[t["element_in"]]["winners"][:1] or None}
             for t in sorted(tx, key=lambda x: x["added"], reverse=True)
             if t["entry"] == ME]

# ------------------------------------------------------------------- actions
my_squad = {p["id"]: p for p in data["my_squad"]}
by_pos = defaultdict(list)
for p in data["my_squad"]:
    by_pos[p["pos"]].append(p)
for k in by_pos:
    by_pos[k].sort(key=lambda x: x["gw_mu"])        # weakest first

free = defaultdict(list)
for p in data["free_agents"]:
    free[p["pos"]].append(p)
for k in free:
    free[k].sort(key=lambda x: -x["proj"])

MIN_START = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}

actions = []
for pos in ("GKP", "DEF", "MID", "FWD"):
    # Greedy unique matching: the best available goes against my weakest at that
    # position, the second best against my second weakest, and so on. That is
    # what stops every row reading "drop Dunk".
    mine = list(by_pos[pos])
    cands = list(free[pos])
    for i, cand in enumerate(cands[:4]):
        if i >= len(mine):
            break
        drop = mine[i]
        gap = round(cand["proj"] - drop["gw_mu"], 2)
        if gap <= 0:
            continue
        c = contention.get(cand["id"], {"attempts": 0, "winners": [], "losers": []})
        contested = c["attempts"] > 0
        verdict = ("MOVE" if gap >= MOVE_THRESHOLD else
                   "WAIT" if gap >= WAIT_THRESHOLD else "HOLD")
        # A contested player is one this user will probably not win.
        if contested and verdict == "MOVE":
            verdict = "WAIT"
        r = cand.get("risk") or {}
        why = []
        if cand["minutes"] >= 700:
            why.append("plays every week")
        if cand["defcon90"] >= cand["defcon_threshold"] and cand["defcon_threshold"]:
            why.append(f"clears the {cand['defcon_threshold']}-action bonus most weeks")
        if cand["fdr"] <= 2.8:
            why.append("easy run of fixtures")
        if r.get("trend", 0) >= 2:
            why.append("scoring has picked up")
        if (drop.get("risk") or {}).get("start_rate", 1) < 0.7:
            why.append(f"{drop['name']} is a rotation risk")
        actions.append({
            "pos": pos,
            "add": cand["name"], "add_team": cand["team"], "add_id": cand["id"],
            "add_proj": cand["proj"],
            "drop": drop["name"], "drop_team": drop["team"], "drop_id": drop["id"],
            "drop_proj": drop["gw_mu"],
            "gap": gap, "verdict": verdict,
            "contested": contested,
            "contested_by": c["winners"] + c["losers"],
            "why": "; ".join(why) if why else "marginally better on the numbers",
        })
actions.sort(key=lambda a: (-{"MOVE": 2, "WAIT": 1, "HOLD": 0}[a["verdict"]], -a["gap"]))

# -------------------------------------------------------------------- forced
# Facts, not projections. These are the only genuinely non-negotiable moves.
forced = []
for p in data["my_squad"]:
    r = p.get("risk") or {}
    reason = None
    if p["status"] in ("i", "s", "u"):
        reason = {"i": "injured", "s": "suspended", "u": "unavailable"}[p["status"]]
    elif p["status"] == "d" and (p.get("chance") or 100) <= 25:
        reason = f"only {p['chance']}% likely to play"
    elif r.get("start_rate", 1) <= 0.5 and p["minutes"] > 0:
        reason = f"has started only {int(r['start_rate']*100)}% of games"
    elif r.get("ceiling_p80", 99) <= 2.0:
        reason = "has never returned more than appearance points"
    if reason:
        repl = [c for c in free[p["pos"]] if c["proj"] > p["gw_mu"]][:1]
        forced.append({
            "player": p["name"], "team": p["team"], "pos": p["pos"],
            "reason": reason, "proj": p["gw_mu"],
            "replacement": repl[0]["name"] if repl else None,
            "replacement_team": repl[0]["team"] if repl else None,
            "replacement_proj": repl[0]["proj"] if repl else None,
        })

# -------------------------------------------------------------------- trades
# Only with managers far from me in the table. A trade that helps the person
# I am chasing is worth less than the same trade with someone out of contention.
trades = []
for eid_s, info in data["all_squads"].items():
    eid = int(eid_s)
    if eid == ME:
        continue
    their_rank = entries.get(eid, {}).get("rank", 99)
    distance = abs(their_rank - my_rank)
    their = defaultdict(list)
    for p in info["squad"]:
        their[p["pos"]].append(p)
    for k in their:
        their[k].sort(key=lambda x: -x.get("proj", 0))
    for pos in ("GKP", "DEF", "MID", "FWD"):
        mine_sorted = sorted(by_pos[pos], key=lambda x: -x["gw_mu"])
        if not mine_sorted or len(their[pos]) <= MIN_START[pos]:
            continue
        my_worst = mine_sorted[-1]
        my_spare = mine_sorted[MIN_START[pos]:] if len(mine_sorted) > MIN_START[pos] else []
        for cand in their[pos][MIN_START[pos]:]:
            my_gain = round(cand.get("proj", 0) - my_worst["gw_mu"], 2)
            if my_gain < MOVE_THRESHOLD:
                continue
            their_worst = their[pos][-1]
            their_gain = round(my_worst["gw_mu"] - their_worst.get("proj", 0), 2)
            trades.append({
                "with_team": info["team"], "with_manager": info["manager"],
                "their_rank": their_rank, "table_distance": distance,
                "safe": distance >= 3,
                "pos": pos,
                "i_get": cand["name"], "i_get_team": cand["team"],
                "i_get_proj": cand.get("proj"),
                "i_give": my_worst["name"], "i_give_team": my_worst["team"],
                "i_give_proj": my_worst["gw_mu"],
                "my_gain": my_gain, "their_gain": their_gain,
                "they_accept": their_gain > 0,
            })
trades.sort(key=lambda t: (-int(t["safe"]), -int(t["they_accept"]), -t["my_gain"]))
seen = set()
trades = [t for t in trades if not (t["with_team"], t["i_get"]) in seen
          and not seen.add((t["with_team"], t["i_get"]))][:6]

# ------------------------------------------------------------------- summary
moves = [a for a in actions if a["verdict"] == "MOVE"]
if forced:
    summary = (f"{len(forced)} player{'s' if len(forced)>1 else ''} on your roster "
               f"{'are' if len(forced)>1 else 'is'} not playing. Fix that first.")
elif moves:
    m = moves[0]
    summary = f"One move worth making: {m['add']} for {m['drop']}, about +{m['gap']}/gw."
else:
    summary = ("No move worth making this week. Nothing available beats what you "
               "have by more than the model's own margin of error.")

data["market"] = {
    "threshold": MOVE_THRESHOLD,
    "summary": summary,
    "actions": actions[:8],
    "forced": forced,
    "trades": trades,
    "rival_activity": activity[:25],
    "my_claim_history": my_claims,
    "contention": {str(k): {"attempts": v["attempts"], "winners": v["winners"],
                            "losers": v["losers"]}
                   for k, v in contention.items() if v["attempts"] > 0},
    "stats": {
        "total_transactions": len(tx),
        "accepted": sum(1 for t in tx if t["result"] == "a"),
        "denied": sum(1 for t in tx if t["result"] != "a"),
        "my_attempts": len(my_claims),
        "my_failures": sum(1 for c in my_claims if c["result"] == "lost"),
    },
}

json.dump(data, open(os.path.join(HERE, "fpl_data.json"), "w"), separators=(",", ":"))

print(f"SUMMARY: {summary}")
print(f"\nactions ({len(actions)}):")
for a in actions[:8]:
    flag = "  CONTESTED" if a["contested"] else ""
    print(f"  [{a['verdict']:<4}] {a['add']:<14}({a['add_team']},{a['pos']}) for "
          f"{a['drop']:<12} +{a['gap']:<5}{flag}")
    print(f"         why: {a['why']}")
print(f"\nforced ({len(forced)}):")
for f in forced:
    print(f"  {f['player']:<12} {f['reason']} -> {f['replacement']}")
print(f"\ntrades ({len(trades)}):")
for t in trades:
    print(f"  {'SAFE' if t['safe'] else 'rival'} {t['with_team'][:16]:<16} rank{t['their_rank']} "
          f"get {t['i_get']:<12} give {t['i_give']:<12} me +{t['my_gain']} them {t['their_gain']:+} "
          f"{'(they accept)' if t['they_accept'] else '(they refuse)'}")
print(f"\nmy claim history: {len(my_claims)} attempts, "
      f"{sum(1 for c in my_claims if c['result']=='lost')} lost")
for c in my_claims[:6]:
    print(f"  GW{c['gameweek']} {c['kind']:<12} {c['wanted']:<22} {c['result']}"
          + (f" (to {c['beaten_by'][0]})" if c['result'] == 'lost' and c['beaten_by'] else ""))
