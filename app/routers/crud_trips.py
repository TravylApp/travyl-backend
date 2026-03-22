"""CRUD endpoints for trips (TRA-219)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.access import assert_trip_access, assert_trip_owner, validate_uuid
from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/trips", tags=["trips-crud"])

_DEFAULT_LIMIT = 50


class TripCreate(BaseModel):
    title: str
    destination: str
    start_date: str
    end_date: str


class TripUpdate(BaseModel):
    title: str | None = None
    destination: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: Literal["planning", "confirmed", "completed", "cancelled"] | None = None
    visibility: Literal["private", "link", "public"] | None = None
    link_permission: Literal["view", "comment", "edit"] | None = None


@router.get("")
def list_trips(
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    res = (
        sb.table("trips")
        .select("*")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return res.data


@router.get("/public")
def list_public_trips(
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    sb = get_supabase()
    res = (
        sb.table("trips")
        .select("id, title, destination, start_date, end_date, visibility, created_at, user_id")
        .or_("visibility.eq.public,visibility.eq.link")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return res.data


@router.get("/share/{token}")
def get_trip_by_share_token(token: str):
    sb = get_supabase()
    res = sb.table("trips").select("id, title, destination, start_date, end_date, visibility, created_at").eq("share_link_token", token).maybe_single().execute()
    if not res or not res.data:
        raise HTTPException(404, "Trip not found")
    if res.data.get("visibility") == "private":
        raise HTTPException(403, "Trip is not shared")
    return res.data


@router.get("/{trip_id}")
def get_trip(trip_id: str, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb)
    res = sb.table("trips").select("*").eq("id", trip_id).maybe_single().execute()
    if not res or not res.data:
        raise HTTPException(404, "Trip not found")
    return res.data


@router.post("", status_code=201)
def create_trip(body: TripCreate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    row = {
        "user_id": user["id"],
        "title": body.title,
        "destination": body.destination,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "status": "planning",
    }
    res = sb.table("trips").insert(row).execute()
    return res.data[0]


@router.patch("/{trip_id}")
def update_trip(trip_id: str, body: TripUpdate, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    sb = get_supabase()
    assert_trip_owner(trip_id, user["id"], sb)
    res = sb.table("trips").update(updates).eq("id", trip_id).execute()
    return res.data[0]


@router.delete("/{trip_id}", status_code=204)
def delete_trip(trip_id: str, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    assert_trip_owner(trip_id, user["id"], sb)
    sb.table("trips").delete().eq("id", trip_id).execute()


@router.post("/{trip_id}/share-token")
def generate_share_token(trip_id: str, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    # single query: verify ownership + check existing token
    res = sb.table("trips").select("user_id, share_link_token").eq("id", trip_id).maybe_single().execute()
    if not res or not res.data:
        raise HTTPException(404, "Trip not found")
    if res.data["user_id"] != user["id"]:
        raise HTTPException(403, "Only the trip owner can do this")
    if res.data.get("share_link_token"):
        return {"share_link_token": res.data["share_link_token"]}
    token = str(uuid.uuid4())
    sb.table("trips").update({"share_link_token": token}).eq("id", trip_id).execute()
    return {"share_link_token": token}


@router.post("/{trip_id}/fork", status_code=201)
def fork_trip(trip_id: str, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    # verify access (owner or collaborator can fork)
    assert_trip_access(trip_id, user["id"], sb)

    original = sb.table("trips").select("title, destination, start_date, end_date").eq("id", trip_id).maybe_single().execute()
    if not original or not original.data:
        raise HTTPException(404, "Trip not found")
    src = original.data

    new_trip = {
        "user_id": user["id"],
        "title": f"{src['title']} (Fork)",
        "destination": src.get("destination"),
        "start_date": src.get("start_date"),
        "end_date": src.get("end_date"),
        "status": "planning",
    }
    trip_res = sb.table("trips").insert(new_trip).execute()
    new_trip_id = trip_res.data[0]["id"]

    # copy activities
    acts = sb.table("activity").select("*").eq("trip_id", trip_id).execute()
    if acts.data:
        rows = []
        for act in acts.data:
            act.pop("id", None)
            act.pop("created_at", None)
            act.pop("updated_at", None)
            act["trip_id"] = new_trip_id
            act["user_id"] = user["id"]
            rows.append(act)
        sb.table("activity").insert(rows).execute()

    return trip_res.data[0]
