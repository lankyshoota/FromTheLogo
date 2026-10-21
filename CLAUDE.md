# FromTheLogo

Daily NBA deep-cut web game. Krillion-style rarity scoring (krillion.io) combined with
HoopGrids-style NBA prompts (hoopgrids.com). Name a player who fits each prompt; the fewer
people who pick the same player, the more it's worth. A basketball player animation starts
at a layup and backs up further from the hoop as your score grows.

Owner: Lanky (GitHub: lankyshoota). Casual, direct communication. Show progress in small
steps and check in before big changes.

## Stack
- Plain HTML/CSS/JS, no framework, no build step. Hosted free on GitHub Pages.
- Supabase (free tier) for the backend: daily packs + everyone's picks -> live pick rates.
- Player data: `build_players_bulk.py` + `fill_details.py` pull from NBA.com via the `nba_api`
  package -> `players.json`. Lanky downloads the repo as a ZIP (~/Downloads/FromTheLogo-main, in
  a venv), so give him `curl -fsSLO https://raw.githubusercontent.com/lankyshoota/FromTheLogo/main/<file>`
  commands, not git pull.
  Do NOT scrape Basketball Reference (their terms forbid it).
- Goal is $0 to run. Only optional cost later is a domain.

## Files
- `index.html` — the full playable prototype (game, animation, scoring, all in one file).
  Loads `players.json` at startup (falls back to `players.sample.json`, the old hand-typed
  starter list, if it's missing). Picks are tracked by player id, since some names are shared
  (two Larry Johnsons); typing a shared name counts if any player with it fits.
- `prompts.js` — prompt generator, loaded by index.html (also runs in Node for tests). Builds
  every prompt the data supports (team pairs, team + decade, team stat seasons, titles with a
  team, undrafted/2nd-round/international/7-footer + team, college (+ team), country, draft pick
  and class, rings, career length, games, playoff games, career/season stats; award prompts too,
  but only once ~all players have "ad"). Keeps a prompt if it has 10+ answers, 3+ deep cuts
  (answers the simulated pick rates put under 2%) and 2+ well-known answers (popularity 6+).
  Difficulty by well-known answers: 20+ easy, 8-19 medium, 2-7 hard. Daily pack = 2 easy,
  3 medium, 2 hard, easy to hard; max one prompt per family (two team pairs allowed), no team
  twice. Prompt ids describe the prompt ("pair:LAL:MIA", "ppg:SAC:20", "col:Duke") so stored
  packs can always be rebuilt. Each prompt is hashed to one day of a 28-day cycle, so no repeats
  within 28 days (rarely 14 when a day borrows). Champions 1970-2024 are a fixed list; later
  seasons come from the data. Unlimited mode picks randomly with the same rules.
- `check_players.py` — sanity checks for players.json (team codes, impossible games, rings vs
  champions, misattributed old game-log rows) and, with ./cache, who's missing vs the old list.
- `players.sample.json` — 324-player fallback (fake negative ids). Delete once players.json
  is committed, or keep as a fallback.
- `build_players.py` — builds `players.json` (every player since 1970-71). Resumable, caches
  to `./cache`. Takes ~2 hours. Fields per player: id, n (name), t (franchises, current
  abbreviations), g (tag letters), s (seasons: [year, team, gp, pts, reb, ast]), aw (award
  counts: AS, MVP, FMVP, CH, DPOY, ROY, 6MOY, ALLNBA, ALLDEF, ALLROOK, POM, plus ALLNBA1 /
  ALLDEF1 / ALLROOK1 for first-team), ps (playoff seasons, same format as s), dr ([year,
  round, pick] or null = undrafted), c (country), pos, col (college, "" if none), ht (inches),
  wt (lbs), bd (birthdate "YYYY-MM-DD"), num ([[team, jersey], ...] from season rosters),
  from, to, gp, ppg, p (popularity 1-10), ad (1 = awards checked; only on players whose awards
  have been fetched, so award prompts can wait until nearly everyone has it).
  Run on Lanky's own computer (NBA.com isn't reachable from the cloud session), then commit
  players.json.
- `build_players_bulk.py` — the main builder now (per-player runs got blocked every ~25 players).
  PlayerIndex + DraftHistory + LeagueGameLog per season (regular + playoffs, player and team
  logs): ~150 requests. Season lines = game logs summed per player/team/season; champion = winner
  of the last playoff game; rings from that. Checks game logs against team box scores (seasons
  before 1985, and later ones that look short); players in seasons that don't add up use
  PlayerCareerStats. Reuses everything in ./cache. Writes players.json right after the bulk part.
  `Polite` class: paced, cached requests that pause on blocks (30s, 2m, 15m, 30m, then hourly) and
  never quit; empty answers are logged in cache/empty_answers.json and skipped after 3.
- `fill_details.py` — slow background filler for what bulk can't get: awards (likeliest winners
  first), birthdates/info, team rosters for jersey history. ~10-14 s per request + 5 min rest per
  40; merges into players.json every 25 items and on Ctrl+C; resumable. Takes a few nights.
- `fill_missing.py` — for the old per-player builder: run after build_players.py. ~4% of players get an empty answer from
  PlayerCareerStats every time (e.g. 1629152, 1824, 202079). Finds everyone in the cached
  player list who isn't in players.json, gets their seasons from PlayerProfileV2, then
  PlayerDashboardByYearOverYear, then per-season PlayerGameLogs, and builds them with
  build_player() (same cache). Backs up players.json to players.before_fill.json.
- `docs/bulk-data-plan.md` — the plan behind build_players_bulk.py / fill_details.py.
- `supabase/reset_test_data.sql` — wipes packs, picks, scores and transfer tokens (keeps
  accounts). Run after schema.sql.
- `supabase/schema.sql` — tables + RPC functions. Paste into Supabase SQL editor (safe to
  re-run, upgrades the earlier device-id version). Tables are locked (RLS, no policies); the
  browser only calls security-definer functions that validate inputs. Tier cutoffs/points and
  the 50-answer cold start are duplicated in pick_scores() — keep them in sync with index.html.

## Backend (Supabase project irtsjiluvpbxgxvteots, publishable key is in index.html)
- **Daily pack:** every browser builds the same pack from the date; the first player of the
  day stores it with get_or_create_pack and everyone else gets the stored one. No cron.
- **Pack dates** use the player's local date. A date is open while it's that date somewhere
  on Earth: until 00:00 UTC two days later. After that its picks are final.
- **Live score:** each daily pick is sent right away; submit_pick returns that player's count
  and the prompt total, and the pick is scored immediately.
- **Cold start:** rate = w * real + (1 - w) * simulated, w = min(1, answers / 50).
- **Settling:** once a date closes, the game re-scores that pack from get_pick_rates on the
  player's next visit and shows "Your <date> pack settled: X final (live score was Y)".
- **One per user:** one pick per prompt per user and one submission per user per day (unique
  keys). A second device loads what the account already did today (get_my_day). Skipped
  prompts (bricks) only reach the server when the pack is finished. Unlimited mode never saves.
- **Accounts (Supabase Auth):** first visit shows a welcome sheet: "Play as guest" (anonymous
  sign-in) or "Log in / Sign up" (email + password, or Google). Everyone is a real auth user,
  so guest picks have a user id from the first pick.
  - Guest -> sign up keeps the same user: updateUser({email}) then password. If "Confirm
    email" is on, the guest keeps playing; the email link brings them back to set a password
    (user_metadata.needs_password). Username rides in user_metadata until it's set.
  - Guest -> Google: linkIdentity. If that Google account already has its own account
    (identity_already_exists), the game signs into it instead and claims the guest's history.
  - Guest -> log in to an existing account: start_transfer() token first, then the account
    calls claim_transfer() after login. The account keeps its own picks on days both played.
  - Usernames: profiles table, 3-20 letters/numbers/_, unique case-insensitive.
  - Saved packs in localStorage are per user: fromthelogo:<user id>:<date>.
- **Stats page** (accounts only): get_my_stats() computes everything server-side from picks:
  packs played, current/best streak, avg/best score, best distance, total feet, cards per
  tier, rarest pull (player, prompt, %), history. Picks store sim_pct so the server can apply
  the same cold-start blend.
- **Offline:** every call times out (~4s). No Supabase = simulated rates; unsent picks are
  retried on the next visit while the date is open.

## Game rules (decided — don't change without asking)
- **7 prompts per day**, same for everyone. Plus "Rip a new pack" unlimited mode.
- **One prompt at a time, 20-second clock** per prompt. Clock starts when the prompt appears.
  "Start pack" button so it doesn't start on page load. Deadline is saved so refreshing
  doesn't reset the clock.
- **3 tries per prompt.** Wrong player = strike. 3 strikes or time out = brick (0 pts).
  A player can only be used once per pack.
- **No autocomplete dropdown.** Type the full name, Enter or Shoot button submits.
  Misspelled/partial name doesn't count as a try; shows "Did you mean X?" button.
  Clicking it fills the input but does NOT submit. Last-name-only typing triggering
  "did you mean" is fine (decided).
- **Only NBA from the 1970-71 season onward.** Franchise history merges (Sonics = Thunder,
  New Jersey = Nets, Vancouver = Grizzlies, Bullets = Wizards, etc.).

## Scoring (decided)
Six card tiers by pick rate, flat points, all ending in 0 or 5:

| Tier | Picked by | Points |
|---|---|---|
| Base | 25%+ | 10 |
| Insert | 10–25% | 25 |
| Refractor | 5–10% | 40 |
| Numbered /99 | 2–5% | 60 |
| Gold /10 | 0.5–2% | 80 |
| 1 of 1 | under 0.5% | 100 |

- Ranges are a first guess — tune them once real pick data exists. Points stay as is.
- **1 point = 1 foot.** The shooter's distance from the rim = total points (min 2 ft layup).
  700 max = ~7 courts back. Court markings repeat every 94 ft.
- Share card: emoji row per tier + score + distance.

## Look
Trading-card binder vibe: dark plum background, gold accent, Big Shoulders Display (headings),
Barlow Semi Condensed (body), IBM Plex Mono (labels). Cards styled per tier (silver /99,
gold /10, black-and-gold 1 of 1 with spinning shine). Canvas animation of the shooter on a
wood court with crowd parallax.

## Roadmap
1. Get the project on GitHub + GitHub Pages.
2. Run `build_players.py`, load `players.json` into the game instead of `RAW`.
3. Auto-generate prompts from the data (done: prompts.js). Award prompts switch on by themselves
   once fill_details.py has checked awards for ~99% of players.
4. Supabase: daily pack per date, store picks, compute real pick rates.
5. Tune tier ranges with real data.

## Open questions (ask Lanky)
- Anti-cheat: guests can make a new anonymous user by clearing site data. Basic version for now
  (optional later: Supabase CAPTCHA on anonymous sign-ins).
- Old anonymous users pile up; picks survive user deletion (user_id set null), so a periodic
  cleanup of stale guests is safe.
