create extension if not exists pgcrypto;

-- ============================================
-- DROP TABLES (order matters because of FKs)
-- ============================================
drop table if exists public.activities cascade;
drop table if exists public.activity cascade;
drop table if exists public.places cascade;
drop table if exists public.itinerary_days cascade;
drop table if exists public.flights cascade;
drop table if exists public.hotels cascade;
drop table if exists public.trips cascade;
drop table if exists public.profiles cascade;
drop table if exists public.itinerary_edits cascade;
drop table if exists public.trip_change_log cascade;
drop table if exists public.favorite_places cascade;
drop table if exists public.search_session cascade;
drop table if exists public.trip_collaborators cascade;
drop table if exists public.user_travel_profile;

-- ============================================
-- PROFILES (Supabase Auth)
-- ============================================
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  email text,
  avatar_url text,
  onboarding_completed bool not null default false,
  preferences jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ============================================
-- TRIPS
-- ============================================
create table public.trips (
  id uuid primary key default gen_random_uuid(),

  user_id uuid not null references auth.users(id) on delete cascade,
  trip_name text not null,
  starting_date date not null,
  ending_date date not null,

  trip_status text not null default 'planning'
    check (trip_status in ('planning', 'confirmed', 'completed', 'cancelled')),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint trips_dates_order check (ending_date >= starting_date)
);

-- ============================================
-- ACTIVITIES - start_date is a key word
-- ============================================
create table public.activity (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references public.trips(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  activity_name text not null,
  activity_type text not null default 'other'
    check (activity_type in ('hotel', 'airport', 'food', 'nature', 'amusement park', 'other')),
  starting_date date not null,
  ending_date date not null,
  starting_time time not null,
  ending_time time not null,
  estimated_cost numeric(10,2) not null default 0 check (estimated_cost >= 0),
  latitude numeric not null,
  longitude numeric not null,
  currency text,
  notes text,
  sort_order integer not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  activity_data jsonb not null default '{}'::jsonb,

  constraint activity_dates_order check (ending_date >= starting_date),
  constraint activity_time_order check (ending_time >= starting_time)
);

-- ============================================
-- ITINERARY EDITS
-- ============================================
create table public.itinerary_edits (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references public.trips(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  activity_id uuid not null references public.activity(id),
  edit_type text not null default 'created'
    check (edit_type in ('created', 'removed', 'edit')),
  original_data jsonb not null default '{}'::jsonb,
  new_data jsonb not null default '{}'::jsonb,
  category_from text,
  category_to text,
  cost_delta numeric,
  created_at timestamptz not null default now()
);

-- ============================================
-- TRIP CHANGE LOG
-- ============================================
create table public.trip_change_log (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references public.trips(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,

  action_type text not null default 'created'
    check (action_type in ('created', 'removed', 'edit')),

  entity_type text not null,
  entity_id uuid default gen_random_uuid(),
  previous_data jsonb not null default '{}'::jsonb,
  new_data jsonb not null default '{}'::jsonb,
  summary text,
  created_at timestamptz not null default now()
);

-- ============================================
-- FAVORITE PLACES
-- ============================================
create table public.favorite_places (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  activity_type text not null default 'other'
    check (activity_type in ('hotel', 'airport', 'food', 'nature', 'amusement park', 'other')),
  activity_data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- ============================================
-- SEARCH SESSION
-- ============================================
create table public.search_session (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  session_type text not null,
  initial_params jsonb not null default '{}'::jsonb,
  final_params jsonb not null default '{}'::jsonb,
  refinement_count integer not null default 0 check(refinement_count >= 0),
  resulted_in_booking bool not null default false,
  created_at timestamptz not null default now()
);

-- ============================================
-- TRIP COLLABORATORS
-- ============================================
create table public.trip_collaborators (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references public.trips(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  invited_email text,
  invite_token text,
  role_type text not null default 'viewer'
    check(role_type in ('viewer', 'editor', 'owner')),
  invite_status text not null default 'pending'
    check(invite_status in ('pending', 'accepted', 'denied')),
  invited_by uuid not null references auth.users(id) on delete cascade,
  accepted_at timestamptz,
  created_at timestamptz not null default now()
);

-- ============================================
-- TRIP FEEDBACK
-- ============================================
create table public.trip_feedback (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  trip_id uuid not null references public.trips(id) on delete cascade,
  overall_rating int check(overall_rating between 0 and 5),
  agent_accuracy int,
  highlights text[] not null default '{}',
  lowlights text[] not null default '{}',
  notes text,
  created_at timestamptz not null default now()
);

-- ============================================
-- USER TRAVEL PROFILES : lead days is booking date minus departure date
-- ============================================
create table public.user_travel_profile (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  travel_style text,
  pace text,
  planning_style text,
  avg_budget_per_day numeric(10,2) not null default 0 check(avg_budget_per_day >= 0),
  avg_trip_duration int not null default 1 check(avg_trip_duration >= 1),
  preferred_cabin_class text not null default 'economy'
    check(preferred_cabin_class in ('economy', 'premium', 'business', 'first')),
  hotel_star_preference integer,
  booking_lead_days int,
  budget_split jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

-- ============================================
-- DROP INDEXES
-- ============================================
drop index if exists public.idx_trips_user_id;
drop index if exists public.idx_itinerary_days_trip_id;
drop index if exists public.idx_activities_day_id;
drop index if exists public.idx_activities_place_id;
drop index if exists public.idx_places_city_country;
drop index if exists public.idx_places_type;
drop index if exists public.idx_places_api;
drop index if exists public.idx_trips_user_status;
drop index if exists public.flights_trip_id_idx;
drop index if exists public.hotels_trip_id_idx;
drop index if exists public.trips_raw_trips_gin;

-- ============================================
-- CREATE INDEXES
-- ============================================
create index idx_trips_user_id on public.trips(user_id);
create index idx_trips_user_status on public.trips(user_id, trip_status);

create index idx_itinerary_days_trip_id on public.itinerary_days(trip_id);

create index idx_activities_day_id on public.activities(day_id);
create index idx_activities_place_id on public.activities(place_id);

create index idx_places_city_country on public.places(city, country);
create index idx_places_type on public.places(place_type);

create unique index idx_places_api
on public.places(api_source, api_place_id)
where api_source is not null and api_place_id is not null;

create index flights_trip_id_idx on public.flights (trip_id);
create index hotels_trip_id_idx on public.hotels (trip_id);

-- jsonb search index (useful for query raw_trips fields)
create index trips_raw_trips_gin on public.trips using gin (raw_trips);
