-- ============================================
-- LEGACY INDEXES (pre-Supabase migration)
-- ============================================

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

create index flights_trip_id_idx on public.flights(trip_id);
create index hotels_trip_id_idx on public.hotels(trip_id);
