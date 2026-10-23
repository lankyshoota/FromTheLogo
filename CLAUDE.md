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
- **Link previews + home screen:** index.html has Open Graph / Twitter tags pointing at
  https://lankyshoota.github.io/FromTheLogo/og.png (absolute URL; change og:url/og:image if the
  site moves to a domain). `og.png` (1200x630: wordmark + the six tier cards), `icon-512/192/180/32.png`
  (FTL in a gold center-court ring; maskable-safe) and `manifest.webmanifest` (standalone,
  start_url ./). Rendered from the game's own CSS with Playwright (cloud session); no service
  worker on purpose (Chromium installs without one, and a cache could serve a stale game).
- `admin.html` — private dashboard (noindex). Uses the game's Supabase session (log in on the
  game first). Server-side gate: get_admin_dashboard() and set_report_status() raise unless
  is_admin() (a non-guest account in the admins table). Shows: packs-left warning (< 60 days,
  red < 14), tiles (players/finished today, new players, signups, D1/D7 retention, avg score,
  packs scheduled, open reports), daily players (returning vs new columns), average score
  (easy vs hard lines), score distribution heatmap (share of each day's finishers per 50 pts),
  by-day table (also the charts' table view), most picked answers, highest brick rate, lowest
  score spread (stddev of points on the prompt, 5+ finishers), open reports with Fixed/Dismiss.
  7/14/30/90-day range. All dates are ET. Signup date = first email/Google identity.
- **Analytics:** GoatCounter (no cookies, no banner): set GOATCOUNTER in index.html to the site
  code once Lanky has signed up; empty = off. Test mode and localhost don't count.
- `rarity.js` — rarity model (browser + Node). Recognition (0-1) = weighted mix of feature
  scales, each scaled 0-1 between its 1st and 99.9th percentile (log scales, not percentiles):
  views (Wikipedia, last 12 months), legacy (average yearly views since July 2015, so legends
  aren't buried under this season's hype), langs (language Wikipedias with an article), playoff
  games, seasons, awards (rings until awards arrive), recency. Missing legacy/langs data counts
  as views. Team prompts (team, pair, team + decade): recognition is scaled down by how weakly the
  player is tied to the team(s): tie = seasons there (/10), games there (/700), share of career
  there, playoff games there (/40); recognition * (1 - team * (1 - tie)). So Jordan/Pippen/Rodman
  are Bulls, Wade and Boozer barely are, and famous one-season stops score higher than lifers.
  Simulated fans (4,000 per prompt, seeded by prompt id): each has a knowledge shift and a
  deep-cut appetite (random^6, so few hunt deep cuts); recalls each answer with
  sigmoid(steep * (recognition - center + shift)), picks among recalled with weight
  exp(pull * recognition). update(): one self-correcting step from a settled day (softmax gradient
  on the weights, pull nudged by how bunched real picks were; steps capped). Default model (v2):
  views .5, legacy .2, langs .1, playoffs .12, seasons .08, awards .12, recency .08, center .97,
  steep 30, spread .1, pull 6, team .3. Don't over-engineer: real picks correct the rest.
- `fetch_fame.py` — run on Lanky's Mac. Wikidata (P3647 = NBA.com id) maps ids to English
  Wikipedia articles and gives each one's language count; the rest via Wikipedia search (title
  must be the name, about basketball) plus its language links. One page-view request per player
  (monthly since July 2015) gives fame (last 12 months) and fame10 (average per year). Cached in
  cache/fame/ (refreshes after 30 days, --refresh for all; old entries without fame10 refetch),
  ~5 req/s, proper User-Agent.
- `test_rarity.js` — sanity test, also run by .github/workflows/rarity.yml on every push that
  touches players.json/rarity.js/prompts.js. Fails on: franchise faces not the clear #1 on their
  team prompt (Silver+ and 1.25x the #2; Jordan/Bulls, Bird/Celtics, Duncan/Spurs, Dirk/Mavs); team legends not Silver (Pippen);
  famous short stops not rarer than the lifer (Wade vs Pippen, Shaq vs Pierce on the Celtics, Nash
  vs Fisher on the Lakers); superstars (top 50) under 2% on prompts with at most 10 superstars
  (unless barely tied to the team: tie under 0.35, e.g. Pau on the Spurs); the 3 least known answers not under 2%. Warns (counts only)
  on well-known role players below Silver as a top-3 answer or under 2% in the top 10 (mostly team
  pairs; real picks settle these). Prints the top 8 of Bulls/Celtics/Lakers (EX1-3 to change).
- `prompts.js` — prompt generator, loaded by index.html (also runs in Node for tests).
  Rule (Lanky): easy to answer, hard to answer well. Anyone should be able to name someone;
  the skill is going deep. So no stacked filters (college + team, country + team, 7-footer +
  team, undrafted/2nd-round + team, team stat seasons...). Generated kinds: single team, team
  pairs, team + decade, big colleges, big countries, No. 1 picks, second-rounders, undrafted,
  draft classes, rings (1+/2+/3+), career length, 7+/9+ teams, 1,000+ games, playoff games,
  career/season scoring, career points; awards (aw:X only) once ~all players have "ad". Older
  kinds stay parseable so saved packs load, but aren't generated.
  Keeps a prompt only with 30+ answers, 5+ widely known answers (top 16% by recognition under
  the default model) and 10+ deep cuts (not widely known AND under 2% in rarity.js's quick
  estimate). Difficulty by widely known answers: 25+ easy, 12-24 medium, 5-11 harder.
  Daily pack = 8: 3 easy, 4 medium, at most 1 harder (easy/medium never fall back to harder);
  one prompt per family (two team pairs), no team twice. Packs are built in order from LAUNCH
  (2026-10-22), each skipping everything used in the previous 27 days, so no repeats within 28
  days; if fresh prompts run out it first allows a family to repeat, only then a recent prompt
  (never happens with the real data). Same data -> same packs for everyone. Prompt ids describe
  the prompt ("pair:LAL:MIA", "col:Duke") so stored packs can always be rebuilt. Champions
  1970-2024 are a fixed list; later seasons come from the data. Test mode (?test=1) picks
  randomly with the same rules.
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
  have been fetched, so award prompts can wait until nearly everyone has it), fame (Wikipedia
  page views in the last 12 months, from fetch_fame.py; 0 = no article), fame10 (average views
  per year since July 2015), langs (language Wikipedias with an article).
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
  first), then team rosters for jersey history. Birthdates/info are skipped by default
  (Lanky doesn't need them; `--only awards,birthdates,jerseys` brings them back). ~10-14 s per request + 5 min rest per
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
- **Daily pack (server-only):** `make_packs.js` builds every daily pack from LAUNCH to a year
  ahead with prompts.js + players.json and writes `supabase/packs.sql` (Lanky pastes it into
  Supabase). get_or_create_pack(date) only returns the stored pack (p_prompts is ignored), so
  browsers can't upload a fake pack; a date with no stored pack errors and the game plays
  offline. Re-running packs.sql replaces packs for dates without picks only. Regenerate before
  the schedule runs out (admin page shows it) or to bring new data (award prompts) into future
  packs. The browser still builds the same pack itself for offline play.
- **CAPTCHA:** Cloudflare Turnstile on guest sign-in (signInAnonymously, which also starts
  every email signup) and email log-in (signInWithPassword), via Supabase Auth > Attack
  Protection. TURNSTILE_KEY (public site key) in index.html; empty = no CAPTCHA. Widget is
  invisible unless Cloudflare wants a click (then it shows inside the open sheet). Google
  sign-in doesn't need it.
- **Reports:** "Report a wrong answer" in the per-prompt reveal (daily, online): kind wrong
  (counted but doesn't fit) or missing (fits but wasn't accepted), player (id if the name
  matches), optional note. submit_report() -> reports table (locked; one per player/prompt/kind
  per user, 30 a day max). Read on the admin page.
- **One global day, Eastern time (Lanky).** Pack date = today in America/New_York (DST
  included), decided by the server: ftl_today() in SQL; the game syncs its clock from get_today()
  at startup (clockSkew) so a wrong device clock can't pick the date; device clock only when
  offline. Everyone gets the new pack at midnight ET. A date is open on its own ET day plus a
  5-minute grace after midnight (pack_date_open; GRACE in index.html) so a pack in progress can
  finish; after that its picks are final and it settles. get_or_create_pack refuses future dates
  (no peeking). The results countdown counts to midnight ET; at midnight the page reloads into
  the new pack by itself unless you're mid-pack. Streaks (get_my_stats) use ftl_today().
  packs.sql rows are ET calendar dates. Packs are still predictable by anyone who runs
  prompts.js (the generator is public); server packs only stop fake uploads.
- **Live score:** each daily pick is sent right away; submit_pick returns that player's count
  and the prompt total, and the pick is scored immediately.
- **Pick rate:** Bayesian blend: rate = (picks + K * simulated share) / (all picks + K), K = 100.
  The simulated share comes from rarity.js; each pick stores it (sim_pct) so the server scores the
  same way (pick_scores).
- **Fame model (self-correcting):** fame_models table, one model per settled day. On load the
  game gets the latest (get_fame_model); the first player after a day settles computes the next
  one from that day's picks (get_closed_day + rarity.js update) and proposes it
  (propose_fame_model: first valid one wins; must be the next day, within bounds, weights within
  0.06 and pull within 16% of the previous, other settings unchanged; an older model without
  the v2 keys is treated as the v2 default).
- **Settling:** once a date closes (midnight ET + 5 min), the game re-scores that pack from get_pick_rates on the
  player's next visit and shows "Your <date> pack settled: X final (live score was Y)".
- **One per user:** one pick per prompt per user and one submission per user per day (unique
  keys). A second device loads what the account already did today (get_my_day). Skipped
  prompts (bricks) only reach the server when the pack is finished. Test mode never saves.
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
  retried on the next visit while the date is open. The game keeps retrying get_or_create_pack on every
  visit, even mid-pack: if the stored pack matches, it goes live and sends the picks made offline.
  The reason it's offline (library didn't load, guest sign-in off, RPC error...) shows in the
  footer and the Offline panel.
- **Results screen (daily only):** get_day_board(date) recomputes everyone's current score from
  picks (same blend as pick_scores) and returns players, a histogram in 10-point buckets, my rank
  and how many I beat, and the deepest real pick per prompt. Only counts/scores, no users. The
  game shows "You beat X%" + "#rank of N", a score curve (bars of 50, smoothed curve, my marker,
  "top 10%" / "top half" labels once 20+ players; "Early: N players so far" before that),
  refreshed every minute until the day settles; then the per-prompt reveal: my pick (%, tier),
  most picked, deepest pull today, 3 deepest valid answers. Pick % shows on cards and is tagged
  live / final / est.
- **"?" button** (header) opens the how-to panel with the tier table and one line per mode.
- **Modes in Supabase:** submissions.mode ('easy'/'hard', default 'hard' = packs from before
  modes). submit_score(date, total, mode); get_my_day returns the day's mode (a second device
  takes it over once the pack is submitted); get_day_board(date, mode) ranks within my mode
  (my submission's mode wins over the argument) and also returns all_players; get_my_stats
  history has mode per pack. Mode is only stored at submission.

## Game rules (decided — don't change without asking)
- **One pack a day. Core rule.** No unlimited mode in the public game. After the daily pack the
  results screen counts down to the next one ("Next pack in 6h 12m", local midnight). The only
  way to play more is the hidden test mode for Lanky: ?test=1 in the URL shows a "Test pack (not
  saved)" tab with unlimited practice packs on simulated rates; test packs never save picks.
- **8 prompts per pack** (was 7; changed by Lanky), same for everyone. index.html uses PACK_SIZE
  from prompts.js; schema.sql allows prompt_idx 0-7, totals up to 800 and 8-prompt packs.
- **One prompt at a time, 20-second clock** per prompt. Clock starts when the prompt appears.
  "Start pack" button so it doesn't start on page load. Deadline is saved so refreshing
  doesn't reset the clock.
- **3 tries per prompt.** Wrong player = strike. 3 strikes or time out = brick (0 pts).
  A player can only be used once per pack.
- **Two modes (Lanky, replaces the old "no dropdown" rule)**, chosen on the start panel before the
  daily pack and locked for that day; the last choice is the default next time (new players: easy).
  - **Easy:** from 2 letters, a dropdown of up to 6 names from ALL players, matched by name only
    (never filtered by who fits: that would give answers away). Best matches first: full-name
    start, then last-name start, then better known. Accents/punctuation ignored. Tapping a name
    fills the input (arrow keys + Enter too); Enter or Shoot submits.
  - **Hard:** no dropdown, type the full name. Badge "🔥 HARD" on the prompt, results screen,
    share text and Stats history. No bonus points.
  - Both: Enter or Shoot submits. Misspelled/partial name doesn't count as a try; shows "Did you
    mean X?" button that fills the input but does NOT submit. Last-name-only typing triggering
    "did you mean" is fine (decided).
  - Picks from both modes count toward the same pick rates. The results board (curve,
    percentile, rank) only compares players in your mode.
- **Only NBA from the 1970-71 season onward.** Franchise history merges (Sonics = Thunder,
  New Jersey = Nets, Vancouver = Grizzlies, Bullets = Wizards, etc.).

## Scoring (decided)
Six card tiers by pick rate, flat points, all ending in 0 or 5 (renamed by Lanky; same ranges):

| Tier | Picked by | Points | Emoji | id |
|---|---|---|---|---|
| Copper | 25%+ | 10 | 🟫 | copper |
| Silver | 10–25% | 25 | ⬜ | silver |
| Gold | 5–10% | 40 | 🟨 | gold |
| Emerald | 2–5% | 60 | 🟩 | emerald |
| Refractor | 0.5–2% | 80 | 🌈 | refr |
| 1 of 1 | under 0.5% | 100 | 💎 | one |

Old saved packs (before the rename) used base/ins/refr/num/gold/one; index.html maps them on load.

- Ranges are a first guess — tune them once real pick data exists. Points stay as is.
- **1 point = 1 foot.** The shooter's distance from the rim = total points (min 2 ft layup).
  800 max = ~8.5 courts back. Court markings repeat every 94 ft.
- Share card: emoji row per tier + score + distance.

## Look
Trading-card binder vibe: dark plum background, gold accent, Big Shoulders Display (headings),
Barlow Semi Condensed (body), IBM Plex Mono (labels). Cards get fancier with each tier: matte
copper, brushed silver, gold foil with a shine sweep, green holo shimmer, animated rainbow
refractor, black-and-gold 1 of 1 with spinning shine, glow and its own flip. Reduced motion turns
every animation off (including ::before/::after). Canvas animation of the shooter on a
wood court with crowd parallax.

## Roadmap
1. Get the project on GitHub + GitHub Pages.
2. Run `build_players.py`, load `players.json` into the game instead of `RAW`.
3. Auto-generate prompts from the data (done: prompts.js). Award prompts switch on by themselves
   once fill_details.py has checked awards for ~99% of players.
4. Supabase: daily pack per date, store picks, compute real pick rates.
5. Tune tier ranges with real data.

## Before public launch (remind Lanky)
- Turnstile CAPTCHA is built but on hold: Cloudflare widget for lankyshoota.github.io -> site key
  into TURNSTILE_KEY -> deploy -> Supabase Attack Protection: CAPTCHA on with the secret key.
- GoatCounter site code into GOATCOUNTER (if not done).

## Open questions (ask Lanky)
- Anti-cheat: guests can make a new anonymous user by clearing site data. Basic version for now
  (optional later: Supabase CAPTCHA on anonymous sign-ins).
- Old anonymous users pile up; picks survive user deletion (user_id set null), so a periodic
  cleanup of stale guests is safe.
