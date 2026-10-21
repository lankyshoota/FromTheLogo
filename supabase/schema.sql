-- FromTheLogo — Supabase setup
-- Paste this whole file into Supabase: SQL Editor -> New query -> Run.
-- Safe to run again: it only creates what's missing and replaces the functions.
--
-- The browser never touches the tables directly. It calls the four functions at
-- the bottom (get_or_create_pack, submit_pick, get_pick_rates, submit_score),
-- which check their inputs and run with the owner's rights. Row level security
-- is on with no policies, so the publishable key can't read or write tables.

-- One row per day: the 7 prompts everyone gets. Created automatically by the
-- first player of the day (see get_or_create_pack), so there's nothing to run daily.
create table if not exists daily_packs (
  pack_date  date primary key,
  prompts    jsonb not null,            -- [{"id": "p12", "text": "..."}, ...] x7
  created_at timestamptz not null default now()
);

-- Every daily answer anyone submits. Unlimited mode never saves.
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

-- One finished daily pack per device (the live score at the time it finished).
create table if not exists submissions (
  pack_date   date not null references daily_packs (pack_date) on delete cascade,
  device_id   uuid not null,
  total       smallint not null check (total between 0 and 700),
  cards       jsonb not null,           -- per prompt: {"player_id", "pts"} or null for a brick
  created_at  timestamptz not null default now(),
  primary key (pack_date, device_id)
);

-- Lock the tables down: only the functions below get in.
alter table daily_packs enable row level security;
alter table picks       enable row level security;
alter table submissions enable row level security;
drop policy if exists "read packs"   on daily_packs;
drop policy if exists "submit picks" on picks;
drop policy if exists "read picks"   on picks;
drop view if exists pick_rates;        -- replaced by get_pick_rates()
revoke all on daily_packs, picks, submissions from anon, authenticated;

-- A pack date is open while it's that date somewhere on Earth (UTC-12 to UTC+14),
-- i.e. from UTC date-1 through UTC date+1. After that its picks are final.
create or replace function pack_date_open(p_date date)
returns boolean
language sql stable
set search_path = public
as $$
  select p_date between (now() at time zone 'utc')::date - 1
                    and (now() at time zone 'utc')::date + 1;
$$;

-- Returns the day's 7 prompts. The first caller of the day proposes the pack
-- (every browser builds the same one from the date); later callers get that one.
create or replace function get_or_create_pack(p_date date, p_prompts jsonb)
returns jsonb
language plpgsql security definer
set search_path = public
as $$
declare
  existing jsonb;
begin
  select prompts into existing from daily_packs where pack_date = p_date;
  if existing is not null then
    return existing;
  end if;
  if not pack_date_open(p_date) then
    raise exception 'pack date % is not open', p_date;
  end if;
  if jsonb_typeof(p_prompts) <> 'array' or jsonb_array_length(p_prompts) <> 7
     or exists (select 1 from jsonb_array_elements(p_prompts) e
                where coalesce(jsonb_typeof(e -> 'id'), '') <> 'string' or length(e ->> 'id') > 64
                   or length(coalesce(e ->> 'text', '')) > 200) then
    raise exception 'a pack is 7 prompts with an id and text';
  end if;
  insert into daily_packs (pack_date, prompts) values (p_date, p_prompts)
  on conflict (pack_date) do nothing;
  return (select prompts from daily_packs where pack_date = p_date);
end;
$$;

-- Saves one pick and returns how that prompt has been answered so far
-- (this player's count and the total), so the game can score it right away.
-- A second pick for the same prompt from the same device is ignored.
create or replace function submit_pick(p_date date, p_prompt_idx smallint,
                                       p_player_id bigint, p_device uuid)
returns table (player_picks int, prompt_picks int)
language plpgsql security definer
set search_path = public
as $$
begin
  if not pack_date_open(p_date) then
    raise exception 'pack date % is closed', p_date;
  end if;
  insert into picks (pack_date, prompt_idx, player_id, device_id)
  values (p_date, p_prompt_idx, p_player_id, p_device)
  on conflict (pack_date, prompt_idx, device_id) do nothing;
  return query
    select count(*) filter (where player_id = p_player_id)::int, count(*)::int
    from picks where pack_date = p_date and prompt_idx = p_prompt_idx;
end;
$$;

-- Every player picked for a date, per prompt. Totals per prompt = sum of picks.
create or replace function get_pick_rates(p_date date)
returns table (prompt_idx smallint, player_id bigint, picks int)
language sql stable security definer
set search_path = public
as $$
  select prompt_idx, player_id, count(*)::int
  from picks where pack_date = p_date
  group by prompt_idx, player_id;
$$;

-- Records a finished daily pack. One per device per day; later calls are ignored.
create or replace function submit_score(p_date date, p_device uuid,
                                        p_total smallint, p_cards jsonb)
returns void
language plpgsql security definer
set search_path = public
as $$
begin
  if not pack_date_open(p_date) then
    raise exception 'pack date % is closed', p_date;
  end if;
  if jsonb_typeof(p_cards) <> 'array' or jsonb_array_length(p_cards) <> 7 then
    raise exception 'cards must be 7 entries';
  end if;
  insert into submissions (pack_date, device_id, total, cards)
  values (p_date, p_device, p_total, p_cards)
  on conflict (pack_date, device_id) do nothing;
end;
$$;

-- Supabase grants new functions to anon by default, so revoke first, then grant
-- only the four the game calls.
revoke all on function pack_date_open(date) from public, anon, authenticated;
revoke all on function get_or_create_pack(date, jsonb) from public, anon, authenticated;
revoke all on function submit_pick(date, smallint, bigint, uuid) from public, anon, authenticated;
revoke all on function get_pick_rates(date) from public, anon, authenticated;
revoke all on function submit_score(date, uuid, smallint, jsonb) from public, anon, authenticated;
grant execute on function get_or_create_pack(date, jsonb) to anon, authenticated;
grant execute on function submit_pick(date, smallint, bigint, uuid) to anon, authenticated;
grant execute on function get_pick_rates(date) to anon, authenticated;
grant execute on function submit_score(date, uuid, smallint, jsonb) to anon, authenticated;
