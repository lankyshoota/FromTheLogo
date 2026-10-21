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
- Player data: `build_players.py` pulls from NBA.com via the `nba_api` package -> `players.json`.
  Do NOT scrape Basketball Reference (their terms forbid it).
- Goal is $0 to run. Only optional cost later is a domain.

## Files
- `index.html` — the full playable prototype (game, animation, scoring, all in one file).
  Currently uses a hand-typed ~230-player list in the `RAW` string and simulated pick rates.
- `build_players.py` — builds `players.json` (every player since 1970-71). Resumable, caches
  to `./cache`. Takes ~2 hours. Fields per player: id, n (name), t (franchises, current
  abbreviations), g (tag letters), s (seasons: [year, team, gp, pts, reb, ast]), aw (award
  counts: AS, MVP, FMVP, CH, DPOY, ROY, 6MOY, ALLNBA, ALLDEF, ALLROOK, POM, plus ALLNBA1 /
  ALLDEF1 / ALLROOK1 for first-team), ps (playoff seasons, same format as s), dr ([year,
  round, pick] or null = undrafted), c (country), pos, col (college, "" if none), ht (inches),
  wt (lbs), bd (birthdate "YYYY-MM-DD"), num ([[team, jersey], ...] from season rosters),
  from, to, gp, ppg, p (popularity 1-10).
  Run on Lanky's own computer (NBA.com isn't reachable from the cloud session), then commit
  players.json.
- `supabase/schema.sql` — tables + pick-rate view. Paste into Supabase SQL editor.

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
3. Auto-generate prompts from the data (team pairs, awards, draft, college, country,
   stat thresholds like "averaged 20+ PPG for the Kings"). Every prompt must have enough
   valid answers, including some deep cuts.
4. Supabase: daily pack per date, store picks, compute real pick rates.
5. Tune tier ranges with real data.

## Open questions (ask Lanky)
- Cold start: early players each day have almost no data. Plan: blend the popularity-based
  estimate with real picks until a prompt has enough answers (e.g. 50+).
- When is rarity locked? At submit time (instant feedback, noisy early) vs. recalculated at
  end of day.
- Anti-cheat (one submission per device per day) — basic version first.
