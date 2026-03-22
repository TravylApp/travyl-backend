"""CRUD endpoints for trip notes (TRA-221)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/trips/{trip_id}/notes", tags=["trip-notes"])
log = logging.getLogger(__name__)


# ---------- Schemas ----------

class NoteCreate(BaseModel):
    day: str
    content: str = ""
    color: str = "#ffd93d"
    pos_x: float = 0.5
    pos_y: float = 0.5
    activity_id: str | None = None


class NoteUpdate(BaseModel):
    content: str | None = None
    color: str | None = None
    day: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    activity_id: str | None = None


# ---------- Endpoints ----------

@router.get("")
async def list_notes(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    res = (
        sb.table("trip_notes")
        .select("*")
        .eq("trip_id", trip_id)
        .order("created_at")
        .execute()
    )
    return res.data


@router.post("", status_code=201)
async def create_note(trip_id: str, body: NoteCreate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    row = body.model_dump()
    row["trip_id"] = trip_id
    row["user_id"] = user["id"]
    res = sb.table("trip_notes").insert(row).execute()
    return res.data[0]


@router.patch("/{note_id}")
async def update_note(
    trip_id: str, note_id: str, body: NoteUpdate, user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    res = (
        sb.table("trip_notes")
        .update(updates)
        .eq("id", note_id)
        .eq("trip_id", trip_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Note not found")
    return res.data[0]


@router.delete("/{note_id}", status_code=204)
async def delete_note(trip_id: str, note_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    res = sb.table("trip_notes").delete().eq("id", note_id).eq("trip_id", trip_id).execute()
    if not res.data:
        raise HTTPException(404, "Note not found")


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
