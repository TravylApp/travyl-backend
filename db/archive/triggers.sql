-- ============================================
-- LEGACY TRIGGERS (pre-Supabase migration)
-- All trigger functions are defined in functions.sql
-- Run order: schema.sql → functions.sql → indexes.sql → triggers.sql
-- ============================================

-- ============================================
-- DROP TRIGGERS
-- ============================================
drop trigger if exists trigger_update_day_total_on_activity on public.activities;
drop trigger if exists trigger_update_trip_total_on_day on public.itinerary_days;
drop trigger if exists trigger_set_updated_at_trips on public.trips;
drop trigger if exists trigger_set_updated_at_activities on public.activities;
drop trigger if exists trigger_ensure_user_on_trip on public.trips;
drop trigger if exists trigger_set_default_people_count on public.trips;
drop trigger if exists set_trips_updated_at on public.trips;
drop trigger if exists set_places_updated_at on public.places;
drop trigger if exists set_activities_updated_at on public.activities;

-- ============================================
-- CREATE TRIGGERS
-- ============================================

-- Recalculates itinerary_days.daily_total_cost after any activity change
create trigger trigger_update_day_total_on_activity
after insert or update or delete on public.activities
for each row
execute function public.update_day_total();

-- Recalculates trips.total_estimated_cost after any itinerary day change
create trigger trigger_update_trip_total_on_day
after insert or update or delete on public.itinerary_days
for each row
execute function public.update_trip_total();

-- Keeps trips.updated_at current on any update
create trigger trigger_set_updated_at_trips
before update on public.trips
for each row
execute function public.set_updated_at();

-- Keeps activities.updated_at current on any update
create trigger trigger_set_updated_at_activities
before update on public.activities
for each row
execute function public.set_updated_at();

-- Keeps places.updated_at current on any update
create trigger set_places_updated_at
before update on public.places
for each row
execute function public.set_updated_at();

-- Ensures trip is owned by a valid user; inserts profiles row if missing
create trigger trigger_ensure_user_on_trip
before insert on public.trips
for each row
execute function public.ensure_user_on_trip();

-- Pre-fills number_of_people using the user's historical mode
create trigger trigger_set_default_people_count
before insert on public.trips
for each row
execute function public.set_default_people_count();
