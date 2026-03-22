"""Shared trip access-control helpers used by CRUD routers."""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from app.services.supabase import get_supabase

ROLE_TYPE = Literal["viewer", "commenter", "editor"]
_ROLE_RANK = {"viewer": 0, "commenter": 1, "editor": 2}


def validate_uuid(value: str, name: str = "Resource"):
    try:
        UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(404, f"{name} not found")


def assert_trip_owner(trip_id: str, user_id: str, sb=None):
    sb = sb or get_supabase()
    res = sb.table("trips").select("user_id").eq("id", trip_id).maybe_single().execute()
    if not res or not res.data:
        raise HTTPException(404, "Trip not found")
    if res.data["user_id"] != user_id:
        raise HTTPException(403, "Only the trip owner can do this")
    return res.data


def assert_trip_access(
    trip_id: str, user_id: str, sb=None, require_role: ROLE_TYPE | None = None,
):
    sb = sb or get_supabase()
    trip = sb.table("trips").select("user_id").eq("id", trip_id).maybe_single().execute()
    if not trip or not trip.data:
        raise HTTPException(404, "Trip not found")
    if trip.data["user_id"] == user_id:
        return trip.data
    collab = (
        sb.table("trip_collaborators")
        .select("id, role_type")
        .eq("trip_id", trip_id)
        .eq("user_id", user_id)
        .eq("invite_status", "accepted")
        .maybe_single()
        .execute()
    )
    if not collab or not collab.data:
        raise HTTPException(403, "Not authorised")
    if require_role and _ROLE_RANK.get(collab.data["role_type"], 0) < _ROLE_RANK[require_role]:
        raise HTTPException(403, f"{require_role.title()} access required")
    return trip.data
