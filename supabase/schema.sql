-- FromTheLogo — Supabase setup
-- Paste this whole file into Supabase: SQL Editor -> New query -> Run.

-- One row per day: the 7 prompts everyone gets.
create table if not exists daily_packs (
  pack_date  date primary key,
  prompts    jsonb not null,            -- array of 7 prompt ids / definitions
  created_at timestamptz not null default now()
);

-- Every answer anyone submits.
create table if not exists picks (
  id          bigint generated always as identity primary key,
  pack_date   date not null references daily_packs (pack_date) on delete cascade,
  prompt_idx  smallint not null check (prompt_idx between 0 and 6),
  player_id   bigint not null,          -- NBA.com player id from players.json
  device_id   uuid not null,            -- random id saved in the player's browser
  created_at  timestamptz not null default now(),
  unique (pack_date, prompt_idx, device_id)   -- one answer per prompt per device
);

create index if not exists picks_prompt on picks (pack_date, prompt_idx, player_id);

-- Live pick rates: what % of people picked each player for each prompt.
create or replace view pick_rates as
select
  pack_date,
  prompt_idx,
  player_id,
  count(*)::int as picks,
  round(100.0 * count(*) / sum(count(*)) over (partition by pack_date, prompt_idx), 2) as pct
from picks
group by pack_date, prompt_idx, player_id;

-- Security: anyone can read packs and rates and submit picks; nobody can edit or delete.
alter table daily_packs enable row level security;
alter table picks enable row level security;

drop policy if exists "read packs" on daily_packs;
create policy "read packs" on daily_packs for select to anon, authenticated using (true);

drop policy if exists "submit picks" on picks;
create policy "submit picks" on picks for insert to anon, authenticated with check (true);

drop policy if exists "read picks" on picks;
create policy "read picks" on picks for select to anon, authenticated using (true);

grant select on pick_rates to anon, authenticated;
