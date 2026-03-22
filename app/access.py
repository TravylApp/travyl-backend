"""Shared trip access-control helpers used by CRUD routers."""

from fastapi import HTTPException

from app.services.supabase import get_supabase


def assert_trip_owner(trip_id: str, user_id: str, sb=None):
    """Raise 404/403 unless *user_id* owns *trip_id*. Returns the trip row."""
    sb = sb or get_supabase()
    res = sb.table("trips").select("user_id").eq("id", trip_id).maybe_single().execute()
    if not res.data:
        raise HTTPException(404, "Trip not found")
    if res.data["user_id"] != user_id:
        raise HTTPException(403, "Only the trip owner can do this")
    return res.data


def assert_trip_access(trip_id: str, user_id: str, sb=None):
    """Raise 404/403 unless *user_id* is owner or accepted collaborator."""
    sb = sb or get_supabase()
    trip = sb.table("trips").select("user_id").eq("id", trip_id).maybe_single().execute()
    if not trip.data:
        raise HTTPException(404, "Trip not found")
    if trip.data["user_id"] == user_id:
        return trip.data
    collab = (
        sb.table("trip_collaborators")
        .select("id")
        .eq("trip_id", trip_id)
        .eq("user_id", user_id)
        .eq("invite_status", "accepted")
        .maybe_single()
        .execute()
    )
    if not collab.data:
        raise HTTPException(403, "Not authorised")
    return trip.data
