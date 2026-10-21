# Plan: build players.json from bulk endpoints

Status: built as `build_players_bulk.py` (bio, draft, seasons, playoffs, rings, with a
completeness check and per-player fallback) and `fill_details.py` (awards, birthdates,
jerseys, slowly). Tested on fake data only; the first real run is the probe.

## Why

`build_players.py` asks NBA.com about one player at a time:

| What | Endpoint | Requests |
|---|---|---|
| Player list | CommonAllPlayers | 1 |
| Seasons + teams + playoffs | PlayerCareerStats | ~5,000 (one per player) |
| Awards | PlayerAwards | ~5,000 |
| Draft, college, country, height, weight, birthdate, last jersey | CommonPlayerInfo | ~5,000 |
| Jersey numbers by team | CommonTeamRoster | ~1,600 (one per team-season) |
| **Total** | | **~16,600**, 4-5 hours at 1.5-2 s each |

About 4% of players get an empty answer from PlayerCareerStats every time, and the
long runs are what trip NBA.com's throttling.

## The bulk endpoints

| Endpoint | One request gives | Replaces |
|---|---|---|
| **PlayerIndex** (`historical_nullable=1`) | Every player ever: id, name, position, height, weight, college, country, draft year/round/pick, from/to year, current jersey | CommonAllPlayers + most of CommonPlayerInfo |
| **DraftHistory** | Every pick since 1947: player id, year, round, pick, overall pick, team | Draft fields (cross-check for PlayerIndex) |
| **LeagueGameLog** (`player_or_team_abbreviation="P"`), per season and season type | Every player-game of that season: player id, team id, points, rebounds, assists | PlayerCareerStats: sum games per (player, team, season) = the `s` and `ps` rows, trades included |

LeagueGameLog is the big one. It's 56 seasons x 2 (regular season and playoffs) =
**~112 requests** for every season row of every player, in place of ~5,000. It also
sidesteps the broken-PlayerCareerStats players completely.

Champions (`CH`) can come from the same data: the team that wins the last playoff
series each season is the champion, and everyone in its playoff game logs gets a ring.

## What has no bulk endpoint

- **Awards** (All-Star, MVP, Finals MVP, DPOY, ROY, 6MOY, All-NBA, All-Defensive,
  All-Rookie, Player of the Month). PlayerAwards only works one player at a time. Options:
  1. Only ask about players who could plausibly have an award: at least one season with
     40+ games, or any All-Rookie-eligible rookie season. Estimate: ~2,500-3,000 requests
     instead of ~5,000. It needs checking against this run's full data, so we know no
     award winner gets missed.
  2. Keep a small hand-checked awards table in the repo (a few thousand rows, changes
     once a year) and only call PlayerAwards for new seasons.
- **Birthdate**: only in CommonPlayerInfo. Only needed for age prompts, so it can be
  fetched for the players those prompts use, or dropped.
- **Jersey numbers by team**: only in CommonTeamRoster (one per team-season, ~1,600).
  Could be optional, or fetched once and cached forever since old rosters don't change.

## Request count

| Version | Requests | Time at ~1.75 s |
|---|---|---|
| Today | ~16,600 | ~8 h of requests (4-5 h in practice with cache hits) |
| Bulk: bio + draft + seasons + champions | ~115 | ~4 min |
| ...plus awards, filtered | +2,500-3,000 | +~1.5 h |
| ...plus jersey history | +1,600 | +~50 min (one time, then cached) |
| ...plus birthdates | +5,000 | +~2.5 h (or skip) |

So yes: the core data drops from ~15,000 requests to ~115. It only becomes "a few hundred
total" if awards are filtered or kept in a table, and jerseys and birthdates are cached or
dropped. Yearly updates get very cheap, too: one new season is ~2 game-log requests,
PlayerIndex, DraftHistory, and awards for that season's players.

## Risks to check first

- **Old seasons.** NBA.com's player game logs may be thin or missing for the 1970s.
  If so, fall back to per-player data only for players whose careers start before the
  first complete season.
- **Response size.** A season's player game log is ~25,000 rows (several MB). It's fine,
  but cache it gzipped.
- **PlayerIndex coverage.** Check that `historical_nullable=1` really returns everyone
  from 1970 on, with draft and college filled in for old players.
- **Team ids.** Game logs use the same team ids as PlayerCareerStats, so the franchise
  merge (Sonics -> OKC, etc.) should work unchanged.

## Build steps (when we do it)

1. **Probe** (`probe_bulk.py`, run once on the Mac, ~10 requests). Call PlayerIndex,
   DraftHistory, and LeagueGameLog for 1970-71, 1985-86, 2000-01 and 2024-25, then save
   the raw responses and print row counts and sample rows.
2. **Validate against this run.** Build `s`, `ps` and `CH` from game logs and compare
   them with the `players.json` this run produced (games, points and teams per season).
   Report any differences, especially in old seasons and trade seasons.
3. **Build `build_players_v2.py`** using bulk endpoints for bio, draft and seasons, and
   per-player calls only for awards (filtered), jerseys (cached) and birthdates (optional).
   Same output format, so `index.html` doesn't change.
4. **Keep the current scripts** as the fallback until v2 matches on a full run.
