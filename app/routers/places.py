import asyncio
import json
import logging
import re
from math import log2

import aiohttp
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.schemas import (
    POI, MenuItem, MenuResponse,
    PlaceDetail, SuggestionItem, SuggestResponse,
    EnrichPhoto, EnrichResponse,
)
from app.services.bedrock import _get_bedrock

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

# Page queries for suggest endpoint — rotated per page for variety
_PAGE_QUERIES = [
    "top things to do",
    "top tourist attractions",
    "best restaurants",
    "best bars and nightlife",
    "museums and cultural sites",
    "outdoor activities",
    "guided tours and excursions",
]

_SUGGEST_CATEGORY_QUERIES: dict[str, str] = {
    "all": _PAGE_QUERIES[0],
    "sightseeing": _PAGE_QUERIES[1],
    "dining": _PAGE_QUERIES[2],
    "nightlife": _PAGE_QUERIES[3],
    "cultural": _PAGE_QUERIES[4],
    "shopping": "shopping",
    "outdoor": _PAGE_QUERIES[5],
    "tour": _PAGE_QUERIES[6],
}

_MENU_EXTRACT_PROMPT = """\
Extract all menu items from this restaurant menu image. For each item return: \
name, price (as shown on menu including currency symbol, or null), description \
(short if visible, or null), dietary_tags (from: vegan, vegetarian, gluten-free, \
halal, nut-free — only if indicated on menu), and category (appetizer, main, \
dessert, drink, side).

Return a JSON array of objects. Example:
[{"name": "Margherita Pizza", "price": "$14", "description": "Fresh mozzarella, basil, tomato sauce", "dietary_tags": ["vegetarian"], "category": "main"}]

Return ONLY the JSON array, no markdown or explanation."""


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
# GET /api/places/menu
# ---------------------------------------------------------------------------

async def _download_image(url: str) -> tuple[bytes, str] | None:
    try:
        async with aiohttp.ClientSession(
            headers={"Accept-Encoding": "gzip, deflate"}, timeout=_TIMEOUT,
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                content_type = resp.headers.get("Content-Type", "")
                data = await resp.read()
                if len(data) < 1000:
                    return None
                if "png" in content_type:
                    fmt = "png"
                elif "webp" in content_type:
                    fmt = "webp"
                else:
                    fmt = "jpeg"
                return data, fmt
    except Exception as e:
        log.warning("Image download failed: %s", e)
        return None


def _call_bedrock_multimodal(image_bytes: bytes, image_format: str, prompt: str) -> str:
    response = _get_bedrock().converse(
        modelId=settings.bedrock_model_id,
        messages=[{
            "role": "user",
            "content": [
                {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                {"text": prompt},
            ],
        }],
        inferenceConfig={"temperature": 0.1, "maxTokens": 4096},
    )
    for block in response.get("output", {}).get("message", {}).get("content", []):
        if "text" in block:
            return block["text"]
    return ""


def _parse_menu_items(raw_text: str) -> list[MenuItem]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        items_data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            return []
        try:
            items_data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []

    if not isinstance(items_data, list):
        return []

    return [
        MenuItem(
            name=item["name"],
            price=item.get("price"),
            description=item.get("description"),
            dietary_tags=item.get("dietary_tags", []),
            category=item.get("category"),
        )
        for item in items_data
        if isinstance(item, dict) and "name" in item
    ]


def _extract_menu_photos(place_data: dict) -> list[str]:
    if place_data.get("menu"):
        return []

    photos = []
    for img in place_data.get("images", []):
        title = (img.get("title") or "").lower()
        if any(kw in title for kw in ("menu", "food", "dish", "plate")):
            url = img.get("image") or img.get("thumbnail")
            if url:
                photos.append(url)

    for photo in place_data.get("user_photos", {}).get("menu", []):
        url = photo.get("image") or photo.get("thumbnail")
        if url:
            photos.append(url)

    if not photos:
        thumb = place_data.get("thumbnail")
        if thumb:
            photos.append(thumb)

    return photos[:3]


@router.get("/menu", response_model=MenuResponse)
async def get_menu(
    name: str = Query(..., description="Restaurant name"),
    city: str | None = Query(None, description="City for context"),
):
    if not settings.serpapi_key:
        raise HTTPException(status_code=503, detail="SerpAPI not configured")

    q = f"{name} restaurant {city}" if city else f"{name} restaurant"
    data = await _serpapi({"engine": "google_maps", "q": q, "hl": "en"})
    results = (data or {}).get("local_results", [])
    if not results:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    place = results[0]
    restaurant_name = place.get("title", name)
    menu_url = place.get("menu") or place.get("website")

    # fetch place details for photos
    data_id = place.get("data_id")
    details = place
    if data_id:
        detail_data = await _serpapi({"engine": "google_maps", "data_id": data_id, "hl": "en"})
        if detail_data:
            details = detail_data

    photo_urls = _extract_menu_photos(details)

    if details.get("menu") and not photo_urls:
        return MenuResponse(restaurant_name=restaurant_name, menu_url=details["menu"], source="serpapi")

    if not photo_urls:
        return MenuResponse(restaurant_name=restaurant_name, menu_url=menu_url, source="serpapi")

    # extract menu from photos with multimodal LLM
    all_items: list[MenuItem] = []
    for url in photo_urls:
        result = await _download_image(url)
        if not result:
            continue
        image_bytes, fmt = result
        try:
            raw_text = await asyncio.to_thread(
                _call_bedrock_multimodal, image_bytes, fmt, _MENU_EXTRACT_PROMPT,
            )
            all_items.extend(_parse_menu_items(raw_text))
        except Exception as e:
            log.warning("Menu extraction failed for %s: %s", restaurant_name, e)

    # deduplicate by name
    seen: set[str] = set()
    deduped = []
    for item in all_items:
        key = item.name.lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return MenuResponse(
        restaurant_name=restaurant_name,
        menu_url=menu_url,
        items=deduped,
        source="ai_extracted" if deduped else "serpapi",
    )


# ---------------------------------------------------------------------------
# GET /api/places/suggest — local suggestion search
# ---------------------------------------------------------------------------

def _infer_category(place_type: str | None, fallback: str) -> str:
    """Map a SerpAPI place type string to a frontend category slug."""
    if not place_type:
        return fallback
    t = place_type.lower()
    if any(kw in t for kw in ("restaurant", "cafe", "bakery", "food", "bar")):
        return "dining"
    if any(kw in t for kw in ("museum", "gallery", "theater", "theatre", "library")):
        return "cultural"
    if any(kw in t for kw in ("park", "garden", "beach", "trail", "nature")):
        return "outdoor"
    if any(kw in t for kw in ("shop", "store", "mall", "market")):
        return "shopping"
    if any(kw in t for kw in ("club", "lounge", "nightlife")):
        return "nightlife"
    if any(kw in t for kw in ("tour", "agency")):
        return "tour"
    if any(kw in t for kw in ("church", "temple", "monument", "landmark", "attraction")):
        return "sightseeing"
    return fallback


def _upscale_thumbnail(url: str) -> str:
    """Replace Google thumbnail size params with a larger variant."""
    if not url:
        return url
    if re.search(r"lh\d*\.googleusercontent\.com", url):
        return re.sub(r"=([whs]\d+(-[a-zA-Z0-9]+)*)$", "=w800-h600", url)
    return url


def _extract_image_urls(place: dict) -> list[str]:
    """Pull image URLs from a SerpAPI local result, preferring non-encrypted ones."""
    seen: set[str] = set()
    urls: list[str] = []
    fallbacks: list[str] = []

    def _push(raw: str) -> None:
        if not raw:
            return
        if "encrypted-tbn" in raw:
            if raw not in seen:
                seen.add(raw)
                fallbacks.append(raw)
            return
        upscaled = _upscale_thumbnail(raw)
        if upscaled and upscaled not in seen:
            seen.add(upscaled)
            urls.append(upscaled)

    thumb = place.get("thumbnail")
    if thumb:
        _push(thumb)

    for p in place.get("photos", []):
        _push(p.get("original") or p.get("url") or p.get("thumbnail") or p.get("image") or "")

    return urls if urls else fallbacks


@router.get("/suggest", response_model=SuggestResponse)
async def suggest_places(
    destination: str = Query(..., description="Destination name (e.g. 'Paris, France')"),
    category: str = Query("all", description="Category filter"),
    q: str | None = Query(None, description="Custom search query"),
    page: int = Query(0, ge=0, description="Page index for paginated browsing"),
):
    """Local suggestion search — mirrors the frontend /api/suggest route."""
    if not settings.serpapi_key:
        return SuggestResponse()

    # Build SerpAPI location string
    parts = [p.strip() for p in destination.split(",")]
    serp_location = f"{parts[0]}, {parts[-1]}" if len(parts) >= 2 else destination

    # Determine search query
    if q:
        query = q
        has_more = False
    elif category != "all":
        query = _SUGGEST_CATEGORY_QUERIES.get(category, _SUGGEST_CATEGORY_QUERIES["all"])
        has_more = False
    else:
        if page >= len(_PAGE_QUERIES):
            return SuggestResponse()
        query = _PAGE_QUERIES[page]
        has_more = page + 1 < len(_PAGE_QUERIES)

    data = await _serpapi({
        "engine": "google_local",
        "q": query,
        "location": serp_location,
    })
    if not data:
        return SuggestResponse()

    local_results = data.get("local_results", [])[:20]

    suggestions: list[SuggestionItem] = []
    for i, place in enumerate(local_results):
        title = place.get("title")
        gps = place.get("gps_coordinates", {})
        if not title:
            continue

        image_urls = _extract_image_urls(place)

        suggestions.append(SuggestionItem(
            id=f"serp-{place.get('place_id', i)}",
            name=title,
            category=_infer_category(place.get("type"), "sightseeing"),
            imageUrl=image_urls[0] if image_urls else "",
            imageUrls=image_urls,
            rating=place.get("rating"),
            location=place.get("address", ""),
            latitude=gps.get("latitude", 0),
            longitude=gps.get("longitude", 0),
            description=place.get("description", ""),
            source="ai",
        ))

    next_page = page + 1 if has_more else None
    log.info("Suggest: %d results for '%s' in '%s'", len(suggestions), query, serp_location)
    return SuggestResponse(suggestions=suggestions, hasMore=has_more, nextPage=next_page)


# ---------------------------------------------------------------------------
# GET /api/places/enrich — photo enrichment
# ---------------------------------------------------------------------------

@router.get("/enrich", response_model=EnrichResponse)
async def enrich_place(
    placeId: str = Query(..., description="SerpAPI data_id / place_id"),
    name: str = Query("", description="Place name (used as fallback title)"),
):
    """Fetch high-res photos for a place via SerpAPI google_maps_photos."""
    if not settings.serpapi_key:
        raise HTTPException(status_code=503, detail="SerpAPI not configured")

    data = await _serpapi({
        "engine": "google_maps_photos",
        "data_id": placeId,
    })

    photos: list[EnrichPhoto] = []
    if data:
        for photo in data.get("photos", []):
            fullsize = photo.get("image") or photo.get("fullsize") or photo.get("thumbnail") or ""
            photos.append(EnrichPhoto(
                thumbnail=photo.get("thumbnail", ""),
                fullsize=fullsize,
                title=photo.get("title") or name,
            ))

    log.info("Enrich: %d photos for placeId=%s name=%s", len(photos), placeId, name)
    return EnrichResponse(photos=photos)


# ---------------------------------------------------------------------------
# GET /api/places/{place_id} — place detail
# ---------------------------------------------------------------------------

def _format_hours(hours_data: dict | list | None) -> str | None:
    """Convert SerpAPI operating_hours into a readable string."""
    if not hours_data:
        return None
    if isinstance(hours_data, str):
        return hours_data
    if isinstance(hours_data, dict):
        parts = [f"{day}: {times}" for day, times in hours_data.items()]
        return "; ".join(parts) if parts else None
    return None


@router.get("/{place_id}", response_model=PlaceDetail)
async def get_place_detail(place_id: str):
    """Fetch detailed info for a single place using its SerpAPI data_id."""
    if not settings.serpapi_key:
        raise HTTPException(status_code=503, detail="SerpAPI not configured")

    data = await _serpapi({
        "engine": "google_maps",
        "data_id": place_id,
        "hl": "en",
    })
    if not data:
        raise HTTPException(status_code=502, detail="Failed to fetch place details")

    # SerpAPI returns place_results for single-place lookups
    place = data.get("place_results", data)

    gps = place.get("gps_coordinates", {})

    # Collect images
    images: list[str] = []
    thumb = place.get("thumbnail")
    if thumb:
        images.append(thumb)
    for img in place.get("images", []):
        url = img.get("image") or img.get("thumbnail") or ""
        if url and url not in images:
            images.append(url)

    # Categories / type
    categories: list[str] = []
    place_type = place.get("type")
    if place_type:
        if isinstance(place_type, list):
            categories = place_type
        else:
            categories = [t.strip() for t in str(place_type).split(",")]

    # Extract city from address
    address = place.get("address")
    city: str | None = None
    if address:
        addr_parts = [p.strip() for p in address.split(",")]
        if len(addr_parts) >= 2:
            city = addr_parts[-2]  # second-to-last is usually city

    return PlaceDetail(
        id=place_id,
        name=place.get("title", ""),
        address=address,
        city=city,
        latitude=gps.get("latitude"),
        longitude=gps.get("longitude"),
        rating=place.get("rating"),
        phone=place.get("phone"),
        website=place.get("website"),
        description=place.get("description"),
        images=images,
        image_url=images[0] if images else None,
        categories=categories,
        hours=_format_hours(place.get("operating_hours")),
        price=_parse_price_level(place.get("price")),
        reviewCount=place.get("reviews"),
    )
