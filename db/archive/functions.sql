-- ============================================
-- LEGACY FUNCTIONS (pre-Supabase migration)
-- ============================================

-- Recalculates and updates daily_total_cost for a single itinerary day
-- by summing estimated_cost of all activities linked to that day
CREATE OR REPLACE FUNCTION public.calculate_day_total_cost(p_day_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE public.itinerary_days
  SET daily_total_cost = (
    SELECT COALESCE(SUM(estimated_cost), 0)
    FROM public.activities
    WHERE day_id = p_day_id
  )
  WHERE day_id = p_day_id;
END;
$$;

-- Recalculates and updates total_estimated_cost for a trip
-- by summing daily_total_cost of all itinerary days for that trip
CREATE OR REPLACE FUNCTION public.calculate_trip_total_cost(p_trip_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE public.trips
  SET total_estimated_cost = (
    SELECT COALESCE(SUM(daily_total_cost), 0)
    FROM public.itinerary_days
    WHERE trip_id = p_trip_id
  )
  WHERE trip_id = p_trip_id;
END;
$$;

-- Returns the mode (most frequent) number_of_people from a user's past trips
-- Used to pre-fill party size when creating a new trip
CREATE OR REPLACE FUNCTION public.get_user_mode_people(p_user_id text)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
  SELECT number_of_people
  FROM public.trips
  WHERE user_id = p_user_id
  GROUP BY number_of_people
  ORDER BY COUNT(*) DESC
  LIMIT 1;
$$;

-- Trigger function: called by trigger_update_day_total_on_activity
-- Delegates to calculate_day_total_cost after any activity change
CREATE OR REPLACE FUNCTION public.update_day_total()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_day_id uuid;
BEGIN
  v_day_id := COALESCE(NEW.day_id, OLD.day_id);
  PERFORM public.calculate_day_total_cost(v_day_id);
  RETURN NULL;
END;
$$;

-- Trigger function: called by trigger_update_trip_total_on_day
-- Delegates to calculate_trip_total_cost after any itinerary day change
CREATE OR REPLACE FUNCTION public.update_trip_total()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_trip_id uuid;
BEGIN
  v_trip_id := COALESCE(NEW.trip_id, OLD.trip_id);
  PERFORM public.calculate_trip_total_cost(v_trip_id);
  RETURN NULL;
END;
$$;

-- Trigger function: sets updated_at to now() before any update
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- Trigger function: runs before INSERT on trips
-- Fills user_id from auth.uid() if missing, aborts if still null,
-- and ensures a matching profiles row exists (ON CONFLICT DO NOTHING)
-- Note: auth.uid() is Supabase-specific and was never compatible with this
-- schema's Cognito user_id (text). Written mid-migration and never finalized.
CREATE OR REPLACE FUNCTION public.ensure_user_on_trip()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  IF NEW.user_id IS NULL THEN
    NEW.user_id := auth.uid()::text;
  END IF;

  IF NEW.user_id IS NULL THEN
    RAISE EXCEPTION 'user_id is required to create a trip';
  END IF;

  INSERT INTO public.profiles (id)
  VALUES (NEW.user_id::uuid)
  ON CONFLICT DO NOTHING;

  RETURN NEW;
END;
$$;

-- Trigger function: runs before INSERT on trips
-- Pre-fills number_of_people using the user's historical mode via get_user_mode_people
-- Falls back to 1 if no history exists
CREATE OR REPLACE FUNCTION public.set_default_people_count()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_mode integer;
BEGIN
  IF NEW.number_of_people IS NULL OR NEW.number_of_people <= 0 THEN
    v_mode := public.get_user_mode_people(NEW.user_id);
    NEW.number_of_people := COALESCE(v_mode, 1);
  END IF;
  RETURN NEW;
END;
$$;
