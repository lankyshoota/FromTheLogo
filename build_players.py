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
good response is cached in ./cache, so if it stops just run it again and it
picks up where it left off. It's also built to run overnight unattended: if
NBA.com stops answering it waits 5 minutes and keeps going, and only gives up
after an hour of silence. Players NBA.com sends empty data for (usually
throttling) get a second, slower try at the end.
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import date
from pathlib import Path

import requests
from nba_api.stats.endpoints import (
    commonallplayers,
    commonplayerinfo,
    commonteamroster,
    playerawards,
    playercareerstats,
)
from nba_api.stats.library.http import NBAStatsHTTP
from nba_api.stats.static import teams as static_teams

FIRST_SEASON = 1970          # 1970-71 is the earliest season included
SLEEP = 1.5                  # seconds between requests, plus up to 0.5s jitter
SLOW = 5.0                   # seconds between requests in the second pass
PAUSE = 300                  # NBA.com stopped answering: wait 5 minutes, then keep going
GIVE_UP = 3600               # ...unless there's been no good response for an hour
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


# nba_api parses whatever comes back, so a 429/5xx page would show up as a
# parse error. Raise those as HTTP errors first so they count as network errors.
def _raise_retryable(resp, *args, **kwargs):
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()


_session = requests.Session()
_session.hooks["response"].append(_raise_retryable)
NBAStatsHTTP.set_session(_session)

# NBA.com answered, but not with usable data. Usually throttling (an empty or
# error body), so these aren't cached: the player gets another try later.
PARSE_ERRORS = (KeyError, ValueError, TypeError, IndexError, AttributeError)


class BadResponse(Exception):
    pass


def valid(data) -> bool:
    """A real NBA.com stats response has a non-empty resultSets list."""
    return isinstance(data, dict) and isinstance(data.get("resultSets"), list) and bool(data["resultSets"])


def purge_bad_cache():
    """Delete cached responses that are empty or errors, so they're fetched again."""
    bad = 0
    for f in CACHE.glob("*.json"):
        try:
            ok = valid(json.loads(f.read_text()))
        except ValueError:
            ok = False
        if not ok:
            f.unlink()
            bad += 1
    if bad:
        print(f"Removed {bad} empty or bad cached responses; they'll be fetched again.")


def pause(seconds: float):
    time.sleep(seconds + random.random() * 0.5)


last_ok = time.time()        # last time NBA.com sent usable data
bad_streak = 0               # unusable responses in a row


def fetch(kind: str, pid, call, delay=SLEEP):
    """Fetch one endpoint, caching only good responses.
    Network errors (timeouts, connection errors, 429/5xx) retry: quickly at
    first, then every 5 minutes, until there's been no good response for an
    hour. Unusable data raises BadResponse (not cached, so it's retried later)."""
    global last_ok, bad_streak
    f = CACHE / f"{kind}_{pid}.json"
    if f.exists():
        return json.loads(f.read_text())
    attempt = 0
    while True:
        try:
            data = call()
            if not valid(data):
                raise KeyError("resultSets")
        except PARSE_ERRORS as e:
            bad_streak += 1
            print(f"   {kind} {pid}: unusable response ({e.__class__.__name__}: {e}), will retry later")
            if bad_streak >= 5:   # a run of empty answers means we're being throttled: back off
                print(f"   {bad_streak} unusable responses in a row, probably throttling. "
                      f"Pausing {PAUSE // 60} minutes...")
                time.sleep(PAUSE)
                bad_streak = 0
            else:
                pause(delay)
            raise BadResponse(f"{kind} {pid}") from e
        except requests.exceptions.RequestException as e:
            attempt += 1
            silent = time.time() - last_ok
            if silent > GIVE_UP:
                sys.exit(f"\nNo good response from NBA.com for {silent / 60:.0f} minutes (stuck at {kind} {pid}). "
                         "Giving up for now; rerun later and it picks up where it left off.")
            wait = 10 * attempt if attempt <= 3 else PAUSE
            print(f"   {kind} {pid}: {e.__class__.__name__}, retrying in "
                  f"{wait if wait < 60 else str(wait // 60) + ' min'}"
                  f"{'' if wait < 60 else f' (no good response for {silent / 60:.0f} min)'}")
            time.sleep(wait)
            continue
        last_ok, bad_streak = time.time(), 0
        f.write_text(json.dumps(data))
        pause(delay)
        return data


def rows(resp: dict, name: str) -> list[dict]:
    for rs in resp.get("resultSets", []):
        if rs["name"] == name:
            return [dict(zip(rs["headers"], r)) for r in rs["rowSet"]]
    return []


def build_player(p: dict, delay=SLEEP) -> dict | None:
    pid = p["PERSON_ID"]

    career = fetch("career", pid, lambda: playercareerstats.PlayerCareerStats(
        player_id=pid, per_mode36="Totals", timeout=30).get_dict(), delay)
    awards = fetch("awards", pid, lambda: playerawards.PlayerAwards(
        player_id=pid, timeout=30).get_dict(), delay)
    info = fetch("info", pid, lambda: commonplayerinfo.CommonPlayerInfo(
        player_id=pid, timeout=30).get_dict(), delay)

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
    purge_bad_cache()
    season = current_season()
    print(f"Getting the full player list (through {season})...")
    while True:   # can't do anything without the list, so keep trying (fetch exits after an hour of silence)
        try:
            everyone = fetch("all", season, lambda: commonallplayers.CommonAllPlayers(
                is_only_current_season=0, league_id="00", season=season, timeout=60).get_dict())
            break
        except BadResponse:
            if time.time() - last_ok > GIVE_UP:
                sys.exit("\nNBA.com kept sending an unusable player list for an hour. Rerun later.")
            print(f"   Player list came back empty, trying again in {PAUSE // 60} minutes...")
            time.sleep(PAUSE)
    pool = [p for p in rows(everyone, "CommonAllPlayers")
            if p.get("GAMES_PLAYED_FLAG") == "Y" and str(p.get("TO_YEAR", "0")).isdigit()
            and int(p["TO_YEAR"]) >= FIRST_SEASON]
    print(f"{len(pool)} players played from {FIRST_SEASON}-{str(FIRST_SEASON + 1)[-2:]} on.\n")

    players, retry, t0 = [], [], time.time()
    for k, p in enumerate(pool, 1):
        try:
            rec = build_player(p)
        except BadResponse:
            retry.append(p)
            rec = None
        if rec:
            players.append(rec)
        if k % 25 == 0 or k == len(pool):
            mins = (time.time() - t0) / 60
            print(f"{k}/{len(pool)}  ({p['DISPLAY_FIRST_LAST']})  {mins:.0f} min so far"
                  f"{f', {len(retry)} to retry' if retry else ''}")

    # second pass: players that got empty responses, after a breather and more slowly
    skipped = []
    if retry:
        print(f"\nSecond pass: retrying {len(retry)} players more slowly "
              f"(starting in {PAUSE // 60} minutes)...")
        time.sleep(PAUSE)
        for k, p in enumerate(retry, 1):
            try:
                rec = build_player(p, delay=SLOW)
            except BadResponse:
                skipped.append((p["PERSON_ID"], p["DISPLAY_FIRST_LAST"]))
                rec = None
            if rec:
                players.append(rec)
            if k % 25 == 0 or k == len(retry):
                print(f"{k}/{len(retry)} retried, {len(skipped)} still failing")

    # jersey numbers: one roster request per team-season (cached like the rest)
    print(f"\nGetting jersey numbers from {len(TEAM_SEASONS)} team rosters...")
    by_id = {r["id"]: r for r in players}

    def add_roster(tid, year, delay):
        roster = fetch(f"roster_{tid}", year, lambda: commonteamroster.CommonTeamRoster(
            team_id=tid, season=season_str(year), timeout=30).get_dict(), delay)
        team = TEAM_BY_ID.get(tid)
        for r in rows(roster, "CommonTeamRoster"):
            rec, num = by_id.get(r.get("PLAYER_ID")), str(r.get("NUM") or "").strip()
            if rec and team and num and [team, num] not in rec["num"]:
                rec["num"].append([team, num])

    roster_retry = []
    for k, (tid, year) in enumerate(sorted(TEAM_SEASONS, key=lambda x: (x[1], x[0])), 1):
        try:
            add_roster(tid, year, SLEEP)
        except BadResponse:
            roster_retry.append((tid, year))
        if k % 100 == 0 or k == len(TEAM_SEASONS):
            print(f"{k}/{len(TEAM_SEASONS)} rosters")
    # rosters only add jersey numbers, so one slower retry is enough
    missing_rosters = 0
    if roster_retry:
        print(f"Retrying {len(roster_retry)} rosters more slowly (starting in {PAUSE // 60} minutes)...")
        time.sleep(PAUSE)
        for tid, year in roster_retry:
            try:
                add_roster(tid, year, SLOW)
            except BadResponse:
                missing_rosters += 1
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
    if missing_rosters:
        print(f"{missing_rosters} team rosters never came back; those players use their last-known jersey number.")
    if skipped:
        print(f"\nSkipped {len(skipped)} players because NBA.com still sent unusable data after a second try:")
        for pid, name in skipped:
            print(f"  {pid}  {name}")
        print("Nothing bad is cached, so just rerun later to try them again.")


if __name__ == "__main__":
    main()
