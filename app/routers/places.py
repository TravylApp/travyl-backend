import logging
from math import log2

import aiohttp
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.schemas import POI, PlaceDetails, PlaceReview

router = APIRouter(prefix="/api/places", tags=["places"])
log = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"
_UA = "TravylApp/1.0 (https://gotravyl.com; dev@gotravyl.com)"
_TIMEOUT = aiohttp.ClientTimeout(total=15)

_CATEGORY_QUERIES = {
    "restaurant": "restaurants",
    "attraction": "attractions",
    "nightlife": "nightlife bars clubs",
    "shopping": "shopping",
    "nature": "parks nature",
    "entertainment": "entertainment",
    "cafe": "cafes coffee shops",
    "hotel": "hotels",
}

_CATEGORY_DEFAULTS = {
    "restaurant": ("restaurant", "restaurant"),
    "nightlife": ("nightlife", "bar"),
    "shopping": ("shopping", "mall"),
    "nature": ("nature", "park"),
    "entertainment": ("entertainment", "theatre"),
    "cafe": ("restaurant", "cafe"),
    "hotel": ("attraction", "attraction"),
}

_SUBCAT_KEYWORDS = {
    "museum": "museum", "park": "park", "church": "church",
    "temple": "temple", "castle": "castle", "gallery": "gallery",
    "bar": "bar", "pub": "pub", "club": "nightclub",
    "cafe": "cafe", "coffee": "cafe", "mall": "mall",
    "market": "marketplace", "beach": "beach", "garden": "garden",
}

_VISIT_DURATIONS = {
    "museum": 90, "gallery": 60, "monument": 20, "castle": 90,
    "attraction": 60, "viewpoint": 20,
    "restaurant": 75, "cafe": 40, "fast_food": 25,
    "bar": 60, "pub": 75, "nightclub": 120,
    "park": 45, "garden": 45, "beach": 120,
    "place_of_worship": 30, "theatre": 150,
    "mall": 90, "marketplace": 60,
}

_SUBCATEGORY_TAGS = {
    "museum": ["history", "culture", "art"],
    "gallery": ["art", "culture"],
    "castle": ["history", "architecture"],
    "attraction": ["culture"],
    "restaurant": ["food", "local_cuisine"],
    "cafe": ["food"],
    "bar": ["nightlife"],
    "pub": ["nightlife"],
    "nightclub": ["nightlife", "music"],
    "park": ["nature", "relaxation"],
    "garden": ["nature", "relaxation"],
    "beach": ["beach", "relaxation", "nature"],
    "mall": ["shopping"],
    "marketplace": ["shopping", "food"],
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _serpapi(params: dict) -> dict | None:
    if not settings.serpapi_key:
        return None
    params["api_key"] = settings.serpapi_key
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": _UA, "Accept-Encoding": "gzip, deflate"},
            timeout=_TIMEOUT,
        ) as session:
            async with session.get(_SERPAPI_URL, params=params) as resp:
                if resp.status == 429:
                    log.warning("SerpAPI rate limited")
                    return None
                resp.raise_for_status()
                return await resp.json()
    except Exception as e:
        log.warning("SerpAPI request failed: %s", e)
        return None


def _parse_price_level(price_str: str | None) -> int | None:
    if not price_str:
        return None
    count = price_str.count("$")
    return min(count, 4) if count else None


def _radius_to_zoom(radius_km: float) -> int:
    if radius_km <= 0:
        return 14
    zoom = 15.5 - log2(max(radius_km, 0.1))
    return max(8, min(18, round(zoom)))


def _detect_subcategory(item_type: str, category: str) -> str:
    item_lower = item_type.lower()
    for keyword, subcat in _SUBCAT_KEYWORDS.items():
        if keyword in item_lower:
            return subcat
    defaults = _CATEGORY_DEFAULTS.get(category)
    return defaults[1] if defaults else "attraction"


# ---------------------------------------------------------------------------
# GET /api/places/nearby
# ---------------------------------------------------------------------------

@router.get("/nearby", response_model=list[POI])
async def nearby_places(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    category: str | None = Query(None, description="Category filter"),
    radius_km: float = Query(2.0, ge=0.1, le=50.0),
    limit: int = Query(20, ge=1, le=50),
):
    if not settings.serpapi_key:
        return []

    q = _CATEGORY_QUERIES.get(category, "things to do") if category else "things to do near me"
    zoom = _radius_to_zoom(radius_km)

    data = await _serpapi({
        "engine": "google_maps",
        "q": q,
        "ll": f"@{lat},{lng},{zoom}z",
        "hl": "en",
    })
    if not data:
        return []

    pois: list[POI] = []
    for item in data.get("local_results", []):
        title = item.get("title")
        gps = item.get("gps_coordinates", {})
        if not title or not gps:
            continue

        place_id = item.get("place_id", "")
        item_type = item.get("type") or ""

        if category and category in _CATEGORY_DEFAULTS:
            cat = _CATEGORY_DEFAULTS[category][0]
        else:
            cat = "attraction"

        subcat = _detect_subcategory(item_type, category or "attraction")

        pois.append(POI(
            id=f"serp_{place_id}" if place_id else f"serp_{title[:30]}",
            name=title,
            lat=gps.get("latitude", 0),
            lng=gps.get("longitude", 0),
            category=cat,
            subcategory=subcat,
            rating=item.get("rating"),
            review_count=item.get("reviews"),
            price_level=_parse_price_level(item.get("price")),
            opening_hours=item.get("operating_hours"),
            description=item.get("description"),
            photo_url=item.get("thumbnail"),
            website=item.get("website"),
            visit_duration_min=_VISIT_DURATIONS.get(subcat, 60),
            tags=_SUBCATEGORY_TAGS.get(subcat, []),
            source="serpapi",
        ))

        if len(pois) >= limit:
            break

    log.info("Nearby: %d results at (%.4f, %.4f) category=%s", len(pois), lat, lng, category)
    return pois


# ---------------------------------------------------------------------------
# GET /api/places/details
# ---------------------------------------------------------------------------

def _parse_details(data: dict) -> PlaceDetails:
    pr = data.get("place_results", {})
    gps = pr.get("gps_coordinates", {})

    reviews = []
    for r in data.get("reviews", []):
        author = r.get("user", {}).get("name") or r.get("source", "")
        text = r.get("snippet") or r.get("extracted_snippet", {}).get("snippet", "")
        if not text:
            continue
        reviews.append(PlaceReview(
            author=author, rating=r.get("rating"),
            text=text, date=r.get("date"),
        ))

    photos = [p["image"] for p in data.get("photos", []) if p.get("image")]

    hours = {}
    for entry in pr.get("hours", []):
        day, times = entry.get("day"), entry.get("hours")
        if day and times:
            hours[day] = times

    category = pr.get("type")
    if not category:
        types = pr.get("types", [])
        category = types[0] if types else None

    return PlaceDetails(
        place_id=pr.get("place_id"),
        name=pr.get("title", ""),
        lat=gps.get("latitude"),
        lng=gps.get("longitude"),
        rating=pr.get("rating"),
        review_count=pr.get("reviews"),
        reviews=reviews,
        photos=photos,
        opening_hours=hours,
        phone=pr.get("phone"),
        website=pr.get("website"),
        address=pr.get("address"),
        price_level=_parse_price_level(pr.get("price")),
        category=category,
        description=pr.get("description"),
    )


@router.get("/details", response_model=PlaceDetails)
async def get_place_details(
    place_id: str | None = Query(None, description="SerpAPI/Google place_id"),
    name: str | None = Query(None, description="Place name"),
    city: str | None = Query(None, description="City for context"),
):
    if not place_id and not name:
        raise HTTPException(status_code=400, detail="Provide place_id or name")
    if not settings.serpapi_key:
        raise HTTPException(status_code=503, detail="SerpAPI not configured")

    if not place_id:
        data = await _serpapi({"engine": "google_maps", "q": f"{name} {city}" if city else name, "hl": "en"})
        results = (data or {}).get("local_results", [])
        if not results:
            raise HTTPException(status_code=404, detail="Place not found")
        place_id = results[0].get("place_id")

    data = await _serpapi({"engine": "google_maps", "place_id": place_id, "hl": "en"})
    if not data or not data.get("place_results"):
        raise HTTPException(status_code=404, detail="Place details not found")

    return _parse_details(data)
