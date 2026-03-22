"""CRUD endpoints for trip collaborators (TRA-222)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.access import ROLE_TYPE, assert_trip_access, assert_trip_owner, validate_uuid
from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/trips/{trip_id}/collaborators", tags=["collaborators"])
_PERMISSION_TO_ROLE = {"view": "viewer", "comment": "commenter", "edit": "editor"}


class InviteCreate(BaseModel):
    invited_email: str
    role_type: ROLE_TYPE = "viewer"


class CollaboratorRoleUpdate(BaseModel):
    role_type: ROLE_TYPE



@router.get("")
def list_collaborators(trip_id: str, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    assert_trip_access(trip_id, user["id"], sb)
    res = (
        sb.table("trip_collaborators")
        .select("id, trip_id, user_id, invited_email, role_type, invite_status, created_at, accepted_at")
        .eq("trip_id", trip_id)
        .order("created_at")
        .execute()
    )
    return res.data


@router.post("/invite", status_code=201)
def invite_collaborator(trip_id: str, body: InviteCreate, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    assert_trip_owner(trip_id, user["id"], sb)

    existing = (
        sb.table("trip_collaborators")
        .select("id")
        .eq("trip_id", trip_id)
        .eq("invited_email", body.invited_email)
        .eq("invite_status", "pending")
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
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


@router.patch("/{collaborator_id}")
def update_collaborator_role(
    trip_id: str, collaborator_id: str, body: CollaboratorRoleUpdate,
    user: dict = Depends(get_current_user),
):
    validate_uuid(trip_id, "Trip")
    validate_uuid(collaborator_id, "Collaborator")
    sb = get_supabase()
    assert_trip_owner(trip_id, user["id"], sb)
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


@router.delete("/{collaborator_id}", status_code=204)
def remove_collaborator(
    trip_id: str, collaborator_id: str, user: dict = Depends(get_current_user),
):
    validate_uuid(trip_id, "Trip")
    validate_uuid(collaborator_id, "Collaborator")
    sb = get_supabase()
    assert_trip_owner(trip_id, user["id"], sb)
    res = (
        sb.table("trip_collaborators")
        .delete()
        .eq("id", collaborator_id)
        .eq("trip_id", trip_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Collaborator not found")


@router.post("/join", status_code=201)
def join_via_link(trip_id: str, user: dict = Depends(get_current_user)):
    validate_uuid(trip_id, "Trip")
    sb = get_supabase()
    trip = sb.table("trips").select("visibility, link_permission, user_id").eq("id", trip_id).maybe_single().execute()
    if not trip or not trip.data:
        raise HTTPException(404, "Trip not found")
    if trip.data["visibility"] not in ("link", "public"):
        raise HTTPException(403, "Trip is not shared via link")
    if trip.data["user_id"] == user["id"]:
        raise HTTPException(400, "You are the trip owner")

    existing = (
        sb.table("trip_collaborators")
        .select("id")
        .eq("trip_id", trip_id)
        .eq("user_id", user["id"])
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
        raise HTTPException(409, "Already a collaborator")

    role = trip.data.get("link_permission", "view")
    row = {
        "trip_id": trip_id,
        "user_id": user["id"],
        "role_type": _PERMISSION_TO_ROLE.get(role, "viewer"),
        "invite_status": "accepted",
        "invited_by": trip.data["user_id"],
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
    res = sb.table("trip_collaborators").insert(row).execute()
    return res.data[0]


# registered separately in main.py as accept_router

accept_router = APIRouter(prefix="/api/collaborators", tags=["collaborators"])


@accept_router.post("/accept/{invite_token}")
def accept_invite(invite_token: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    invite = (
        sb.table("trip_collaborators")
        .select("id, invited_email, invite_status")
        .eq("invite_token", invite_token)
        .eq("invite_status", "pending")
        .maybe_single()
        .execute()
    )
    if not invite or not invite.data:
        raise HTTPException(404, "Invite not found or already accepted")
    if invite.data["invited_email"] != user.get("email"):
        raise HTTPException(403, "This invite was sent to a different email")
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
