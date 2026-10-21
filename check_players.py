"""
Sanity checks for players.json. Run it in the folder with players.json (and ./cache):

  python3 check_players.py

Prints problems (bad team codes, impossible game counts, broken totals, suspicious
season rows, ring counts that don't match the champions in the data) and, if the
cached player list from build_players.py is there, who's missing compared to it.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

CHAMPIONS = {1970:"MIL",1971:"LAL",1972:"NYK",1973:"BOS",1974:"GSW",1975:"BOS",1976:"POR",1977:"WAS",1978:"OKC",1979:"LAL",1980:"BOS",1981:"LAL",1982:"PHI",1983:"BOS",1984:"LAL",1985:"BOS",1986:"LAL",1987:"LAL",1988:"DET",1989:"DET",1990:"CHI",1991:"CHI",1992:"CHI",1993:"HOU",1994:"HOU",1995:"CHI",1996:"CHI",1997:"CHI",1998:"SAS",1999:"LAL",2000:"LAL",2001:"LAL",2002:"SAS",2003:"DET",2004:"SAS",2005:"MIA",2006:"SAS",2007:"BOS",2008:"LAL",2009:"LAL",2010:"DAL",2011:"MIA",2012:"MIA",2013:"SAS",2014:"GSW",2015:"CLE",2016:"GSW",2017:"GSW",2018:"TOR",2019:"LAL",2020:"MIL",2021:"GSW",2022:"DEN",2023:"BOS",2024:"OKC"}   # season start year -> franchise; newer seasons come from the data

TEAMS = {"ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL",
         "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR",
         "UTA", "WAS"}


def main():
    players = json.loads(Path("players.json").read_text())
    issues = defaultdict(list)
    ids = Counter(p["id"] for p in players)
    issues["duplicate ids"] = [i for i, c in ids.items() if c > 1]

    roster = defaultdict(list)   # (year, team) -> playoff players, to find each year's champion
    for p in players:
        for s in p["ps"]:
            roster[(s[0], s[1])].append(p)
    champs = {y: (1, t) for y, t in CHAMPIONS.items()}
    for (y, t), ps in roster.items():
        if y in CHAMPIONS:
            continue
        share = sum(1 for p in ps if p["aw"].get("CH")) / len(ps)
        games = max(sum(s[2] for s in p["ps"] if s[0] == y and s[1] == t) for p in ps)
        if len(ps) >= 5 and share >= 0.9 and (share, games) > champs.get(y, (0, 0, ""))[:2]:
            champs[y] = (share, games, t)
    champs = {y: v[-1] for y, v in champs.items()}

    for p in players:
        name = f'{p["n"]} ({p["id"]})'
        if not p["n"].strip() or p["n"].startswith("Player "):
            issues["missing name"].append(name)
        bad = {s[1] for s in p["s"] + p["ps"]} - TEAMS
        if bad:
            issues["unknown team code"].append(f"{name}: {sorted(bad)}")
        if [s[0] for s in p["s"]] != sorted(s[0] for s in p["s"]):
            issues["seasons out of order"].append(name)
        if p["gp"] != sum(s[2] for s in p["s"]) or p["from"] != p["s"][0][0] or p["to"] != p["s"][-1][0]:
            issues["totals don't match seasons"].append(name)
        per_year, po_year = Counter(), Counter()
        for s in p["s"]:
            per_year[s[0]] += s[2]
            if s[2] <= 0 or min(s[3:]) < 0:
                issues["zero or negative stats"].append(f"{name}: {s}")
            if s[0] < 1986 and s[2] >= 3 and s[4] == 0 and s[5] == 0:
                issues["old season with no rebounds or assists (likely misattributed games)"].append(f"{name}: {s}")
        for s in p["ps"]:
            po_year[s[0]] += s[2]
        for y, g in per_year.items():
            if g > 85:
                issues["more than 85 games in a season (fine for a mid-season trade, wrong if 90+)"].append(
                    f"{name} {y}: {g} games, {[s[1] for s in p['s'] if s[0] == y]}")
        for y, g in po_year.items():
            if g > 28:
                issues["more than 28 playoff games in a year"].append(f"{name} {y}: {g}")
        titles = sum(1 for s in p["ps"] if champs.get(s[0]) == s[1])
        if p["aw"].get("CH", 0) < titles:
            issues["fewer rings than titles in the data"].append(f'{name}: CH {p["aw"].get("CH", 0)}, {titles} titles')

    names = defaultdict(list)
    for p in players:
        names[p["n"]].append(p)
    shared = {n: ps for n, ps in names.items() if len(ps) > 1}
    overlap = [n for n, ps in shared.items()
               if any(a is not b and {(s[0], s[1]) for s in a["s"]} & {(s[0], s[1]) for s in b["s"]}
                      for a in ps for b in ps)]

    print(f"{len(players)} players, {sum(len(p['s']) for p in players)} team-seasons, "
          f"{len({s[0] for p in players for s in p['s']})} seasons, "
          f"champions found for {len(champs)} seasons.")
    print(f"{len(shared)} names shared by different players (normal: two Larry Johnsons)"
          + (f"; same name on the same team in the same season (check they're really two people): {overlap}"
             if overlap else "."))
    for kind, rows in issues.items():
        if rows:
            print(f"\n{kind}: {len(rows)}")
            for r in rows[:12]:
                print(f"  {r}")
            if len(rows) > 12:
                print(f"  ... {len(rows) - 12} more")
    if not any(issues.values()):
        print("\nNo problems found.")

    lists = sorted(Path("cache").glob("all_*.json"))
    if not lists:
        print("\n(No cache/all_*.json here, so I can't list who's missing compared to the old player list.)")
        return
    raw = json.loads(lists[-1].read_text())
    rs = next(r for r in raw["resultSets"] if r["name"] == "CommonAllPlayers")
    pool = [dict(zip(rs["headers"], row)) for row in rs["rowSet"]]
    pool = [p for p in pool if p.get("GAMES_PLAYED_FLAG") == "Y" and str(p.get("TO_YEAR", "0")).isdigit()
            and int(p["TO_YEAR"]) >= 1970]
    have = set(ids)
    missing = [p for p in pool if p["PERSON_ID"] not in have]
    extra = len(have - {p["PERSON_ID"] for p in pool})
    try:
        empty = json.loads(Path("cache/empty_answers.json").read_text())
    except (OSError, ValueError):
        empty = {}
    print(f"\nOld player list ({lists[-1].name}): {len(pool)} players. Missing from players.json: {len(missing)}."
          + (f" In players.json but not the old list: {extra}." if extra else ""))
    for p in sorted(missing, key=lambda p: (int(p["FROM_YEAR"]), p["DISPLAY_FIRST_LAST"])):
        pid, first, last = p["PERSON_ID"], int(p["FROM_YEAR"]), int(p["TO_YEAR"])
        why = []
        if first <= 1984 and last >= 1977:
            why.append("played 1977-84: should come back when the per-player re-fetch finishes")
        if empty.get(f"career_{pid}"):
            why.append(f"PlayerCareerStats came back empty {empty[f'career_{pid}']}x")
        print(f"  {pid:>8}  {p['DISPLAY_FIRST_LAST']:<26} {first}-{last}  {'; '.join(why)}")


if __name__ == "__main__":
    sys.exit(main())
