"""CRUD endpoints for activities (TRA-220)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.access import assert_trip_access, validate_uuid
from app.auth import get_current_user
from app.schemas import ACTIVITY_TYPE
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/trips/{trip_id}/activities", tags=["activities"])


class ActivityCreate(BaseModel):
    activity_name: str
    starting_date: str
    ending_date: str
    starting_time: str
    ending_time: str
    activity_type: ACTIVITY_TYPE = "other"
    latitude: float = 0
    longitude: float = 0
    estimated_cost: float = 0
    currency: str | None = None
    notes: str | None = None
    sort_order: int = 0
    activity_data: dict = Field(default_factory=dict)


class ActivityUpdate(BaseModel):
    activity_name: str | None = None
    starting_date: str | None = None
    ending_date: str | None = None
    starting_time: str | None = None
    ending_time: str | None = None
    activity_type: ACTIVITY_TYPE | None = None
    latitude: float | None = None
    longitude: float | None = None
    estimated_cost: float | None = None
    currency: str | None = None
    notes: str | None = None
    sort_order: int | None = None
    activity_data: dict | None = None


class ActivityBulkItem(ActivityCreate):
    id: str | None = None


@router.get("")
def list_activities(
    trip_id: str,
    type: ACTIVITY_TYPE | None = Query(None, description="Filter by activity_type"),
    user: dict = Depends(get_current_user),
):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb)
    q = sb.table("activity").select("*").eq("trip_id", trip_id)
    if type:
        q = q.eq("activity_type", type)
    res = q.order("starting_date").order("starting_time").execute()
    return res.data


@router.post("", status_code=201)
def create_activity(trip_id: str, body: ActivityCreate, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb, require_role="editor")
    row = body.model_dump()
    row["trip_id"] = trip_id
    row["user_id"] = user["id"]
    res = sb.table("activity").insert(row).execute()
    return res.data[0]


@router.patch("/{activity_id}")
def update_activity(
    trip_id: str, activity_id: str, body: ActivityUpdate, user: dict = Depends(get_current_user),
):
    validate_uuid(trip_id, "Trip")
    validate_uuid(activity_id, "Activity")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb, require_role="editor")
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
def delete_activity(trip_id: str, activity_id: str, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    validate_uuid(activity_id, "Activity")
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb, require_role="editor")
    res = sb.table("activity").delete().eq("id", activity_id).eq("trip_id", trip_id).execute()
    if not res.data:
        raise HTTPException(404, "Activity not found")


@router.put("/bulk")
def bulk_upsert_activities(
    trip_id: str, items: list[ActivityBulkItem], user: dict = Depends(get_current_user),
):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb, require_role="editor")

    # validate that any provided IDs belong to this trip
    update_ids = [item.id for item in items if item.id]
    if update_ids:
        existing = sb.table("activity").select("id").eq("trip_id", trip_id).in_("id", update_ids).execute()
        existing_ids = {row["id"] for row in existing.data}
        invalid = [uid for uid in update_ids if uid not in existing_ids]
        if invalid:
            raise HTTPException(403, "Cannot update activities from other trips")

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
