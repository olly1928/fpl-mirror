#!/usr/bin/env python3
"""
Fetch the FPL API and write compact CSV files.
Runs in GitHub Actions, which has open internet access.
Output lands in a public repo, which Claude's sandbox CAN reach.
"""
import json, urllib.request, datetime, pathlib

BASE = "https://fantasy.premierleague.com/api"
OUT = pathlib.Path("data")
OUT.mkdir(exist_ok=True)

def get(path):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"User-Agent": "Mozilla/5.0 (fpl-mirror)", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
clean = lambda s: str(s or "").replace(",", " ").replace("\n", " ").strip()

boot = get("/bootstrap-static/")
fixtures = get("/fixtures/")

events = boot["events"]
current = next((e for e in events if e.get("is_current")), None)
nxt = next((e for e in events if e.get("is_next")), None) \
      or next((e for e in events if not e.get("finished")), None)
season_year = int(events[0]["deadline_time"][:4])
season = f"{season_year}/{str(season_year + 1)[2:]}"
teams = {t["id"]: t["short_name"] for t in boot["teams"]}

meta = {
    "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "season": season,
    "preseason": current is None,
    "current_event": current["id"] if current else None,
    "next_event": nxt["id"] if nxt else None,
    "next_deadline": nxt["deadline_time"] if nxt else None,
    "player_count": len(boot["elements"]),
    "teams_in_game": sorted(teams.values()),
}
(OUT / "meta.json").write_text(json.dumps(meta, indent=2))

header = (
    f"# season={meta['season']} preseason={meta['preseason']} "
    f"next_gw={meta['next_event']} deadline={meta['next_deadline']} "
    f"players={meta['player_count']} fetched={meta['fetched_at']}"
)

# ---- players.csv -------------------------------------------------
rows = []
for e in boot["elements"]:
    rows.append({
        "id": e["id"], "name": clean(e["web_name"]), "team": teams[e["team"]],
        "pos": POS[e["element_type"]], "price": e["now_cost"] / 10,
        "own": e["selected_by_percent"], "pts": e["total_points"],
        "ppg": e["points_per_game"], "mins": e["minutes"],
        "g": e["goals_scored"], "a": e["assists"], "cs": e["clean_sheets"],
        "bonus": e["bonus"], "st": e["status"],
        "news": clean(e.get("news")), "chance": e.get("chance_of_playing_next_round"),
    })
rows.sort(key=lambda r: -r["pts"])

cols = ["id", "name", "team", "pos", "price", "own", "pts", "ppg",
        "mins", "g", "a", "cs", "bonus", "st"]
lines = [
    header,
    "# pts/ppg/mins/g/a/cs/bonus are LAST SEASON totals carried into the new game.",
    "# st: a=available i=injured d=doubtful s=suspended u=unavailable",
    ",".join(cols),
]
lines += [",".join(str(r[c]) for c in cols) for r in rows]

alerts = [r for r in rows if r["st"] != "a"]
if alerts:
    lines += ["", "# ALERTS — id,name,team,status,chance,news"]
    lines += [f"# {r['id']},{r['name']},{r['team']},{r['st']},"
              f"{r['chance'] if r['chance'] is not None else ''},{r['news']}"
              for r in alerts]
(OUT / "players.csv").write_text("\n".join(lines))

# ---- fdr.csv — per-team difficulty over the next 6 GWs ------------
start = meta["next_event"] or 1
n = 6
tbl = {s: [] for s in teams.values()}
for f in fixtures:
    gw = f.get("event")
    if gw is None or gw < start or gw >= start + n:
        continue
    tbl[teams[f["team_h"]]].append((gw, teams[f["team_a"]], True, f["team_h_difficulty"]))
    tbl[teams[f["team_a"]]].append((gw, teams[f["team_h"]], False, f["team_a_difficulty"]))

summary = []
for team, fx in tbl.items():
    fx.sort()
    avg = round(sum(d for *_, d in fx) / len(fx), 2) if fx else None
    seq = " ".join(f"{o if h else o.lower()}({d})" for _, o, h, d in fx)
    summary.append((avg if avg is not None else 9, team, len(fx), avg, seq))
summary.sort()

fdr = [header, f"# difficulty GW{start}-{start + n - 1}, easiest first",
       "# opponents: UPPER=home lower=away, (n)=difficulty",
       "team,games,avg_difficulty,opponents"]
fdr += [f"{t},{g},{a},{s}" for _, t, g, a, s in summary]
(OUT / "fdr.csv").write_text("\n".join(fdr))

# ---- fixtures.csv — full season ----------------------------------
fx = [header, "gw,home,away,h_diff,a_diff,kickoff"]
for f in sorted((x for x in fixtures if x.get("event")), key=lambda x: x["event"]):
    fx.append(f"{f['event']},{teams[f['team_h']]},{teams[f['team_a']]},"
              f"{f['team_h_difficulty']},{f['team_a_difficulty']},"
              f"{(f.get('kickoff_time') or '')[:10]}")
(OUT / "fixtures.csv").write_text("\n".join(fx))

print(f"OK — {season}, GW{meta['next_event']}, {len(rows)} players")
for p in sorted(OUT.iterdir()):
    print(f"  {p}  {p.stat().st_size/1024:.1f} KB")
