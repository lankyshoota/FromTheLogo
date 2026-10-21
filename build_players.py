"""
Deep Pull — player database builder.

Pulls every NBA player who played from the 1970-71 season on, straight from
NBA.com's official stats (through the free nba_api package), and writes
players.json for the game.

For each player you get:
  - every team, every season since 1970 (with games, points, rebounds, assists),
    regular season and playoffs
  - awards: All-Star, MVP, Finals MVP, champion, DPOY, ROY, 6MOY, All-NBA,
    All-Defensive, All-Rookie, Player of the Month
  - draft year / round / pick (or undrafted), country, position, college
  - height, weight, birthdate, jersey numbers by team

Run it on your own computer:
  pip install nba_api
  python build_players.py

It makes ~3 requests per player plus one per team roster per season (for
jersey numbers), and waits between them so NBA.com doesn't block you, so a
full run takes a couple of hours. It's resumable: every
response is cached in ./cache, so if it stops (wifi, laptop sleeps, NBA.com
hiccups) just run it again and it picks up where it left off.
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import date
from pathlib import Path

from nba_api.stats.endpoints import (
    commonallplayers,
    commonplayerinfo,
    commonteamroster,
    playerawards,
    playercareerstats,
)
from nba_api.stats.static import teams as static_teams

FIRST_SEASON = 1970          # 1970-71 is the earliest season included
SLEEP = 0.7                  # seconds between requests (be polite to NBA.com)
CACHE = Path("cache")
OUT = Path("players.json")

# Every team id -> its current abbreviation, so franchise history merges:
# Sonics -> OKC, New Jersey -> BKN, Vancouver -> MEM, Bullets -> WAS,
# Charlotte Hornets (1988-2002) -> CHA, New Orleans Hornets -> NOP, etc.
TEAM_BY_ID = {t["id"]: t["abbreviation"] for t in static_teams.get_teams()}

AWARD_KEYS = {
    "NBA All-Star": "AS",
    "NBA Champion": "CH",
    "NBA Most Valuable Player": "MVP",
    "NBA Finals Most Valuable Player": "FMVP",
    "NBA Defensive Player of the Year": "DPOY",
    "NBA Rookie of the Year": "ROY",
    "NBA Sixth Man of the Year": "6MOY",
    "All-NBA": "ALLNBA",
    "All-Defensive Team": "ALLDEF",
    "All-Rookie Team": "ALLROOK",
    "NBA Player of the Month": "POM",
}

# (team id, season start year) -> True for every team-season seen, so we can
# fetch each roster once for jersey numbers
TEAM_SEASONS = set()


def current_season() -> str:
    d = date.today()
    start = d.year if d.month >= 10 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def season_str(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def fetch(kind: str, pid, call, required=True):
    """Fetch one endpoint, caching the raw response. Retries with backoff.
    If required=False, gives up and returns None instead of exiting."""
    f = CACHE / f"{kind}_{pid}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(6):
        try:
            data = call()
            f.write_text(json.dumps(data))
            time.sleep(SLEEP + random.random() * 0.4)
            return data
        except Exception as e:  # timeouts, connection resets, 429s
            wait = 10 * (attempt + 1)
            print(f"   {kind} {pid}: {e.__class__.__name__}, retrying in {wait}s")
            time.sleep(wait)
    if not required:
        return None
    sys.exit(f"\nNBA.com stopped answering at {kind} {pid}. "
             "Wait a few minutes and rerun; finished players are cached.")


def rows(resp: dict, name: str) -> list[dict]:
    for rs in resp.get("resultSets", []):
        if rs["name"] == name:
            return [dict(zip(rs["headers"], r)) for r in rs["rowSet"]]
    return []


def build_player(p: dict) -> dict | None:
    pid = p["PERSON_ID"]

    career = fetch("career", pid, lambda: playercareerstats.PlayerCareerStats(
        player_id=pid, per_mode36="Totals", timeout=30).get_dict())
    awards = fetch("awards", pid, lambda: playerawards.PlayerAwards(
        player_id=pid, timeout=30).get_dict())
    info = fetch("info", pid, lambda: commonplayerinfo.CommonPlayerInfo(
        player_id=pid, timeout=30).get_dict())

    # seasons: one row per team per season (skip the "TOT" combined rows)
    def season_rows(name):
        out = []
        for r in rows(career, name):
            year = int(r["SEASON_ID"][:4])
            if year < FIRST_SEASON or not r["TEAM_ID"]:
                continue
            team = TEAM_BY_ID.get(r["TEAM_ID"], r["TEAM_ABBREVIATION"])
            out.append([year, team, r["GP"] or 0, r["PTS"] or 0, r["REB"] or 0, r["AST"] or 0])
            TEAM_SEASONS.add((r["TEAM_ID"], year))
        return out

    seasons = season_rows("SeasonTotalsRegularSeason")
    if not seasons:
        return None
    playoffs = season_rows("SeasonTotalsPostSeason")
    teams = []
    for s in seasons:
        if s[1] not in teams:
            teams.append(s[1])

    aw = {}
    for r in rows(awards, "PlayerAwards"):
        key = AWARD_KEYS.get(r["DESCRIPTION"])
        if key:
            aw[key] = aw.get(key, 0) + 1
            # first-team All-NBA / All-Defensive / All-Rookie get their own count too
            if key in ("ALLNBA", "ALLDEF", "ALLROOK") and str(r.get("ALL_NBA_TEAM_NUMBER")) == "1":
                aw[key + "1"] = aw.get(key + "1", 0) + 1

    i = (rows(info, "CommonPlayerInfo") or [{}])[0]
    rnd, pick, dyear = i.get("DRAFT_ROUND"), i.get("DRAFT_NUMBER"), i.get("DRAFT_YEAR")
    drafted = str(rnd).isdigit() and str(pick).isdigit()
    draft = [int(dyear), int(rnd), int(pick)] if drafted and str(dyear).isdigit() else None
    country = (i.get("COUNTRY") or "").strip()

    ht = str(i.get("HEIGHT") or "")          # "6-9" -> 81 inches
    feet, _, inches = ht.partition("-")
    height = int(feet) * 12 + int(inches) if feet.isdigit() and inches.isdigit() else None
    wt = str(i.get("WEIGHT") or "").strip()
    birth = str(i.get("BIRTHDATE") or "")[:10] or None
    last_num = str(i.get("JERSEY") or "").strip()

    gp = sum(s[2] for s in seasons)
    pts = sum(s[3] for s in seasons)

    # game-ready tags (same letters the prototype uses)
    tags = ""
    if aw.get("CH"): tags += "C"
    if aw.get("MVP"): tags += "M"
    if aw.get("AS"): tags += "A"
    if draft and draft[2] == 1: tags += "1"
    if not draft or draft[1] >= 2: tags += "R"
    if country and country.upper() != "USA": tags += "I"
    if aw.get("DPOY"): tags += "D"
    if aw.get("ROY"): tags += "Y"
    if aw.get("6MOY"): tags += "S"
    if aw.get("FMVP"): tags += "F"

    return {
        "id": pid,
        "n": p["DISPLAY_FIRST_LAST"],
        "t": teams,
        "g": tags,
        "s": seasons,
        "ps": playoffs,                  # playoff seasons, same format as "s"
        "aw": aw,
        "dr": draft,                     # [year, round, pick] or None = undrafted
        "c": country,
        "pos": i.get("POSITION") or "",
        "col": "" if str(i.get("SCHOOL")).strip() in ("None", "") else i["SCHOOL"].strip(),
        "ht": height,                    # inches
        "wt": int(wt) if wt.isdigit() else None,   # pounds
        "bd": birth,                     # "YYYY-MM-DD"
        "num": [],                       # [[team, "jersey"], ...], filled from rosters
        "_last_num": last_num,
        "from": seasons[0][0],
        "to": seasons[-1][0],
        "gp": gp,
        "ppg": round(pts / gp, 1) if gp else 0,
        "_fame": pts / 1000 + aw.get("AS", 0) * 4 + aw.get("MVP", 0) * 12
                 + aw.get("ALLNBA", 0) * 3 + aw.get("CH", 0) * 1.5 + aw.get("FMVP", 0) * 6,
    }


def main():
    CACHE.mkdir(exist_ok=True)
    season = current_season()
    print(f"Getting the full player list (through {season})...")
    everyone = fetch("all", season, lambda: commonallplayers.CommonAllPlayers(
        is_only_current_season=0, league_id="00", season=season, timeout=60).get_dict())
    pool = [p for p in rows(everyone, "CommonAllPlayers")
            if p.get("GAMES_PLAYED_FLAG") == "Y" and str(p.get("TO_YEAR", "0")).isdigit()
            and int(p["TO_YEAR"]) >= FIRST_SEASON]
    print(f"{len(pool)} players played from {FIRST_SEASON}-{str(FIRST_SEASON + 1)[-2:]} on.\n")

    players, t0 = [], time.time()
    for k, p in enumerate(pool, 1):
        rec = build_player(p)
        if rec:
            players.append(rec)
        if k % 25 == 0 or k == len(pool):
            mins = (time.time() - t0) / 60
            print(f"{k}/{len(pool)}  ({p['DISPLAY_FIRST_LAST']})  {mins:.0f} min so far")

    # jersey numbers: one roster request per team-season (cached like the rest)
    print(f"\nGetting jersey numbers from {len(TEAM_SEASONS)} team rosters...")
    by_id = {r["id"]: r for r in players}
    for k, (tid, year) in enumerate(sorted(TEAM_SEASONS, key=lambda x: (x[1], x[0])), 1):
        roster = fetch(f"roster_{tid}", year, lambda: commonteamroster.CommonTeamRoster(
            team_id=tid, season=season_str(year), timeout=30).get_dict(), required=False)
        team = TEAM_BY_ID.get(tid)
        for r in rows(roster or {}, "CommonTeamRoster"):
            rec, num = by_id.get(r.get("PLAYER_ID")), str(r.get("NUM") or "").strip()
            if rec and team and num and [team, num] not in rec["num"]:
                rec["num"].append([team, num])
        if k % 100 == 0 or k == len(TEAM_SEASONS):
            print(f"{k}/{len(TEAM_SEASONS)} rosters")
    # fallback: players missing from every roster still get their last-known number
    for r in players:
        if not r["num"] and r["_last_num"]:
            r["num"].append([r["t"][-1], r["_last_num"]])
        del r["_last_num"]

    # popularity 1-10 from fame rank: most players are deep cuts, few are stars
    ranked = sorted(players, key=lambda r: r["_fame"])
    for idx, r in enumerate(ranked):
        pct = idx / max(1, len(ranked) - 1)
        r["p"] = max(1, round(1 + 9 * pct ** 4))
        del r["_fame"]

    players.sort(key=lambda r: r["n"])
    OUT.write_text(json.dumps(players, separators=(",", ":"), ensure_ascii=False))
    size = OUT.stat().st_size / 1e6
    print(f"\nDone. {len(players)} players written to {OUT} ({size:.1f} MB).")


if __name__ == "__main__":
    main()
