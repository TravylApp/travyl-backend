"""CRUD endpoints for trip notes (TRA-221)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.access import assert_trip_access
from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/trips/{trip_id}/notes", tags=["trip-notes"])


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
def list_notes(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb)
    res = (
        sb.table("trip_notes")
        .select("*")
        .eq("trip_id", trip_id)
        .order("created_at")
        .execute()
    )
    return res.data


@router.post("", status_code=201)
def create_note(trip_id: str, body: NoteCreate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb)
    row = body.model_dump()
    row["trip_id"] = trip_id
    row["user_id"] = user["id"]
    res = sb.table("trip_notes").insert(row).execute()
    return res.data[0]


@router.patch("/{note_id}")
def update_note(
    trip_id: str, note_id: str, body: NoteUpdate, user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb)
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
def delete_note(trip_id: str, note_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb)
    res = sb.table("trip_notes").delete().eq("id", note_id).eq("trip_id", trip_id).execute()
    if not res.data:
        raise HTTPException(404, "Note not found")
