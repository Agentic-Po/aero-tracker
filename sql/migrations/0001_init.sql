-- aero-tracker initial schema
-- Run this in the Supabase SQL editor (Project -> SQL -> New query)

-- ============================================================
-- 8-hour multiplier snapshots
-- ============================================================
create table if not exists snapshots_8h (
  id                  bigserial primary key,
  captured_at         timestamptz not null default now(),
  epoch_number        int,
  new_emissions       numeric(30, 6) not null,
  aero_price_usd      numeric(20, 8) not null,
  emissions_value     numeric(30, 6) not null,
  total_rewards       numeric(30, 6) not null,
  total_fees          numeric(30, 6),
  total_incentives    numeric(30, 6),
  total_voting_power  numeric(30, 6),
  multiplier          numeric(20, 6) not null,
  sim_plus_1k         numeric(20, 6),
  sim_plus_25k        numeric(20, 6),
  sim_plus_50k        numeric(20, 6),
  sim_plus_100k       numeric(20, 6),
  source_url          text not null default 'https://aerodrome.finance/vote',
  raw                 jsonb
);

create index if not exists snapshots_8h_captured_at_idx
  on snapshots_8h (captured_at desc);

-- ============================================================
-- Per-pool rows attached to each 8h snapshot
-- ============================================================
create table if not exists snapshots_pools (
  id              bigserial primary key,
  snapshot_id     bigint not null references snapshots_8h(id) on delete cascade,
  pool_address    text,
  pair            text,
  votes           numeric(30, 6),
  fees_usd        numeric(20, 6),
  incentives_usd  numeric(20, 6),
  rewards_usd     numeric(20, 6)
);

create index if not exists snapshots_pools_snapshot_id_idx
  on snapshots_pools (snapshot_id);

-- ============================================================
-- Weekly epoch winner (captured Wed 23:00 UTC, 1h before flip)
-- ============================================================
create table if not exists epoch_winners (
  id              bigserial primary key,
  epoch_number    int,
  captured_at     timestamptz not null default now(),
  pool_address    text,
  pair            text not null,
  votes           numeric(30, 6) not null,
  total_votes     numeric(30, 6) not null,
  pct_of_total    numeric(8, 4) not null,
  is_ignition     boolean default false,
  raw             jsonb
);

create unique index if not exists epoch_winners_epoch_idx
  on epoch_winners (epoch_number)
  where epoch_number is not null;

-- ============================================================
-- Public read-only access via PostgREST anon role
-- (dashboard reads with anon key; service_role writes from GH Actions)
-- ============================================================
alter table snapshots_8h enable row level security;
alter table snapshots_pools enable row level security;
alter table epoch_winners enable row level security;

create policy "public read snapshots_8h"
  on snapshots_8h for select using (true);

create policy "public read snapshots_pools"
  on snapshots_pools for select using (true);

create policy "public read epoch_winners"
  on epoch_winners for select using (true);

-- Convenience view: latest snapshot
-- security_invoker = on so the view respects the caller's RLS, not the creator's
create or replace view latest_snapshot_8h
  with (security_invoker = on)
  as select * from snapshots_8h order by captured_at desc limit 1;
