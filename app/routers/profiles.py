"""CRUD endpoints for user profiles (TRA-223)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


# ---------- Schemas ----------

class ProfileUpdate(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None
    onboarding_completed: bool | None = None
    preferences: dict | None = None


# ---------- Endpoints ----------

@router.get("/me")
def get_my_profile(user: dict = Depends(get_current_user)):
    sb = get_supabase()
    res = sb.table("profiles").select("*").eq("id", user["id"]).maybe_single().execute()
    if not res.data:
        raise HTTPException(404, "Profile not found")
    return res.data


@router.patch("/me")
def update_my_profile(body: ProfileUpdate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    res = sb.table("profiles").update(updates).eq("id", user["id"]).execute()
    if not res.data:
        raise HTTPException(404, "Profile not found")
    return res.data[0]


@router.get("/{username}")
def get_public_profile(username: str):
    sb = get_supabase()
    res = (
        sb.table("profiles")
        .select("id, display_name, avatar_url")
        .ilike("display_name", username)
        .maybe_single()
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "User not found")
    return res.data
