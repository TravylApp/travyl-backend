"""Image search endpoints — expose the image service over HTTP."""

import logging
from typing import Literal

import aiohttp
from fastapi import APIRouter, Query

from app.config import settings
from app.services.images import (
    _UNSPLASH_API,
    _unsplash_photo_to_result,
    get_place_image,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["images"])


def _to_response_image(candidate: dict) -> dict:
    """Normalise an internal image-candidate dict to the API response shape."""
    source = candidate.get("source", "")
    return {
        "url": candidate.get("url", ""),
        "thumb": candidate.get("thumbnail", candidate.get("url", "")),
        "credit": source,
    }


async def _unsplash_search_multi(
    query: str,
    per_page: int = 5,
    orientation: str = "landscape",
) -> list[dict]:
    """Search Unsplash and return up to *per_page* results."""
    if not settings.unsplash_access_key:
        return []

    headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}
    params = {
        "query": query,
        "per_page": str(per_page),
        "orientation": orientation,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _UNSPLASH_API,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("Unsplash multi-search returned %d for %r", resp.status, query)
                    return []
                data = await resp.json()
                return [
                    _unsplash_photo_to_result(photo, query)
                    for photo in data.get("results", [])
                ]
    except Exception as e:
        log.warning("Unsplash multi-search failed for %r: %s", query, e)
        return []


# ---------------------------------------------------------------------------
# GET /api/images/search
# ---------------------------------------------------------------------------

@router.get("/search")
async def search_images(
    q: str = Query(..., description="Search query"),
    type: Literal["hero", "restaurant", "activity", "hotel"] | None = Query(
        None, description="Image type hint",
    ),
    per_page: int = Query(5, ge=1, le=30, description="Number of images"),
):
    """Search for images matching a query.

    For single-image requests (per_page=1) the full AI-powered cascade from the
    image service is used.  For multi-image requests, Unsplash is queried
    directly so that several results can be returned quickly.
    """
    search_query = q
    if type:
        search_query = f"{q} {type}"

    if per_page == 1:
        result = await get_place_image(q)
        if result:
            img = _to_response_image(result)
            return {
                "images": [img],
                "url": img["url"],
                "thumb": img["thumb"],
                "credit": img["credit"],
            }
        return {"images": [], "url": None, "thumb": None, "credit": None}

    # Multi-image: go straight to Unsplash for speed
    candidates = await _unsplash_search_multi(search_query, per_page=per_page)
    images = [_to_response_image(c) for c in candidates]

    first = images[0] if images else {"url": None, "thumb": None, "credit": None}
    return {
        "images": images,
        "url": first["url"],
        "thumb": first["thumb"],
        "credit": first["credit"],
    }


# ---------------------------------------------------------------------------
# GET /api/images/destination
# ---------------------------------------------------------------------------

@router.get("/destination")
async def destination_image(
    destination: str = Query(..., description="Destination name"),
):
    """Return a single hero image URL for a destination."""
    result = await get_place_image(destination)
    return {"url": result["url"] if result else None}
