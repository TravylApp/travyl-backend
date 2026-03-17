import logging
from typing import Literal

import aiohttp
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.schemas import HotelOption

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/hotels", tags=["hotels"])

_SERPAPI_URL = "https://serpapi.com/search"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


async def _serpapi(session: aiohttp.ClientSession, params: dict) -> dict | None:
    if not settings.serpapi_key:
        return None
    params["api_key"] = settings.serpapi_key
    try:
        async with session.get(_SERPAPI_URL, params=params) as resp:
            if resp.status == 429:
                log.warning("SerpAPI rate limited")
                return None
            resp.raise_for_status()
            return await resp.json()
    except Exception as e:
        log.warning("SerpAPI hotel search failed: %s", e)
        return None


def _parse_hotels(data: dict) -> list[HotelOption]:
    hotels: list[HotelOption] = []
    for item in data.get("properties", []):
        name = item.get("name")
        if not name:
            continue

        rate = item.get("rate_per_night", {})
        total = item.get("total_rate", {})
        gps = item.get("gps_coordinates", {})
        images = item.get("images", [])

        raw_stars = item.get("hotel_class")
        stars = None
        if isinstance(raw_stars, int):
            stars = raw_stars
        elif isinstance(raw_stars, str):
            digits = "".join(c for c in raw_stars if c.isdigit())
            stars = int(digits) if digits else None

        hotels.append(HotelOption(
            name=name,
            price_per_night=rate.get("extracted_lowest"),
            total_price=total.get("extracted_lowest"),
            currency="USD",
            rating=item.get("overall_rating"),
            review_count=item.get("reviews"),
            stars=stars,
            address=item.get("description"),
            lat=gps.get("latitude"),
            lng=gps.get("longitude"),
            photo_url=images[0].get("thumbnail") if images else item.get("thumbnail"),
            amenities=item.get("amenities", []),
            link=item.get("link"),
        ))
    return hotels


_SORT_KEYS = {
    "rating": lambda h: (-(h.rating or 0), h.price_per_night or float("inf")),
    "price": lambda h: (h.price_per_night or float("inf"), -(h.rating or 0)),
    "stars": lambda h: (-(h.stars or 0), -(h.rating or 0)),
}


@router.get("/search", response_model=list[HotelOption])
async def search_hotels(
    city: str = Query(...),
    country: str = Query(...),
    check_in: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    check_out: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    guests: int = Query(default=2, ge=1, le=10),
    min_stars: int | None = Query(default=None, ge=1, le=5),
    max_price: float | None = Query(default=None, gt=0),
    sort_by: Literal["rating", "price", "stars"] = Query(default="rating"),
):
    """Search hotels via SerpAPI google_hotels engine."""
    if not settings.serpapi_key:
        raise HTTPException(status_code=503, detail="Hotel search unavailable")

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        data = await _serpapi(session, {
            "engine": "google_hotels",
            "q": f"{city} {country}",
            "check_in_date": check_in,
            "check_out_date": check_out,
            "adults": str(guests),
            "currency": "USD",
            "hl": "en",
            "gl": "us",
        })

    if not data:
        raise HTTPException(status_code=502, detail="Hotel search returned no results")

    hotels = _parse_hotels(data)

    if min_stars is not None:
        hotels = [h for h in hotels if h.stars is not None and h.stars >= min_stars]

    if max_price is not None:
        hotels = [h for h in hotels if h.price_per_night is not None and h.price_per_night <= max_price]

    hotels.sort(key=_SORT_KEYS[sort_by])

    return hotels
