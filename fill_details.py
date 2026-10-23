"""
FromTheLogo — slowly fetch what the bulk endpoints can't give us:

  1. awards (All-Star, MVP, Finals MVP, All-NBA, All-Defensive, All-Rookie,
     DPOY, ROY, 6MOY, Player of the Month, rings), likeliest winners first
  2. jersey numbers for every team-season (team rosters)
  (birthdates are skipped unless you ask for them: --only awards,birthdates,jerseys)

It's meant to run in the background over a few nights:
  - one request every ~10-14 s, plus a 5-minute rest every 40 requests,
    which is well under what got the per-player run blocked
  - if NBA.com blocks it anyway, it pauses (15 min, 30, then hourly) and
    carries on more slowly; it never quits on its own
  - every answer is saved in ./cache as it arrives, and players.json is
    updated every 25 items, so Ctrl+C (or a closed laptop) loses nothing;
    run it again and it continues where it left off

Run build_players_bulk.py first (it writes players.json), then:
  caffeinate -i python3 fill_details.py            # Mac: stay awake while it runs
  python3 fill_details.py --only awards            # just awards
  python3 fill_details.py --delay 15               # slower, if it still gets blocked
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from nba_api.stats.endpoints import commonplayerinfo, commonteamroster, playerawards

import build_players as bp
import build_players_bulk as bulk

DELAY = 10.0                 # seconds between requests (plus up to 40% jitter)
REST_EVERY = 40              # requests between rests
REST = 300                   # seconds of rest
SAVE_EVERY = 25              # update players.json after this many items


def likely_awards(r: dict) -> float:
    """Rough chance of having won something: big scoring seasons, long careers,
    deep playoff runs, high picks and busy rookie years come first."""
    s = r["s"]
    best_ppg = max((x[3] / x[2] for x in s if x[2] >= 40), default=0)
    rookie_gp = sum(x[2] for x in s if x[0] == s[0][0])
    high_pick = r["dr"] and r["dr"][1] == 1 and r["dr"][2] <= 10
    return (best_ppg * 2 + sum(x[3] for x in s) / 1000 + sum(x[2] for x in r["ps"]) / 10
            + len({x[0] for x in s}) / 2 + (6 if high_pick else 0) + (4 if rookie_gp >= 60 else 0))


def merge():
    """Rebuild players.json from itself plus everything now in ./cache."""
    players = json.loads(bp.OUT.read_text())
    for r in players:
        r["_jersey"] = r["num"][-1][1] if r["num"] else ""
        bulk.apply_cached_details(r, r["aw"].get("CH", 0))
    bulk.write_players(players)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="awards,jerseys",
                    help="comma-separated: awards, birthdates, jerseys (default: awards,jerseys, in that order; "
                         "birthdates only when listed)")
    ap.add_argument("--delay", type=float, default=DELAY, help=f"seconds between requests (default {DELAY:g})")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",")}

    if not bp.OUT.exists():
        sys.exit("players.json not found. Run build_players_bulk.py first.")
    players = json.loads(bp.OUT.read_text())
    ranked = sorted(players, key=likely_awards, reverse=True)
    pol = None

    # every request still to make: (cache name, how to fetch it, label)
    todo = []
    if "awards" in only:
        todo += [(f"awards_{r['id']}", lambda pid=r["id"]: playerawards.PlayerAwards(
            player_id=pid, timeout=30).get_dict(), "awards") for r in ranked]
    if "birthdates" in only:
        todo += [(f"info_{r['id']}", lambda pid=r["id"]: commonplayerinfo.CommonPlayerInfo(
            player_id=pid, timeout=30).get_dict(), "birthdates") for r in ranked]
    if "jerseys" in only:
        team_seasons = sorted({(bulk.TEAM_ID_BY_ABBR[t], y) for r in players for y, t, *_ in r["s"]
                               if t in bulk.TEAM_ID_BY_ABBR}, key=lambda x: (-x[1], x[0]))
        todo += [(f"roster_{tid}_{y}", lambda tid=tid, y=y: commonteamroster.CommonTeamRoster(
            team_id=tid, season=bp.season_str(y), timeout=30).get_dict(), "jerseys") for tid, y in team_seasons]
    pol = bulk.Polite(delay=args.delay, min_delay=args.delay)
    todo = [t for t in todo if bulk.cached(t[0]) is None and not pol.gave_up(t[0])]
    left = {k: sum(1 for t in todo if t[2] == k) for k in ("awards", "birthdates", "jerseys") if k in only}
    if not todo:
        print(f"Nothing left to fetch: {', '.join(sorted(only))} all cached.")
        merge()
        return

    per_item = args.delay * 1.2 + REST / REST_EVERY
    print(f"To fetch: {', '.join(f'{n} {k}' for k, n in left.items())} "
          f"(~{len(todo) * per_item / 3600:.0f} h at this pace, not counting any blocks).\n")

    done = {k: 0 for k in left}
    since_save, since_rest, t0 = 0, 0, time.time()
    try:
        for name, call, kind in todo:
            try:
                pol.get(name, call)
            except bp.BadResponse:
                pass   # counted in cache/empty_answers.json; asked again next run, up to MAX_EMPTY times
            done[kind] += 1
            since_save += 1
            since_rest += 1
            if since_save >= SAVE_EVERY:
                merge()
                since_save = 0
                remaining = sum(left.values()) - sum(done.values())
                print(f"[{time.strftime('%H:%M')}] " + " · ".join(f"{k} {done[k]}/{left[k]}" for k in left)
                      + f" · players.json updated · ~{remaining * (pol.delay * 1.2 + REST / REST_EVERY) / 3600:.1f} h left")
            if since_rest >= REST_EVERY:
                time.sleep(REST)
                since_rest = 0
    except KeyboardInterrupt:
        merge()
        sys.exit("\nStopped. players.json is up to date with everything fetched; run again to continue.")
    merge()
    gave_up = [t[0] for t in todo if pol.gave_up(t[0])]
    print(f"\nDone in {(time.time() - t0) / 3600:.1f} h. players.json has all the {', '.join(sorted(only))} "
          f"NBA.com gave us.")
    if gave_up:
        print(f"{len(gave_up)} items came back empty {bulk.MAX_EMPTY} times and won't be asked again "
              f"(delete {bulk.EMPTY_LOG} to retry them): {', '.join(gave_up[:15])}{' ...' if len(gave_up) > 15 else ''}")
    retry = sum(1 for t in todo if bulk.cached(t[0]) is None and not pol.gave_up(t[0]))
    if retry:
        print(f"{retry} items came back empty this time; run again later to retry them.")


if __name__ == "__main__":
    main()
