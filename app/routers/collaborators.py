"""CRUD endpoints for trip collaborators (TRA-222)."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(tags=["collaborators"])
log = logging.getLogger(__name__)


# ---------- Schemas ----------

class InviteCreate(BaseModel):
    invited_email: str
    role_type: str = "viewer"


class CollaboratorRoleUpdate(BaseModel):
    role_type: str


# ---------- Trip-scoped endpoints ----------

_trip_router = APIRouter(prefix="/api/trips/{trip_id}/collaborators")


@_trip_router.get("")
async def list_collaborators(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_access(sb, trip_id, user["id"])
    res = (
        sb.table("trip_collaborators")
        .select("*")
        .eq("trip_id", trip_id)
        .order("created_at")
        .execute()
    )
    return res.data


@_trip_router.post("/invite", status_code=201)
async def invite_collaborator(trip_id: str, body: InviteCreate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    _assert_trip_owner(sb, trip_id, user["id"])

    # check for existing pending invite
    existing = (
        sb.table("trip_collaborators")
        .select("id")
        .eq("trip_id", trip_id)
        .eq("invited_email", body.invited_email)
        .eq("invite_status", "pending")
        .maybe_single()
        .execute()
    )
    if existing.data:
        raise HTTPException(409, "Pending invite already exists for this email")

    row = {
        "trip_id": trip_id,
        "invited_email": body.invited_email,
        "role_type": body.role_type,
        "invite_token": str(uuid.uuid4()),
        "invited_by": user["id"],
        "invite_status": "pending",
    }
    res = sb.table("trip_collaborators").insert(row).execute()
    return res.data[0]


@_trip_router.patch("/{collaborator_id}")
async def update_collaborator_role(
    trip_id: str, collaborator_id: str, body: CollaboratorRoleUpdate,
    user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    _assert_trip_owner(sb, trip_id, user["id"])
    res = (
        sb.table("trip_collaborators")
        .update({"role_type": body.role_type})
        .eq("id", collaborator_id)
        .eq("trip_id", trip_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Collaborator not found")
    return res.data[0]


@_trip_router.delete("/{collaborator_id}", status_code=204)
async def remove_collaborator(
    trip_id: str, collaborator_id: str, user: dict = Depends(get_current_user),
):
    sb = get_supabase()
    _assert_trip_owner(sb, trip_id, user["id"])
    res = (
        sb.table("trip_collaborators")
        .delete()
        .eq("id", collaborator_id)
        .eq("trip_id", trip_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Collaborator not found")


@_trip_router.post("/join", status_code=201)
async def join_via_link(trip_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    trip = sb.table("trips").select("visibility, link_permission, user_id").eq("id", trip_id).maybe_single().execute()
    if not trip.data:
        raise HTTPException(404, "Trip not found")
    if trip.data["visibility"] not in ("link", "public"):
        raise HTTPException(403, "Trip is not shared via link")
    if trip.data["user_id"] == user["id"]:
        raise HTTPException(400, "You are the trip owner")

    # check if already a collaborator
    existing = (
        sb.table("trip_collaborators")
        .select("id")
        .eq("trip_id", trip_id)
        .eq("user_id", user["id"])
        .maybe_single()
        .execute()
    )
    if existing.data:
        raise HTTPException(409, "Already a collaborator")

    role = trip.data.get("link_permission", "view")
    role_map = {"view": "viewer", "comment": "commenter", "edit": "editor"}
    row = {
        "trip_id": trip_id,
        "user_id": user["id"],
        "role_type": role_map.get(role, "viewer"),
        "invite_status": "accepted",
        "invited_by": trip.data["user_id"],
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
    res = sb.table("trip_collaborators").insert(row).execute()
    return res.data[0]


# ---------- Non-trip-scoped endpoint ----------

_accept_router = APIRouter(prefix="/api/collaborators")


@_accept_router.post("/accept/{invite_token}")
async def accept_invite(invite_token: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    invite = (
        sb.table("trip_collaborators")
        .select("*")
        .eq("invite_token", invite_token)
        .eq("invite_status", "pending")
        .maybe_single()
        .execute()
    )
    if not invite.data:
        raise HTTPException(404, "Invite not found or already accepted")

    res = (
        sb.table("trip_collaborators")
        .update({
            "user_id": user["id"],
            "invite_status": "accepted",
            "accepted_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", invite.data["id"])
        .execute()
    )
    return res.data[0]


# Combine both sub-routers into the exported router
router.include_router(_trip_router)
router.include_router(_accept_router)


# ---------- Helpers ----------

def _assert_trip_owner(sb, trip_id: str, user_id: str):
    trip = sb.table("trips").select("user_id").eq("id", trip_id).maybe_single().execute()
    if not trip.data:
        raise HTTPException(404, "Trip not found")
    if trip.data["user_id"] != user_id:
        raise HTTPException(403, "Only the trip owner can do this")


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
