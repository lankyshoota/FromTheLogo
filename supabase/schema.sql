-- FromTheLogo — Supabase setup
-- Paste this whole file into Supabase: SQL Editor -> New query -> Run.
-- Safe to run again (and safe to run over the earlier device-id version).
--
-- The browser never touches the tables directly. It calls the functions below,
-- which check their inputs and run with the owner's rights. Row level security
-- is on with no policies, so the publishable key can't read or write tables.
--
-- Everyone who plays is a Supabase Auth user: guests are anonymous users, and
-- signing up later turns that same user into a real account.
--
-- Keep in sync with index.html / rarity.js / prompts.js: 8 prompts per pack (max 800),
-- the tier ids, cutoffs and points in pick_scores(),
-- the Bayesian prior K = 100, and the fame model bounds in propose_fame_model().

-- ---------------------------------------------------------------- tables

-- One row per day: the 8 prompts everyone gets. Created automatically by the
-- first player of the day (see get_or_create_pack), so there's nothing to run daily.
create table if not exists daily_packs (
  pack_date  date primary key,
  prompts    jsonb not null,            -- [{"id": "p12", "text": "..."}, ...] x7
  created_at timestamptz not null default now()
);

-- Usernames for real accounts (guests don't have one).
create table if not exists profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  username   text not null check (username ~ '^[A-Za-z0-9_]{3,20}$'),
  created_at timestamptz not null default now()
);
create unique index if not exists profiles_username_lower on profiles (lower(username));

-- Every daily answer. Unlimited mode never saves.
create table if not exists picks (
  id          bigint generated always as identity primary key,
  pack_date   date not null references daily_packs (pack_date) on delete cascade,
  prompt_idx  smallint not null check (prompt_idx between 0 and 7),
  player_id   bigint not null,          -- NBA.com player id from players.json
  user_id     uuid references auth.users (id) on delete set null,   -- picks outlive deleted users so rates don't change
  sim_pct     real not null default 0 check (sim_pct between 0 and 100),  -- simulated rate, for the cold-start blend
  created_at  timestamptz not null default now()
);
-- upgrade from the device-id version
alter table picks add column if not exists user_id uuid references auth.users (id) on delete set null;
alter table picks add column if not exists sim_pct real not null default 0 check (sim_pct between 0 and 100);
alter table picks drop constraint if exists picks_pack_date_prompt_idx_device_id_key;
alter table picks drop column if exists device_id;
create unique index if not exists picks_one_per_user on picks (pack_date, prompt_idx, user_id);
create index if not exists picks_prompt on picks (pack_date, prompt_idx, player_id);
create index if not exists picks_user on picks (user_id, pack_date);

-- One finished daily pack per user (the live score when they finished).
do $$ begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'submissions' and column_name = 'device_id') then
    drop table submissions;             -- device-id version: replaced by user ids
  end if;
end $$;
create table if not exists submissions (
  pack_date   date not null references daily_packs (pack_date) on delete cascade,
  user_id     uuid not null references auth.users (id) on delete cascade,
  total       smallint not null check (total between 0 and 800),
  created_at  timestamptz not null default now(),
  primary key (pack_date, user_id)
);

-- 8 prompts per pack (it was 7): prompt positions 0-7, scores up to 800. Re-adding the checks
-- updates tables created by an earlier version of this file.
alter table picks drop constraint if exists picks_prompt_idx_check;
alter table picks add constraint picks_prompt_idx_check check (prompt_idx between 0 and 7);
alter table submissions drop constraint if exists submissions_total_check;
alter table submissions add constraint submissions_total_check check (total between 0 and 800);

-- A guest about to log in to an existing account gets a one-time token, so the
-- account can claim the guest's history afterwards.
create table if not exists transfer_tokens (
  token      uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);

-- The rarity model (rarity.js) for each settled day: recognition weights plus how fans
-- recall and pick. The first player after a day settles proposes the next one.
create table if not exists fame_models (
  model_date date primary key,          -- the settled day this model learned from
  model      jsonb not null,
  created_at timestamptz not null default now()
);

-- Lock the tables down: only the functions below get in.
alter table daily_packs     enable row level security;
alter table profiles        enable row level security;
alter table picks           enable row level security;
alter table submissions     enable row level security;
alter table transfer_tokens enable row level security;
alter table fame_models     enable row level security;
drop policy if exists "read packs"   on daily_packs;
drop policy if exists "submit picks" on picks;
drop policy if exists "read picks"   on picks;
drop view if exists pick_rates;
revoke all on daily_packs, profiles, picks, submissions, transfer_tokens, fame_models from anon, authenticated;

-- ---------------------------------------------------------------- helpers

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

-- The signed-in user, or an error. Guests count (they're anonymous users).
create or replace function require_user()
returns uuid
language plpgsql stable
set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'not signed in';
  end if;
  return auth.uid();
end;
$$;

-- True for guests (anonymous users). Reads auth.users, not the token, so it's
-- right immediately after a guest links an account.
create or replace function is_guest()
returns boolean
language sql stable security definer
set search_path = public
as $$
  select coalesce((select is_anonymous from auth.users where id = auth.uid()), true);
$$;

-- Every pick by one user, scored: rate = (picks + K * simulated share) / (all picks + K),
-- K = 100 (sim_pct is the simulated % the game sent with the pick). Live while the date is
-- open, final after. Tiers: copper 25%+, silver 10-25, gold 5-10, emerald 2-5, refr 0.5-2, one.
create or replace function pick_scores(p_user uuid)
returns table (pack_date date, prompt_idx smallint, player_id bigint,
               pct real, tier text, pts int, final boolean)
language sql stable security definer
set search_path = public
as $$
  select m.pack_date, m.prompt_idx, m.player_id, r.pct,
         case when r.pct >= 25 then 'copper' when r.pct >= 10 then 'silver' when r.pct >= 5 then 'gold'
              when r.pct >= 2 then 'emerald' when r.pct >= 0.5 then 'refr' else 'one' end,
         case when r.pct >= 25 then 10 when r.pct >= 10 then 25 when r.pct >= 5 then 40
              when r.pct >= 2 then 60 when r.pct >= 0.5 then 80 else 100 end,
         not pack_date_open(m.pack_date)
  from picks m
  cross join lateral (
    select count(*) as total, count(*) filter (where o.player_id = m.player_id) as same
    from picks o where o.pack_date = m.pack_date and o.prompt_idx = m.prompt_idx
  ) c
  cross join lateral (select (100.0 * (c.same + m.sim_pct) / (c.total + 100))::real as pct) r
  where m.user_id = p_user;
$$;

-- ---------------------------------------------------------------- game

-- Returns the day's 8 prompts. The first caller of the day proposes the pack
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
  if jsonb_typeof(p_prompts) <> 'array' or jsonb_array_length(p_prompts) <> 8
     or exists (select 1 from jsonb_array_elements(p_prompts) e
                where coalesce(jsonb_typeof(e -> 'id'), '') <> 'string' or length(e ->> 'id') > 64
                   or length(coalesce(e ->> 'text', '')) > 200) then
    raise exception 'a pack is 8 prompts with an id and text';
  end if;
  insert into daily_packs (pack_date, prompts) values (p_date, p_prompts)
  on conflict (pack_date) do nothing;
  return (select prompts from daily_packs where pack_date = p_date);
end;
$$;

-- Saves one pick and returns how that prompt has been answered so far
-- (this player's count and the total), so the game can score it right away.
-- One pick per prompt per user; a second one is ignored.
drop function if exists submit_pick(date, smallint, bigint, uuid);
create or replace function submit_pick(p_date date, p_prompt_idx smallint,
                                       p_player_id bigint, p_sim_pct real)
returns table (player_picks int, prompt_picks int)
language plpgsql security definer
set search_path = public
as $$
declare
  me uuid := require_user();
begin
  if not pack_date_open(p_date) then
    raise exception 'pack date % is closed', p_date;
  end if;
  insert into picks (pack_date, prompt_idx, player_id, user_id, sim_pct)
  values (p_date, p_prompt_idx, p_player_id, me, greatest(0, least(100, coalesce(p_sim_pct, 0))))
  on conflict (pack_date, prompt_idx, user_id) do nothing;
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

-- Records a finished daily pack. One per user per day; later calls are ignored.
drop function if exists submit_score(date, uuid, smallint, jsonb);
create or replace function submit_score(p_date date, p_total smallint)
returns void
language plpgsql security definer
set search_path = public
as $$
declare
  me uuid := require_user();
begin
  if not pack_date_open(p_date) then
    raise exception 'pack date % is closed', p_date;
  end if;
  insert into submissions (pack_date, user_id, total)
  values (p_date, me, greatest(0, least(800, p_total)))
  on conflict (pack_date, user_id) do nothing;
end;
$$;

-- What the signed-in user already did on a date (from any device), so a second
-- device can't replay the daily pack.
create or replace function get_my_day(p_date date)
returns jsonb
language sql stable security definer
set search_path = public
as $$
  select jsonb_build_object(
    'submitted', exists (select 1 from submissions where pack_date = p_date and user_id = require_user()),
    'picks', coalesce((select jsonb_agg(jsonb_build_object('prompt_idx', prompt_idx, 'player_id', player_id,
                                                           'pct', pct, 'tier', tier, 'pts', pts))
                       from pick_scores(require_user()) where pack_date = p_date), '[]'::jsonb));
$$;

-- ---------------------------------------------------------------- accounts

create or replace function username_available(p_name text)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select p_name ~ '^[A-Za-z0-9_]{3,20}$'
     and not exists (select 1 from profiles where lower(username) = lower(p_name) and id <> coalesce(auth.uid(), gen_random_uuid()));
$$;

-- Sets (or changes) the signed-in account's username. Guests can't have one.
create or replace function set_username(p_name text)
returns text
language plpgsql security definer
set search_path = public
as $$
declare
  me uuid := require_user();
begin
  if is_guest() then
    raise exception 'guests can''t pick a username';
  end if;
  if p_name !~ '^[A-Za-z0-9_]{3,20}$' then
    raise exception 'username must be 3-20 letters, numbers or _';
  end if;
  insert into profiles (id, username) values (me, p_name)
  on conflict (id) do update set username = excluded.username;
  return p_name;
exception when unique_violation then
  raise exception 'username taken' using errcode = 'P0001', hint = 'taken';
end;
$$;

create or replace function get_profile()
returns jsonb
language sql stable security definer
set search_path = public
as $$
  select jsonb_build_object('username', (select username from profiles where id = auth.uid()),
                            'guest', is_guest());
$$;

-- Guest -> existing account: the guest gets a token before logging in...
create or replace function start_transfer()
returns uuid
language plpgsql security definer
set search_path = public
as $$
declare
  me uuid := require_user();
  t uuid;
begin
  if not is_guest() then
    raise exception 'only guests can transfer history';
  end if;
  delete from transfer_tokens where user_id = me or created_at < now() - interval '1 day';
  insert into transfer_tokens (user_id) values (me) returning token into t;
  return t;
end;
$$;

-- ...and the account claims it after. The account keeps its own picks on any
-- day both played; every other guest day moves over. Returns days moved.
create or replace function claim_transfer(p_token uuid)
returns int
language plpgsql security definer
set search_path = public
as $$
declare
  me uuid := require_user();
  old uuid;
  moved int;
begin
  if is_guest() then
    raise exception 'log in before claiming guest history';
  end if;
  delete from transfer_tokens where token = p_token and created_at > now() - interval '1 day'
  returning user_id into old;
  if old is null or old = me then
    return 0;
  end if;
  delete from picks g where g.user_id = old and g.pack_date in
    (select pack_date from picks where user_id = me union select pack_date from submissions where user_id = me);
  delete from submissions g where g.user_id = old and g.pack_date in
    (select pack_date from picks where user_id = me union select pack_date from submissions where user_id = me);
  select count(distinct d) into moved from
    (select pack_date d from picks where user_id = old union select pack_date from submissions where user_id = old) x;
  update picks set user_id = me where user_id = old;
  update submissions set user_id = me where user_id = old;
  return moved;
end;
$$;

-- ---------------------------------------------------------------- stats

-- Everything the Stats page shows. p_today is the player's local date (for streaks).
create or replace function get_my_stats(p_today date)
returns jsonb
language sql stable security definer
set search_path = public
as $$
  with me as (select require_user() as id),
  s as (select * from pick_scores((select id from me))),
  days as (   -- a pack counts as played if it was finished or has any pick
    select pack_date from submissions where user_id = (select id from me)
    union select pack_date from s
  ),
  packs as (
    select d.pack_date, coalesce(sum(s.pts), 0)::int as score, not pack_date_open(d.pack_date) as final,
           jsonb_agg(jsonb_build_object('i', s.prompt_idx, 'tier', s.tier, 'pts', s.pts, 'player_id', s.player_id, 'pct', s.pct)
                     order by s.prompt_idx) filter (where s.prompt_idx is not null) as cards
    from days d left join s on s.pack_date = d.pack_date
    group by d.pack_date
  ),
  runs as (   -- consecutive days: same (date - row number) means same streak
    select min(pack_date) as first_day, max(pack_date) as last_day, count(*)::int as len
    from (select pack_date, pack_date - (row_number() over (order by pack_date))::int as grp from packs) x
    group by grp
  ),
  rarest as (
    select s.player_id, s.pct, s.tier, s.pack_date, dp.prompts -> s.prompt_idx ->> 'text' as prompt
    from s join daily_packs dp on dp.pack_date = s.pack_date
    order by s.pct asc, s.pack_date asc limit 1
  )
  select jsonb_build_object(
    'username',       (select username from profiles where id = (select id from me)),
    'packs',          (select count(*) from packs),
    'current_streak', coalesce((select len from runs where last_day >= p_today - 1 order by last_day desc limit 1), 0),
    'best_streak',    coalesce((select max(len) from runs), 0),
    'avg_score',      (select round(avg(score)) from packs),
    'best_score',     (select max(score) from packs),
    'best_ft',        (select greatest(2, max(score)) from packs),
    'total_ft',       coalesce((select sum(score) from packs), 0),
    'tiers',          (select jsonb_object_agg(t, coalesce(n, 0)) from unnest(array['copper','silver','gold','emerald','refr','one']) t
                       left join (select tier, count(*) n from s group by tier) c on c.tier = t),
    'rarest',         (select to_jsonb(rarest) from rarest),
    'history',        coalesce((select jsonb_agg(jsonb_build_object('date', pack_date, 'score', score, 'final', final,
                                                                    'cards', coalesce(cards, '[]'::jsonb)) order by pack_date desc)
                                from (select * from packs order by pack_date desc limit 100) h), '[]'::jsonb)
  );
$$;

-- ---------------------------------------------------------------- rarity model

-- Latest model, plus the first pack date (where learning starts when there's no model yet).
create or replace function get_fame_model()
returns jsonb
language sql stable security definer
set search_path = public
as $$
  select jsonb_build_object(
    'model_date', (select model_date from fame_models order by model_date desc limit 1),
    'model',      (select model from fame_models order by model_date desc limit 1),
    'first_pack', (select min(pack_date) from daily_packs));
$$;

-- A settled day's prompts and pick counts, to learn from. Nothing for days still open.
create or replace function get_closed_day(p_date date)
returns jsonb
language sql stable security definer
set search_path = public
as $$
  select case when pack_date_open(p_date) then null else jsonb_build_object(
    'prompts', (select prompts from daily_packs where pack_date = p_date),
    'picks', coalesce((select jsonb_agg(jsonb_build_object('prompt_idx', prompt_idx, 'player_id', player_id, 'picks', n))
                       from (select prompt_idx, player_id, count(*)::int n from picks
                             where pack_date = p_date group by prompt_idx, player_id) x), '[]'::jsonb)) end;
$$;

-- Store the model learned from a settled day. First valid proposal wins; returns the stored one.
-- Valid = same shape and bounds as rarity.js, the day right after the latest stored model,
-- and only a small step from it (weights +-0.06, pull +-16%, other settings unchanged).
create or replace function propose_fame_model(p_date date, p_model jsonb)
returns jsonb
language plpgsql security definer
set search_path = public
as $$
declare
  prev jsonb;
  prev_date date;
  existing jsonb;
  f text;
begin
  perform require_user();
  select model into existing from fame_models where model_date = p_date;
  if existing is not null then
    return existing;
  end if;
  if pack_date_open(p_date) then
    raise exception 'day % has not settled', p_date;
  end if;
  select model, model_date into prev, prev_date from fame_models order by model_date desc limit 1;
  if prev_date is not null and p_date <> prev_date + 1 then
    raise exception 'next model must be for %', prev_date + 1;
  end if;
  if prev is null then   -- rarity.js DEFAULT_MODEL
    prev := '{"v":1,"w":{"views":0.6,"playoffs":0.12,"seasons":0.08,"awards":0.12,"recency":0.08},
              "center":0.9,"steep":20,"spread":0.1,"pull":12}';
  end if;
  foreach f in array array['views','playoffs','seasons','awards','recency'] loop
    if jsonb_typeof(p_model -> 'w' -> f) <> 'number'
       or (p_model -> 'w' ->> f)::numeric not between 0.01 and 1
       or abs((p_model -> 'w' ->> f)::numeric - (prev -> 'w' ->> f)::numeric) > 0.06 then
      raise exception 'bad weight %', f;
    end if;
  end loop;
  if jsonb_typeof(p_model -> 'pull') <> 'number' or (p_model ->> 'pull')::numeric not between 1 and 20
     or abs((p_model ->> 'pull')::numeric / (prev ->> 'pull')::numeric - 1) > 0.16
     or p_model -> 'center' <> prev -> 'center' or p_model -> 'steep' <> prev -> 'steep'
     or p_model -> 'spread' <> prev -> 'spread' then
    raise exception 'bad model settings';
  end if;
  insert into fame_models (model_date, model) values (p_date, p_model) on conflict (model_date) do nothing;
  return (select model from fame_models where model_date = p_date);
end;
$$;

-- ---------------------------------------------------------------- permissions

-- Supabase grants new functions to anon by default, so revoke everything, then
-- grant only what the game calls. Guests and accounts are both "authenticated".
do $$
declare f text;
begin
  foreach f in array array[
    'pack_date_open(date)', 'require_user()', 'is_guest()', 'pick_scores(uuid)',
    'get_or_create_pack(date, jsonb)', 'submit_pick(date, smallint, bigint, real)',
    'get_pick_rates(date)', 'submit_score(date, smallint)', 'get_my_day(date)',
    'username_available(text)', 'set_username(text)', 'get_profile()',
    'start_transfer()', 'claim_transfer(uuid)', 'get_my_stats(date)',
    'get_fame_model()', 'get_closed_day(date)', 'propose_fame_model(date, jsonb)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
  end loop;
end $$;
grant execute on function get_or_create_pack(date, jsonb)          to anon, authenticated;
grant execute on function get_pick_rates(date)                     to anon, authenticated;
grant execute on function username_available(text)                to anon, authenticated;
grant execute on function submit_pick(date, smallint, bigint, real) to authenticated;
grant execute on function submit_score(date, smallint)             to authenticated;
grant execute on function get_my_day(date)                         to authenticated;
grant execute on function set_username(text)                       to authenticated;
grant execute on function get_profile()                            to authenticated;
grant execute on function start_transfer()                         to authenticated;
grant execute on function claim_transfer(uuid)                     to authenticated;
grant execute on function get_my_stats(date)                       to authenticated;
grant execute on function get_fame_model()                         to anon, authenticated;
grant execute on function get_closed_day(date)                     to anon, authenticated;
grant execute on function propose_fame_model(date, jsonb)          to authenticated;
