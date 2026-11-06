# Backyordi Data

Automated data feed for the Backyordi FPL Draft league analytics dashboard.

`fpl_data.json` is regenerated twice a day by GitHub Actions and read directly
by the dashboard. Do not edit it by hand — it will be overwritten.

| File | What it does |
|---|---|
| `build_data.py` | Pulls the FPL Draft + classic APIs, builds squads, free agents, waiver targets, trades, key fixtures, standings |
| `risk.py` | Per-gameweek history → distribution stats (mean, sd, floor, ceiling, dud rate) |
| `strategy.py` | Merges the two, picks a favourite/underdog-aware XI, rewrites `fpl_data.json` |

Schedule: 07:00 and 17:00 UTC daily. Manual run: Actions tab → Update FPL data → Run workflow.
