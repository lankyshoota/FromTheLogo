"""
FromTheLogo — fast player database builder (bulk endpoints).

Instead of asking NBA.com about one player at a time, this pulls whole seasons:

  - PlayerIndex: every player ever (name, position, height, weight, college,
    country, draft, current jersey)
  - DraftHistory: every draft pick
  - LeagueGameLog: every player-game of every regular season and playoffs
    since 1970-71. Summed per player, team and season, that's the same season
    lines PlayerCareerStats gives (trades included). The last playoff game of
    each year tells us the champion, so rings come for free.

That's ~150 requests instead of ~16,000. Before trusting a season's game logs
it checks them against the team box scores (every season before 1985, and any
later season that looks short). Players in a season that doesn't add up get
their seasons from PlayerCareerStats instead, one player at a time.

Anything already in ./cache from build_players.py (awards, player info,
careers, rosters) is reused, so nothing gets fetched twice. Awards that
aren't cached yet stay empty (except rings) until fill_details.py gets them.

Run:
  python3 build_players_bulk.py

players.json is written as soon as the bulk part is done (a few minutes plus
any waiting on NBA.com), then updated as the per-player fallbacks come in.
If NBA.com stops answering it pauses and keeps going; Ctrl+C is safe and a
rerun picks up from the cache.
"""

from __future__ import annotations

import gzip
import json
import random
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import requests
from nba_api.stats.endpoints import drafthistory, leaguegamelog, playercareerstats, playerindex

import build_players as bp   # shared: cache folder, franchise merge, award names, response checks

CHECK_BEFORE = 1985          # always check these seasons' game logs against team box scores
COMPLETE = 0.99              # share of team-games whose player points must add up to the team's
MIN_GAMES = {1998: 50, 2011: 66, 2019: 63, 2020: 72}   # short seasons; everything else is 82
DELAY = 6.0                  # seconds between requests (adapts: slower after a block)
FALLBACK_SAVE_EVERY = 25     # rewrite players.json after this many per-player fallbacks
MAX_EMPTY = 3                # stop asking for something after this many empty answers (across runs)
EMPTY_LOG = bp.CACHE / "empty_answers.json"   # request name -> empty answers so far
TEAM_ID_BY_ABBR = {abbr: tid for tid, abbr in bp.TEAM_BY_ID.items()}
POSITIONS = {"G": "Guard", "F": "Forward", "C": "Center"}


# ---------------------------------------------------------------- polite fetching

class Polite:
    """Paced, cached requests that wait out NBA.com blocks instead of quitting.

    Network errors (timeouts, 429/5xx) retry after 30 s and 2 min, then count as a
    block: pause 15 min, then 30, then every 60, for as long as it takes. Each block
    also makes the pace slower; long clean stretches speed it back up.

    An empty answer raises BadResponse (some players' data is just broken). Three in a
    row might be a quieter kind of block, so it pauses once and asks again; if the same
    request is still empty after the pause, it's the data, not a block, and it won't
    pause for empties again until something succeeds. Requests that came back empty
    MAX_EMPTY times (across runs, kept in cache/empty_answers.json) aren't sent again.
    """

    WAITS = [30, 120, 900, 1800, 3600]

    def __init__(self, delay=DELAY, min_delay=None, max_delay=45.0):
        self.delay = delay
        self.min_delay = min_delay or delay
        self.max_delay = max_delay
        self.empty_streak = 0
        self.empty_pause_ok = True   # re-armed by the next good answer
        self.clean = 0
        self.made = 0                # requests actually sent
        try:
            self.empties = json.loads(EMPTY_LOG.read_text())
        except (OSError, ValueError):
            self.empties = {}

    def gave_up(self, name: str) -> bool:
        return self.empties.get(name, 0) >= MAX_EMPTY

    def get(self, name: str, call, gz=False):
        f = bp.CACHE / (name + (".json.gz" if gz else ".json"))
        if f.exists():
            try:
                data = json.loads(gzip.decompress(f.read_bytes()) if gz else f.read_text())
                if bp.valid(data):
                    return data
            except (ValueError, OSError):
                pass
            f.unlink()
        if self.gave_up(name):
            raise bp.BadResponse(name)
        failures, retried_after_pause = 0, False
        while True:
            try:
                self.made += 1
                data = call()
                if not bp.valid(data):
                    raise KeyError("resultSets")
            except bp.PARSE_ERRORS as e:
                self.empty_streak += 1
                if self.empty_streak >= 3 and self.empty_pause_ok and not retried_after_pause:
                    print(f"   [{time.strftime('%H:%M')}] {self.empty_streak} empty answers in a row, maybe a block. "
                          f"Pausing 15 min, then asking again.")
                    time.sleep(900)
                    retried_after_pause = True
                    continue
                if retried_after_pause:
                    self.empty_pause_ok = False   # still empty after a pause: it's the data
                    self.empty_streak = 0
                self.empties[name] = self.empties.get(name, 0) + 1
                tmp = EMPTY_LOG.with_name(EMPTY_LOG.name + ".tmp")
                tmp.write_text(json.dumps(self.empties))
                tmp.replace(EMPTY_LOG)
                self._sleep()
                raise bp.BadResponse(name) from e
            except requests.exceptions.RequestException as e:
                wait = self.WAITS[min(failures, len(self.WAITS) - 1)]
                failures += 1
                if wait >= 900:
                    self._blocked(e.__class__.__name__, wait)
                else:
                    print(f"   {name}: {e.__class__.__name__}, retrying in {wait}s")
                    time.sleep(wait)
                continue
            self.empty_streak = 0
            self.empty_pause_ok = True
            self.clean += 1
            if self.clean >= 100 and self.delay > self.min_delay:
                self.delay = max(self.min_delay, self.delay * 0.85)
                self.clean = 0
            tmp = f.with_name(f.name + ".tmp")
            raw = json.dumps(data)
            tmp.write_bytes(gzip.compress(raw.encode())) if gz else tmp.write_text(raw)
            tmp.replace(f)
            self._sleep()
            return data

    def _sleep(self):
        time.sleep(self.delay + random.random() * self.delay * 0.4)

    def _blocked(self, why, pause):
        self.delay = min(self.max_delay, self.delay * 1.5)
        self.clean = 0
        print(f"   [{time.strftime('%H:%M')}] NBA.com isn't answering ({why}). Pausing {pause // 60} min, "
              f"then continuing at ~{self.delay:.0f}s per request.")
        time.sleep(pause)


# ---------------------------------------------------------------- bulk downloads

def last_season() -> int:
    return int(bp.current_season()[:4])


def season_logs(pol: Polite, year: int, playoffs: bool, who="P") -> list[dict]:
    """Every player-game (who="P") or team-game (who="T") of one season."""
    season, kind = bp.season_str(year), "Playoffs" if playoffs else "Regular Season"
    # the season in progress changes every day, so its cache is per day
    tag = f"_{date.today()}" if year == last_season() else ""
    resp = pol.get(f"bulk_log_{who}_{'po' if playoffs else 'rs'}_{season}{tag}",
                   lambda: leaguegamelog.LeagueGameLog(
                       season=season, season_type_all_star=kind, player_or_team_abbreviation=who,
                       timeout=120).get_dict(), gz=True)
    return bp.rows(resp, "LeagueGameLog")


def check_complete(pol: Polite, year: int, playoffs: bool, prows: list[dict]) -> float | None:
    """Share of team-games where the players' points add up to the team's points
    (and at least 5 players are listed). None if the team logs couldn't be fetched."""
    try:
        trows = season_logs(pol, year, playoffs, who="T")
    except bp.BadResponse:
        return None
    if not trows:
        return 1.0 if not prows else 0.0
    pts, players = defaultdict(int), defaultdict(int)
    for r in prows:
        key = (r["GAME_ID"], r["TEAM_ID"])
        pts[key] += r["PTS"] or 0
        players[key] += 1
    ok = sum(1 for r in trows if pts.get((r["GAME_ID"], r["TEAM_ID"])) == (r["PTS"] or 0)
             and players.get((r["GAME_ID"], r["TEAM_ID"]), 0) >= 5)
    return ok / len(trows)


def looks_short(year: int, prows: list[dict]) -> bool:
    """Regular season only: does any team have clearly fewer games than the season had?"""
    if year == last_season():
        return False   # still being played
    games = defaultdict(set)
    for r in prows:
        games[r["TEAM_ID"]].add(r["GAME_ID"])
    need = MIN_GAMES.get(year, 82) * 0.95
    return not games or min(len(g) for g in games.values()) < need


def stints(year: int, prows: list[dict]) -> dict[int, list[list]]:
    """player id -> [[year, team, gp, pts, reb, ast], ...] in the order he played for them."""
    agg = {}
    for r in prows:
        team = bp.TEAM_BY_ID.get(r["TEAM_ID"], r["TEAM_ABBREVIATION"])
        k = (r["PLAYER_ID"], team)
        a = agg.setdefault(k, [r["GAME_DATE"], 0, 0, 0, 0])
        a[0] = min(a[0], r["GAME_DATE"])
        a[1] += 1
        a[2] += r["PTS"] or 0
        a[3] += r["REB"] or 0
        a[4] += r["AST"] or 0
    out = defaultdict(list)
    for (pid, team), (first, gp, pts, reb, ast) in sorted(agg.items(), key=lambda kv: kv[1][0]):
        out[pid].append([year, team, gp, pts, reb, ast])
    return out


def champion(prows: list[dict]) -> str | None:
    """Franchise that won the last playoff game of the year."""
    if not prows:
        return None
    last = max(r["GAME_DATE"] for r in prows)
    winners = {r["TEAM_ID"] for r in prows if r["GAME_DATE"] == last and r.get("WL") == "W"}
    return bp.TEAM_BY_ID.get(winners.pop()) if len(winners) == 1 else None


def career_seasons(pol: Polite, pid) -> tuple[list, list] | None:
    """Per-player fallback: (regular, playoffs) season lines from PlayerCareerStats
    (or whatever fill_missing.py cached in its place)."""
    try:
        resp = pol.get(f"career_{pid}", lambda: playercareerstats.PlayerCareerStats(
            player_id=pid, per_mode36="Totals", timeout=30).get_dict())
    except bp.BadResponse:
        return None

    def lines(name):
        out = []
        for r in bp.rows(resp, name):
            year = int(str(r["SEASON_ID"])[:4])
            if year < bp.FIRST_SEASON or not r["TEAM_ID"] or r.get("TEAM_ABBREVIATION") == "TOT":
                continue
            out.append([year, bp.TEAM_BY_ID.get(r["TEAM_ID"], r["TEAM_ABBREVIATION"]),
                        r["GP"] or 0, r["PTS"] or 0, r["REB"] or 0, r["AST"] or 0])
        return out

    regular = lines("SeasonTotalsRegularSeason")
    return (regular, lines("SeasonTotalsPostSeason")) if regular else None


# ---------------------------------------------------------------- building records

def cached(name: str):
    """A response build_players.py / fill_details.py already saved, or None."""
    f = bp.CACHE / f"{name}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except ValueError:
        return None
    return data if bp.valid(data) else None


def parse_awards(resp) -> dict:
    aw = {}
    for r in bp.rows(resp, "PlayerAwards"):
        key = bp.AWARD_KEYS.get(r["DESCRIPTION"])
        if key:
            aw[key] = aw.get(key, 0) + 1
            if key in ("ALLNBA", "ALLDEF", "ALLROOK") and str(r.get("ALL_NBA_TEAM_NUMBER")) == "1":
                aw[key + "1"] = aw.get(key + "1", 0) + 1
    return aw


def inches(ht) -> int | None:
    feet, _, inch = str(ht or "").partition("-")
    return int(feet) * 12 + int(inch) if feet.isdigit() and inch.isdigit() else None


def clean(v) -> str:
    v = str(v or "").strip()
    return "" if v in ("None", "null") else v


def new_record(pid, name, s, ps, idx: dict, draft: dict) -> dict:
    """A player from bulk data, in the same format build_players.py writes."""
    teams = []
    for line in s:
        if line[1] not in teams:
            teams.append(line[1])
    dr = None
    if draft:
        dr = [int(draft["SEASON"]), int(draft["ROUND_NUMBER"]), int(draft["OVERALL_PICK"])]
    elif str(idx.get("DRAFT_ROUND")).isdigit() and str(idx.get("DRAFT_NUMBER")).isdigit() \
            and str(idx.get("DRAFT_YEAR")).isdigit():
        dr = [int(idx["DRAFT_YEAR"]), int(idx["DRAFT_ROUND"]), int(idx["DRAFT_NUMBER"])]
    wt = clean(idx.get("WEIGHT"))
    pos = "-".join(POSITIONS.get(p, p) for p in clean(idx.get("POSITION")).split("-") if p)
    col = clean(idx.get("COLLEGE")) or (clean(draft.get("ORGANIZATION"))
                                        if draft and draft.get("ORGANIZATION_TYPE") == "College/University" else "")
    return {
        "id": pid, "n": name, "t": teams, "g": "", "s": s, "ps": ps, "aw": {}, "dr": dr,
        "c": clean(idx.get("COUNTRY")), "pos": pos, "col": col,
        "ht": inches(idx.get("HEIGHT")), "wt": int(wt) if wt.isdigit() else None, "bd": None,
        "num": [], "from": s[0][0], "to": s[-1][0], "gp": 0, "ppg": 0, "p": 1,
        "_jersey": clean(idx.get("JERSEY_NUMBER")),   # current/last jersey, used if rosters have none
    }


def apply_cached_details(rec: dict, rings: int):
    """Fill in whatever per-player data is in ./cache: awards, player info, rosters."""
    pid = rec["id"]
    awards = cached(f"awards_{pid}")
    rec["aw"] = parse_awards(awards) if awards else ({"CH": rings} if rings else {})
    info = cached(f"info_{pid}")
    if info:
        i = (bp.rows(info, "CommonPlayerInfo") or [{}])[0]
        rnd, pick, dyear = i.get("DRAFT_ROUND"), i.get("DRAFT_NUMBER"), i.get("DRAFT_YEAR")
        if str(rnd).isdigit() and str(pick).isdigit() and str(dyear).isdigit():
            rec["dr"] = [int(dyear), int(rnd), int(pick)]
        elif str(rnd).strip().lower() == "undrafted":
            rec["dr"] = None
        rec["c"] = clean(i.get("COUNTRY")) or rec["c"]
        rec["pos"] = clean(i.get("POSITION")) or rec["pos"]
        rec["col"] = clean(i.get("SCHOOL")) or rec["col"]
        rec["ht"] = inches(i.get("HEIGHT")) or rec["ht"]
        wt = clean(i.get("WEIGHT"))
        rec["wt"] = int(wt) if wt.isdigit() else rec["wt"]
        rec["bd"] = clean(i.get("BIRTHDATE"))[:10] or None
        rec["_jersey"] = rec.get("_jersey") or clean(i.get("JERSEY"))
    num = []
    for year, team in dict.fromkeys((line[0], line[1]) for line in rec["s"]):
        roster = cached(f"roster_{TEAM_ID_BY_ABBR.get(team)}_{year}")
        for r in bp.rows(roster or {}, "CommonTeamRoster"):
            n = clean(r.get("NUM"))
            if r.get("PLAYER_ID") == pid and n and [team, n] not in num:
                num.append([team, n])
    if not num and rec.get("_jersey"):
        num.append([rec["t"][-1], rec["_jersey"]])
    rec["num"] = num


def finish(players: list[dict]) -> list[dict]:
    """Totals, tags and popularity (same rules as build_players.py); returns sorted output."""
    for r in players:
        aw, dr, c = r["aw"], r["dr"], r["c"]
        gp, pts = sum(x[2] for x in r["s"]), sum(x[3] for x in r["s"])
        r["gp"], r["ppg"] = gp, round(pts / gp, 1) if gp else 0
        r["from"], r["to"] = r["s"][0][0], r["s"][-1][0]
        tags = ""
        if aw.get("CH"): tags += "C"
        if aw.get("MVP"): tags += "M"
        if aw.get("AS"): tags += "A"
        if dr and dr[2] == 1: tags += "1"
        if not dr or dr[1] >= 2: tags += "R"
        if c and c.upper() != "USA": tags += "I"
        if aw.get("DPOY"): tags += "D"
        if aw.get("ROY"): tags += "Y"
        if aw.get("6MOY"): tags += "S"
        if aw.get("FMVP"): tags += "F"
        r["g"] = tags
    fame = lambda r: (sum(x[3] for x in r["s"]) / 1000 + r["aw"].get("AS", 0) * 4 + r["aw"].get("MVP", 0) * 12
                      + r["aw"].get("ALLNBA", 0) * 3 + r["aw"].get("CH", 0) * 1.5 + r["aw"].get("FMVP", 0) * 6)
    ranked = sorted(players, key=fame)
    for i, r in enumerate(ranked):
        r["p"] = max(1, round(1 + 9 * (i / max(1, len(ranked) - 1)) ** 4))
    keys = ["id", "n", "t", "g", "s", "ps", "aw", "dr", "c", "pos", "col", "ht", "wt", "bd", "num",
            "from", "to", "gp", "ppg", "p"]
    return sorted(({k: r[k] for k in keys} for r in players), key=lambda r: r["n"])


def write_players(players: list[dict]):
    out = finish(players)
    tmp = bp.OUT.with_name(bp.OUT.name + ".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    tmp.replace(bp.OUT)   # never leaves a half-written players.json
    return len(out), bp.OUT.stat().st_size / 1e6


def rings_for(ps: list, champs: dict) -> int:
    return len({y for y, team, *_ in ps if champs.get(y) == team})


# ---------------------------------------------------------------- main

def main():
    bp.CACHE.mkdir(exist_ok=True)
    pol = Polite()
    first, last = bp.FIRST_SEASON, last_season()
    today = date.today()

    print("Getting every player (PlayerIndex) and every draft pick (DraftHistory)...")
    index = {r["PERSON_ID"]: r for r in bp.rows(pol.get(f"bulk_playerindex_{today}", lambda: playerindex.PlayerIndex(
        historical_nullable=1, league_id="00", season=bp.season_str(last), timeout=90).get_dict()), "PlayerIndex")}
    drafts = {}
    for r in bp.rows(pol.get(f"bulk_drafthistory_{today:%Y-%m}", lambda: drafthistory.DraftHistory(
            league_id="00", timeout=90).get_dict()), "DraftHistory"):
        # a few players were drafted twice (went back to school); the later draft is the one that counted
        if r["PERSON_ID"] not in drafts or int(r["SEASON"]) > int(drafts[r["PERSON_ID"]]["SEASON"]):
            drafts[r["PERSON_ID"]] = r
    print(f"  {len(index)} players, {len(drafts)} draft picks.\n")

    print(f"Getting game logs for {last - first + 1} seasons (regular season + playoffs)...")
    regular, playoffs, names = defaultdict(list), defaultdict(list), {}
    champs, incomplete = {}, {}
    for year in range(first, last + 1):
        rs, po = season_logs(pol, year, False), season_logs(pol, year, True)
        problems = []
        for kind, rows_, is_po in (("regular season", rs, False), ("playoffs", po, True)):
            if year < CHECK_BEFORE or (not is_po and looks_short(year, rows_)):
                share = check_complete(pol, year, is_po, rows_)
                if share is None or share < COMPLETE:
                    problems.append(f"{kind} {'unchecked' if share is None else f'{share:.0%} complete'}")
        for pid, lines in stints(year, rs).items():
            regular[pid] += lines
        for pid, lines in stints(year, po).items():
            playoffs[pid] += lines
        for r in rs:
            names.setdefault(r["PLAYER_ID"], r.get("PLAYER_NAME"))
        champs[year] = champion(po)
        if not champs[year] and year < last:   # thin playoff logs: use the team box scores
            try:
                champs[year] = champion(season_logs(pol, year, True, who="T"))
            except bp.BadResponse:
                pass
        if problems:
            incomplete[year] = {r["PLAYER_ID"] for r in rs + po}
        print(f"  {bp.season_str(year)}: {len({r['PLAYER_ID'] for r in rs})} players"
              f"{f', champion {champs[year]}' if champs[year] else ''}"
              f"{'  <- ' + '; '.join(problems) if problems else ''}")

    # players to take from PlayerCareerStats instead: anyone in a season that didn't add up,
    # including players the logs are missing entirely (PlayerIndex says they played then)
    fallback = set()
    for year, seen in incomplete.items():
        fallback |= seen
        fallback |= {pid for pid, r in index.items()
                     if str(r.get("FROM_YEAR")).isdigit() and str(r.get("TO_YEAR")).isdigit()
                     and int(r["FROM_YEAR"]) <= year <= int(r["TO_YEAR"])}

    def make(pid, s, ps):
        idx = index.get(pid, {})
        name = f"{clean(idx.get('PLAYER_FIRST_NAME'))} {clean(idx.get('PLAYER_LAST_NAME'))}".strip() \
            or names.get(pid) or f"Player {pid}"
        rec = new_record(pid, name, s, ps, idx, drafts.get(pid))
        apply_cached_details(rec, rings_for(ps, champs))
        return rec

    players = {pid: make(pid, s, playoffs.get(pid, [])) for pid, s in regular.items()}
    n, mb = write_players(list(players.values()))
    print(f"\nWrote {n} players to {bp.OUT} ({mb:.1f} MB) from {pol.made} requests. The game can use it now.")

    if fallback:
        cached_now = sum(1 for pid in fallback if (bp.CACHE / f"career_{pid}.json").exists())
        skipped = sum(1 for pid in fallback if pol.gave_up(f"career_{pid}"))
        print(f"\n{len(incomplete)} season(s) didn't add up ({', '.join(bp.season_str(y) for y in sorted(incomplete))}).")
        print(f"Using PlayerCareerStats for their {len(fallback)} players instead: {cached_now} cached, "
              f"{skipped} always empty, {len(fallback) - cached_now - skipped} to fetch one at a time...")
        partial, lost = [], []
        for k, pid in enumerate(sorted(fallback, key=lambda p: -len(regular.get(p, []))), 1):
            got = career_seasons(pol, pid)
            if got:
                players[pid] = make(pid, *got)
            elif pid in players:
                partial.append(pid)   # keep what the game logs had
            else:
                lost.append(pid)
            if k % FALLBACK_SAVE_EVERY == 0 or k == len(fallback):
                n, mb = write_players(list(players.values()))
                print(f"  {k}/{len(fallback)} done, players.json updated ({n} players)")
        if partial:
            print(f"  {len(partial)} of them only have game-log data (PlayerCareerStats was empty): "
                  f"{', '.join(map(str, partial[:20]))}{' ...' if len(partial) > 20 else ''}")
        if lost:
            print(f"  {len(lost)} players aren't in the game logs and PlayerCareerStats was empty, so they're left out "
                  f"for now (fill_missing.py can try other endpoints): "
                  f"{', '.join(map(str, lost[:20]))}{' ...' if len(lost) > 20 else ''}")

    have_awards = sum(1 for pid in players if (bp.CACHE / f"awards_{pid}.json").exists())
    print(f"\nDone: {len(players)} players. Awards cached for {have_awards}; "
          f"run fill_details.py to get the rest (plus birthdates and jersey history).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nStopped. Everything downloaded so far is cached; rerun to continue.")
