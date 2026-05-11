-- ============================================
-- ENABLE ROW LEVEL SECURITY
-- ============================================
alter table public.profiles enable row level security;
alter table public.trips enable row level security;
alter table public.activity enable row level security;
alter table public.itinerary_edits enable row level security;
alter table public.trip_change_log enable row level security;
alter table public.favorite_places enable row level security;
alter table public.search_session enable row level security;
alter table public.trip_collaborators enable row level security;
alter table public.trip_feedback enable row level security;
alter table public.user_travel_profile enable row level security;

-- ============================================
-- TRIP COLLABORATORS POLICIES
-- Written to unblock Simon Santos's collaborator page — he needed this to get his flow running
-- ============================================

-- Select trip collaborators
CREATE POLICY "Collaborators can view trip collaborators"
ON trip_collaborators
FOR SELECT
TO authenticated
USING (
  user_id = auth.uid()
  OR invited_by = auth.uid()
  OR trip_id IN (
    SELECT trip_id FROM trip_collaborators WHERE user_id = auth.uid()
  )
);

-- INSERT or invite trip collaborators
CREATE POLICY "Trip owners can invite collaborators"
ON trip_collaborators
FOR INSERT
TO authenticated
WITH CHECK (
  invited_by = auth.uid()
  AND trip_id IN (
    SELECT id FROM trips WHERE user_id = auth.uid()
  )
);

-- UPDATE roles of others
CREATE POLICY "Trip owners can update collaborator roles"
ON trip_collaborators
FOR UPDATE
TO authenticated
USING (
  trip_id IN (
    SELECT id FROM trips WHERE user_id = auth.uid()
  )
);

-- DELETE from trip collaborators
CREATE POLICY "Trip owners or self can remove collaborators"
ON trip_collaborators
FOR DELETE
TO authenticated
USING (
  user_id = auth.uid()
  OR trip_id IN (
    SELECT id FROM trips WHERE user_id = auth.uid()
  )
);

-- ============================================
-- TRIP COLLABORATORS - USER SELF-ACCESS POLICIES
-- ============================================

-- Users will be able to see their own collaborations
CREATE POLICY "Users can select their own collaborations"
ON public.trip_collaborators
FOR SELECT
TO authenticated
USING (user_id = auth.uid());

-- Users will be able to insert their own collaboration
CREATE POLICY "Users can insert own collaborations"
ON public.trip_collaborators
FOR INSERT
TO authenticated
WITH CHECK (user_id = auth.uid());

-- Own users can update invite status (written to unblock Simon Santos's collaborator page — he needed this to get his flow running)
CREATE POLICY "Users can update own invite status"
ON public.trip_collaborators
FOR UPDATE
TO authenticated
USING (user_id = auth.uid())
WITH CHECK (user_id = auth.uid());

-- ============================================
-- SECURITY DEFINER FUNCTIONS
-- ============================================

CREATE OR REPLACE FUNCTION get_collaborator_trips(p_user_id UUID)
RETURNS TABLE (
  id UUID,
  user_id UUID,
  title TEXT,
  start_date DATE,
  end_date DATE,
  status TEXT,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  destination TEXT,
  share_link_token TEXT,
  visibility TEXT,
  link_permission TEXT,
  budget NUMERIC,
  currency TEXT,
  travelers INT4,
  theme TEXT,
  custom_theme_color TEXT,
  tab_color_overrides JSONB,
  itinerary_color_overrides JSONB,
  hidden_tabs JSONB,
  forked_from_trip_id UUID,
  fork_count INT4,
  is_generated BOOL,
  trip_context JSONB,
  cover_image_url TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  RETURN QUERY
  SELECT t.*
  FROM public.trips t
  INNER JOIN public.trip_collaborators tc ON tc.trip_id = t.id
  WHERE tc.user_id = p_user_id
    AND tc.invite_status = 'accepted';
END;
$$;

-- Grant permissions
REVOKE EXECUTE ON FUNCTION get_collaborator_trips(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION get_collaborator_trips(UUID) TO authenticated;

-- ============================================
-- FIX RLS POLICIES FOR TRIP_COLLABORATORS
-- Resolves infinite recursion; required for the "Join to edit" flow
-- Written to unblock Simon Santos's collaborator page — he needed this to get his flow running
-- ============================================

-- Step 1: Create SECURITY DEFINER functions to bypass RLS

-- Function to check if user has existing collaboration
CREATE OR REPLACE FUNCTION public.get_user_collaboration(p_trip_id uuid, p_user_id uuid)
  RETURNS TABLE (
    id uuid,
    trip_id uuid,
    user_id uuid,
    role_type text,
    invite_status text,
    invited_by uuid,
    accepted_at timestamptz,
    created_at timestamptz
  )
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT
    tc.id,
    tc.trip_id,
    tc.user_id,
    tc.role_type,
    tc.invite_status,
    tc.invited_by,
    tc.accepted_at,
    tc.created_at
  FROM trip_collaborators tc
  WHERE tc.trip_id = p_trip_id
    AND (tc.user_id = p_user_id OR tc.user_id IS NULL)
    AND tc.invite_status IN ('pending', 'accepted', 'cancelled')
$$;

-- Function to join a trip via link (bypasses RLS)
CREATE OR REPLACE FUNCTION public.join_trip_via_link(
  p_trip_id uuid,
  p_user_id uuid,
  p_role_type text
)
  RETURNS TABLE (
    id uuid,
    trip_id uuid,
    user_id uuid,
    role_type text,
    invite_status text,
    invited_by uuid,
    accepted_at timestamptz,
    created_at timestamptz
  )
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  INSERT INTO trip_collaborators (
    trip_id,
    user_id,
    role_type,
    invite_status,
    invited_by,
    accepted_at
  )
  VALUES (
    p_trip_id,
    p_user_id,
    p_role_type,
    'accepted',
    p_user_id,
    now()
  )
  RETURNING
    id,
    trip_id,
    user_id,
    role_type as role_type,
    invite_status,
    invited_by,
    accepted_at,
    created_at;
$$;

-- Step 2: Drop ALL existing policies on trip_collaborators to start fresh
DROP POLICY IF EXISTS "users_can_read_own_collaborations" ON trip_collaborators;
DROP POLICY IF EXISTS "users_can_insert_own_collaborations" ON trip_collaborators;
DROP POLICY IF EXISTS "owners_can_read_trip_collaborations" ON trip_collaborators;
DROP POLICY IF EXISTS "Trip owners can view all collaborators" ON trip_collaborators;
DROP POLICY IF EXISTS "Users can update own invite status" ON trip_collaborators;
DROP POLICY IF EXISTS "users_can_update_own_collaborations" ON trip_collaborators;
DROP POLICY IF EXISTS "users_can_delete_own_collaborations" ON trip_collaborators;

-- Step 3: Create simple, non-circular policies

-- Users can read their own collaboration records
CREATE POLICY "users_can_read_own_collaborations"
  ON trip_collaborators FOR SELECT
  TO authenticated
  USING (user_id = auth.uid() OR user_id IS NULL);

-- Users can update their own collaboration records
CREATE POLICY "users_can_update_own_collaborations"
  ON trip_collaborators FOR UPDATE
  TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- Users can delete their own collaboration records
CREATE POLICY "users_can_delete_own_collaborations"
  ON trip_collaborators FOR DELETE
  TO authenticated
  USING (user_id = auth.uid());

-- Step 4: Grant execute on helper functions to authenticated users
GRANT EXECUTE ON FUNCTION public.get_user_collaboration(uuid, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.join_trip_via_link(uuid, uuid, text) TO authenticated;

-- ============================================
-- HELPER FUNCTIONS & ITINERARY EDITS POLICIES
-- Written to unblock Simon Santos's collaborator page — he needed this to get his flow running
-- ============================================

-- Helper to check if the current user is the trip owner
CREATE OR REPLACE FUNCTION public.is_trip_owner(p_trip_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM trips
    WHERE id = p_trip_id
      AND user_id = auth.uid()
  )
$$;

DROP POLICY IF EXISTS "Collaborators can read itinerary edits" ON itinerary_edits;

CREATE POLICY "Collaborators can read itinerary edits"
  ON itinerary_edits FOR SELECT
  TO authenticated
  USING (
    public.is_trip_owner(trip_id)
    OR
    public.is_trip_collaborator(trip_id)
  );
