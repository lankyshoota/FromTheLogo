"""
Fill in players that build_players.py had to skip.

For ~4% of players NBA.com's PlayerCareerStats answers with JSON that has no
data in it, every time. This script finds those players (everyone in the
cached player list who isn't in players.json), gets their seasons and teams
from other endpoints, and merges them into players.json:

  1. PlayerProfileV2            (1 request, same tables as PlayerCareerStats)
  2. PlayerDashboardByYearOverYear  (2 requests: regular season + playoffs)
  3. PlayerGameLogs, one season at a time  (last resort, summed per team)

Everything else (awards, draft, college, jerseys, tags) is built exactly the
way build_players.py builds it, using the same cache. Run it after
build_players.py finishes:

  python3 fill_missing.py              # every player missing from players.json
  python3 fill_missing.py 1629152 1824 # just these ids

It backs up players.json to players.before_fill.json first, and it's safe to
run again: players it fills are cached, so a rerun only retries the rest.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from nba_api.stats.endpoints import (
    playerdashboardbyyearoveryear,
    playergamelogs,
    playerprofilev2,
)
from nba_api.stats.library.http import NBAStatsHTTP

import build_players as bp

RAW_SAMPLES = 3              # save this many raw PlayerCareerStats answers, to see what NBA.com sends
BACKUP = Path("players.before_fill.json")
CAREER_HEADERS = ["SEASON_ID", "TEAM_ID", "TEAM_ABBREVIATION", "GP", "PTS", "REB", "AST"]


def career_dict(regular: list[list], playoffs: list[list], source: str) -> dict:
    """Shape season rows like a PlayerCareerStats response, so build_player() can use them."""
    return {
        "_source": source,
        "resultSets": [
            {"name": "SeasonTotalsRegularSeason", "headers": CAREER_HEADERS, "rowSet": regular},
            {"name": "SeasonTotalsPostSeason", "headers": CAREER_HEADERS, "rowSet": playoffs},
        ],
    }


def team_rows(rows: list[dict], season_key: str) -> list[list] | None:
    """Per-team season rows. None if some season only has a combined (TOT) row,
    because then we can't tell which teams he played for."""
    by_season = defaultdict(list)
    for r in rows:
        by_season[r[season_key]].append(r)
    out = []
    for season, rs in by_season.items():
        teams = [r for r in rs if r.get("TEAM_ID") and r.get("TEAM_ABBREVIATION") != "TOT"]
        if not teams:
            return None
        out += [[season, r["TEAM_ID"], r["TEAM_ABBREVIATION"], r["GP"] or 0, r["PTS"] or 0,
                 r["REB"] or 0, r["AST"] or 0] for r in teams]
    return out


def from_profile(pid) -> dict | None:
    resp = bp.fetch("profile", pid, lambda: playerprofilev2.PlayerProfileV2(
        player_id=pid, per_mode36="Totals", timeout=30).get_dict())
    regular = team_rows(bp.rows(resp, "SeasonTotalsRegularSeason"), "SEASON_ID")
    playoffs = team_rows(bp.rows(resp, "SeasonTotalsPostSeason"), "SEASON_ID")
    if not regular:
        return None
    return career_dict(regular, playoffs or [], "playerprofilev2")


def from_year_over_year(pid) -> dict | None:
    def get(season_type):
        resp = bp.fetch(f"yoy_{season_type[:3].lower()}", pid,
                        lambda: playerdashboardbyyearoveryear.PlayerDashboardByYearOverYear(
                            player_id=pid, per_mode_detailed="Totals",
                            season_type_playoffs=season_type, timeout=30).get_dict())
        return team_rows(bp.rows(resp, "ByYearPlayerDashboard"), "GROUP_VALUE")
    regular = get("Regular Season")
    if not regular:
        return None
    try:
        playoffs = get("Playoffs") or []
    except bp.BadResponse:
        playoffs = []
    return career_dict(regular, playoffs, "playerdashboardbyyearoveryear")


def from_game_logs(pid) -> dict | None:
    info = (bp.rows(bp.fetch("info", pid, lambda: bp.commonplayerinfo.CommonPlayerInfo(
        player_id=pid, timeout=30).get_dict()), "CommonPlayerInfo") or [{}])[0]
    first, last = info.get("FROM_YEAR"), info.get("TO_YEAR")
    if not str(first).isdigit() or not str(last).isdigit():
        return None

    def season_totals(season_type):
        totals = {}   # (season, team id) -> row
        for year in range(max(int(first), bp.FIRST_SEASON), int(last) + 1):
            season = bp.season_str(year)
            try:
                resp = bp.fetch(f"glog_{season_type[:3].lower()}_{season}", pid,
                                lambda: playergamelogs.PlayerGameLogs(
                                    player_id_nullable=pid, season_nullable=season,
                                    season_type_nullable=season_type, timeout=30).get_dict())
            except bp.BadResponse:
                continue
            for g in bp.rows(resp, "PlayerGameLogs"):
                key = (g["SEASON_YEAR"], g["TEAM_ID"])
                t = totals.setdefault(key, [g["SEASON_YEAR"], g["TEAM_ID"], g["TEAM_ABBREVIATION"], 0, 0, 0, 0])
                t[3] += 1
                t[4] += g.get("PTS") or 0
                t[5] += g.get("REB") or 0
                t[6] += g.get("AST") or 0
        return sorted(totals.values(), key=lambda t: t[0])

    regular = season_totals("Regular Season")
    if not regular:
        return None
    return career_dict(regular, season_totals("Playoffs"), "playergamelogs")


SOURCES = [from_profile, from_year_over_year, from_game_logs]


class CareerFromOtherEndpoints:
    """Stands in for PlayerCareerStats inside build_player()."""

    def __init__(self, player_id, **kwargs):
        self.pid = player_id

    def get_dict(self):
        for source in SOURCES:
            try:
                data = source(self.pid)
            except bp.BadResponse:
                data = None
            if data:
                return data
        raise KeyError("no seasons from any endpoint")


def save_raw_sample(pid):
    """Keep what PlayerCareerStats actually sends for a few of these players."""
    f = bp.CACHE / f"raw_career_{pid}.txt"
    if f.exists():
        return
    try:
        resp = NBAStatsHTTP().send_api_request(
            endpoint="playercareerstats",
            parameters={"PlayerID": pid, "PerMode": "Totals", "LeagueID": "00"}, timeout=30)
        body = resp.get_response()
    except Exception as e:  # just a diagnostic, never fatal
        body = f"request failed: {e!r}"
    f.write_text(body)
    print(f"   raw PlayerCareerStats answer for {pid} saved to {f}: {body[:160]!r}")


def fame(r: dict) -> float:
    """Same formula build_players.py uses for popularity."""
    aw = r.get("aw", {})
    pts = sum(s[3] for s in r["s"])
    return (pts / 1000 + aw.get("AS", 0) * 4 + aw.get("MVP", 0) * 12 + aw.get("ALLNBA", 0) * 3
            + aw.get("CH", 0) * 1.5 + aw.get("FMVP", 0) * 6)


def jersey_numbers(rec: dict, team_seasons: set):
    for tid, year in sorted(team_seasons, key=lambda x: (x[1], x[0])):
        try:
            roster = bp.fetch(f"roster_{tid}", year, lambda: bp.commonteamroster.CommonTeamRoster(
                team_id=tid, season=bp.season_str(year), timeout=30).get_dict())
        except bp.BadResponse:
            continue
        team = bp.TEAM_BY_ID.get(tid)
        for r in bp.rows(roster, "CommonTeamRoster"):
            num = str(r.get("NUM") or "").strip()
            if r.get("PLAYER_ID") == rec["id"] and team and num and [team, num] not in rec["num"]:
                rec["num"].append([team, num])
    if not rec["num"] and rec["_last_num"]:
        rec["num"].append([rec["t"][-1], rec["_last_num"]])
    del rec["_last_num"]


def main():
    if not bp.OUT.exists():
        sys.exit("players.json not found. Run build_players.py first.")
    lists = sorted(bp.CACHE.glob("all_*.json"))
    if not lists:
        sys.exit("No cached player list in ./cache. Run build_players.py first.")
    players = json.loads(bp.OUT.read_text())
    have = {r["id"] for r in players}
    pool = [p for p in bp.rows(json.loads(lists[-1].read_text()), "CommonAllPlayers")
            if p.get("GAMES_PLAYED_FLAG") == "Y" and str(p.get("TO_YEAR", "0")).isdigit()
            and int(p["TO_YEAR"]) >= bp.FIRST_SEASON]
    wanted = {int(a) for a in sys.argv[1:]}
    todo = [p for p in pool if (p["PERSON_ID"] in wanted if wanted else p["PERSON_ID"] not in have)]
    if wanted - {p["PERSON_ID"] for p in todo}:
        print(f"Not in the cached player list (ignored): {sorted(wanted - {p['PERSON_ID'] for p in todo})}")
    if not todo:
        print("Nothing to fill: every player in the list is in players.json.")
        return
    print(f"{len(todo)} players to fill in.\n")

    # build_player() asks for PlayerCareerStats; hand it the other endpoints instead
    bp.playercareerstats.PlayerCareerStats = CareerFromOtherEndpoints
    filled, still_missing, sources = [], [], defaultdict(int)
    for k, p in enumerate(todo, 1):
        pid = p["PERSON_ID"]
        if k <= RAW_SAMPLES:
            save_raw_sample(pid)
        before = set(bp.TEAM_SEASONS)
        cached = (bp.CACHE / f"career_{pid}.json").exists()
        try:
            rec = bp.build_player(p)
        except bp.BadResponse:
            rec = None
        if not rec:
            still_missing.append((pid, p["DISPLAY_FIRST_LAST"]))
            continue
        jersey_numbers(rec, bp.TEAM_SEASONS - before)
        src = json.loads((bp.CACHE / f"career_{pid}.json").read_text()).get("_source", "playercareerstats")
        sources[src + (" (cached)" if cached else "")] += 1
        filled.append(rec)
        print(f"{k}/{len(todo)}  {p['DISPLAY_FIRST_LAST']}: {len(rec['s'])} team-seasons from {src}")

    if filled:
        if not BACKUP.exists():
            BACKUP.write_text(bp.OUT.read_text())
        by_id = {r["id"]: r for r in players}
        for rec in filled:
            rec.pop("_fame", None)
            by_id[rec["id"]] = rec
        players = list(by_id.values())
        # popularity is a rank across everyone, so recompute it with the new players in
        ranked = sorted(players, key=fame)
        for idx, r in enumerate(ranked):
            pct = idx / max(1, len(ranked) - 1)
            r["p"] = max(1, round(1 + 9 * pct ** 4))
        players.sort(key=lambda r: r["n"])
        bp.OUT.write_text(json.dumps(players, separators=(",", ":"), ensure_ascii=False))

    print(f"\nFilled {len(filled)} players" + (": " + ", ".join(f"{n} from {s}" for s, n in sources.items()) if filled else "."))
    if filled:
        print(f"players.json now has {len(players)} players (backup of the original: {BACKUP}).")
    if still_missing:
        print(f"\nStill missing {len(still_missing)} (no endpoint had their seasons):")
        for pid, name in still_missing:
            print(f"  {pid}  {name}")


if __name__ == "__main__":
    main()
