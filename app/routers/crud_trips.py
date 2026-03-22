"""CRUD endpoints for trips (TRA-219)."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/trips", tags=["trips-crud"])
log = logging.getLogger(__name__)


# ---------- Schemas ----------

class TripCreate(BaseModel):
    title: str
    destination: str
    start_date: str | None = None
    end_date: str | None = None


class TripUpdate(BaseModel):
    title: str | None = None
    destination: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str | None = None
    visibility: str | None = None
    link_permission: str | None = None


# ---------- Endpoints ----------

@router.get("")
async def list_trips(user: dict = Depends(get_current_user)):
    sb = get_supabase()
    res = sb.table("trips").select("*").eq("user_id", user["id"]).order("created_at", desc=True).execute()
    return res.data


@router.get("/public")
async def list_public_trips():
    sb = get_supabase()
    res = (
        sb.table("trips")
        .select("*, profiles!trips_user_id_fkey(display_name, avatar_url)")
        .or_("visibility.eq.public,visibility.eq.link")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


@router.get("/share/{token}")
async def get_trip_by_share_token(token: str):
    sb = get_supabase()
    res = sb.table("trips").select("*").eq("share_link_token", token).maybe_single().execute()
    if not res.data:
        raise HTTPException(404, "Trip not found")
    return res.data


@router.get("/{trip_id}")
async def get_trip(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    res = sb.table("trips").select("*").eq("id", trip_id).maybe_single().execute()
    if not res.data:
        raise HTTPException(404, "Trip not found")
    trip = res.data
    if trip["user_id"] != user["id"]:
        # check if collaborator
        collab = (
            sb.table("trip_collaborators")
            .select("id")
            .eq("trip_id", trip_id)
            .eq("user_id", user["id"])
            .eq("invite_status", "accepted")
            .maybe_single()
            .execute()
        )
        if not collab.data:
            raise HTTPException(403, "Not authorised")
    return trip


@router.post("", status_code=201)
async def create_trip(body: TripCreate, user: dict = Depends(get_current_user)):
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
async def update_trip(trip_id: str, body: TripUpdate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_owner(sb, trip_id, user["id"])
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    res = sb.table("trips").update(updates).eq("id", trip_id).execute()
    return res.data[0]


@router.delete("/{trip_id}", status_code=204)
async def delete_trip(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_owner(sb, trip_id, user["id"])
    sb.table("trips").delete().eq("id", trip_id).execute()


@router.post("/{trip_id}/share-token")
async def generate_share_token(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_owner(sb, trip_id, user["id"])
    existing = sb.table("trips").select("share_link_token").eq("id", trip_id).single().execute()
    if existing.data.get("share_link_token"):
        return {"share_link_token": existing.data["share_link_token"]}
    token = str(uuid.uuid4())
    sb.table("trips").update({"share_link_token": token}).eq("id", trip_id).execute()
    return {"share_link_token": token}


@router.post("/{trip_id}/fork", status_code=201)
async def fork_trip(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    # read original trip
    original = sb.table("trips").select("*").eq("id", trip_id).maybe_single().execute()
    if not original.data:
        raise HTTPException(404, "Trip not found")
    src = original.data

    # create forked trip
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
    for act in acts.data:
        act.pop("id", None)
        act.pop("created_at", None)
        act.pop("updated_at", None)
        act["trip_id"] = new_trip_id
        act["user_id"] = user["id"]
    if acts.data:
        sb.table("activity").insert(acts.data).execute()

    return trip_res.data[0]


# ---------- Helpers ----------

def _assert_trip_owner(sb, trip_id: str, user_id: str):
    res = sb.table("trips").select("user_id").eq("id", trip_id).maybe_single().execute()
    if not res.data:
        raise HTTPException(404, "Trip not found")
    if res.data["user_id"] != user_id:
        raise HTTPException(403, "Not authorised")
