-- ============================================
-- LEGACY SCHEMA (pre-Supabase migration)
-- Auth: AWS Cognito (user_id is text/sub, not uuid)
-- Structure: normalized — itinerary_days + places + activities
-- No JSON columns; cost rollups handled by triggers
-- ============================================

-- ============================================
-- DROP TABLES
-- ============================================
drop table if exists public.activities cascade;
drop table if exists public.places cascade;
drop table if exists public.itinerary_days cascade;
drop table if exists public.trips cascade;
drop table if exists public.users cascade;
drop table if exists public.hotels cascade;
drop table if exists public.flights cascade;

-- ============================================
-- EXTENSIONS
-- ============================================
create extension if not exists pgcrypto;

-- ============================================
-- USERS (Cognito sub as text PK)
-- ============================================
create table public.users (
  id           text primary key,     -- Cognito sub
  display_name text,
  created_at   timestamptz not null default now()
);

-- ============================================
-- TRIPS
-- ============================================
create table public.trips (
  trip_id              uuid primary key default gen_random_uuid(),
  user_id              text not null,
  name                 text not null,
  start_date           date not null,
  end_date             date not null,
  number_of_people     integer not null default 1 check (number_of_people > 0),
  total_estimated_cost numeric(10,2) default 0,
  trip_status          text not null default 'planning'
                       check (trip_status in ('planning', 'confirmed', 'completed', 'cancelled')),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  constraint fk_trips_user foreign key (user_id) references public.users(id) on delete cascade
);

-- ============================================
-- ITINERARY DAYS
-- ============================================
create table public.itinerary_days (
  day_id           uuid primary key default gen_random_uuid(),
  trip_id          uuid not null references public.trips(trip_id) on delete cascade,
  date             date not null,
  daily_total_cost decimal(10, 2) default 0,
  created_at       timestamptz not null default now()
);

-- ============================================
-- PLACES
-- ============================================
create table public.places (
  place_id     uuid primary key default gen_random_uuid(),
  name         text not null,
  address      text,
  city         text not null,
  country      text not null,
  place_type   text not null check (place_type in ('food', 'scenery', 'amusement_park', 'zoo', 'paid_activity', 'hotel', 'lodging', 'other')),
  latitude     decimal(10, 8),
  longitude    decimal(11, 8),
  api_source   text,
  api_place_id text,
  description  text,
  price_level  integer check (price_level between 1 and 4),
  average_cost decimal(10, 2),
  rating       decimal(2, 1) check (rating between 0 and 5),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- ============================================
-- ACTIVITIES
-- ============================================
create table public.activities (
  activity_id    uuid primary key default gen_random_uuid(),
  day_id         uuid not null references public.itinerary_days(day_id) on delete cascade,
  place_id       uuid not null references public.places(place_id) on delete cascade,
  start_time     time,
  end_time       time,
  estimated_cost decimal(10, 2) default 0,
  notes          text,
  display_order  integer,
  is_confirmed   boolean default false,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

-- ============================================
-- FLIGHTS
-- ============================================
create table public.flights (
  id            uuid primary key default gen_random_uuid(),
  trip_id       uuid not null references public.trips(trip_id) on delete cascade,
  ariline       text not null,
  flight_number text not null,
  origin_iata   char(3) not null,
  dest_iata     char(3) not null,
  price         numeric(10,2),
  currency      char(3),
  cabin_class   text,
  booking_ref   text,
  offer_id      text,

  constraint origin_iata_len check (char_length(origin_iata) = 3),
  constraint dest_iata_len   check (char_length(dest_iata) = 3),
  constraint currency_len    check (currency is null or char_length(currency) = 3),
  constraint price_nonneg    check (price is null or price >= 0)
);

-- ============================================
-- HOTELS
-- ============================================
create table public.hotels (
  id               uuid primary key default gen_random_uuid(),
  trip_id          uuid not null references public.trips(trip_id) on delete cascade,
  hotel_name       text not null,
  hotel_address    text not null,
  hotel_lat        numeric(9,6),
  hotel_lng        numeric(9,6),
  check_in         date,
  check_out        date,
  price_per_night  numeric(10,2),
  total_price      numeric(10,2),
  currency         char(3),
  rating           int,
  star_rating      int,
  image_url        text,
  booking_ref      text not null,
  offer_id         text not null,

  constraint price_per_night_nonneg check (price_per_night is null or price_per_night >= 0),
  constraint currency_len           check (currency is null or char_length(currency) = 3),
  constraint total_price_nonneg     check (total_price is null or total_price >= 0),
  constraint check_dates_order      check (check_out is null or check_in is null or check_out >= check_in)
);
