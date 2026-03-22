"""CRUD endpoints for activities (TRA-220)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/trips/{trip_id}/activities", tags=["activities"])
log = logging.getLogger(__name__)


# ---------- Schemas ----------

class ActivityCreate(BaseModel):
    activity_name: str
    starting_date: str
    ending_date: str
    starting_time: str
    ending_time: str
    activity_type: str = "other"
    latitude: float = 0
    longitude: float = 0
    estimated_cost: float = 0
    currency: str | None = None
    notes: str | None = None
    sort_order: int = 0
    activity_data: dict = {}


class ActivityUpdate(BaseModel):
    activity_name: str | None = None
    starting_date: str | None = None
    ending_date: str | None = None
    starting_time: str | None = None
    ending_time: str | None = None
    activity_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    estimated_cost: float | None = None
    currency: str | None = None
    notes: str | None = None
    sort_order: int | None = None
    activity_data: dict | None = None


class ActivityBulkItem(BaseModel):
    id: str | None = None
    activity_name: str
    starting_date: str
    ending_date: str
    starting_time: str
    ending_time: str
    activity_type: str = "other"
    latitude: float = 0
    longitude: float = 0
    estimated_cost: float = 0
    currency: str | None = None
    notes: str | None = None
    sort_order: int = 0
    activity_data: dict = {}


# ---------- Endpoints ----------

@router.get("")
async def list_activities(
    trip_id: str,
    type: str | None = Query(None, description="Filter by activity_type"),
    user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    q = sb.table("activity").select("*").eq("trip_id", trip_id)
    if type:
        q = q.eq("activity_type", type)
    res = q.order("starting_date").order("starting_time").execute()
    return res.data


@router.post("", status_code=201)
async def create_activity(trip_id: str, body: ActivityCreate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    row = body.model_dump()
    row["trip_id"] = trip_id
    row["user_id"] = user["id"]
    res = sb.table("activity").insert(row).execute()
    return res.data[0]


@router.patch("/{activity_id}")
async def update_activity(
    trip_id: str, activity_id: str, body: ActivityUpdate, user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    res = (
        sb.table("activity")
        .update(updates)
        .eq("id", activity_id)
        .eq("trip_id", trip_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Activity not found")
    return res.data[0]


@router.delete("/{activity_id}", status_code=204)
async def delete_activity(trip_id: str, activity_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    res = sb.table("activity").delete().eq("id", activity_id).eq("trip_id", trip_id).execute()
    if not res.data:
        raise HTTPException(404, "Activity not found")


@router.put("/bulk")
async def bulk_upsert_activities(
    trip_id: str, items: list[ActivityBulkItem], user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    rows = []
    for item in items:
        row = item.model_dump()
        row["trip_id"] = trip_id
        row["user_id"] = user["id"]
        if not row.get("id"):
            row.pop("id", None)
        rows.append(row)
    res = sb.table("activity").upsert(rows).execute()
    return res.data


# ---------- Helpers ----------

def _assert_trip_access(sb, trip_id: str, user_id: str):
    trip = sb.table("trips").select("user_id").eq("id", trip_id).maybe_single().execute()
    if not trip.data:
        raise HTTPException(404, "Trip not found")
    if trip.data["user_id"] == user_id:
        return
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
