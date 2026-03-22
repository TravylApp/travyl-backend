"""CRUD endpoints for favorite places (TRA-224)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.services.supabase import get_supabase

router = APIRouter(prefix="/api/favorites", tags=["favorites"])

ACTIVITY_TYPE = Literal["hotel", "airport", "food", "nature", "amusement park", "other"]


# ---------- Schemas ----------

class FavoriteCreate(BaseModel):
    activity_type: ACTIVITY_TYPE = "other"
    activity_data: dict = Field(default_factory=dict)


# ---------- Endpoints ----------

@router.get("")
def list_favorites(user: dict = Depends(get_current_user)):
    sb = get_supabase()
    res = (
        sb.table("favorite_places")
        .select("*")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


@router.post("", status_code=201)
def add_favorite(body: FavoriteCreate, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    row = body.model_dump()
    row["user_id"] = user["id"]
    res = sb.table("favorite_places").insert(row).execute()
    return res.data[0]


@router.delete("/{favorite_id}", status_code=204)
def remove_favorite(favorite_id: str, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    res = (
        sb.table("favorite_places")
        .delete()
        .eq("id", favorite_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Favorite not found")
