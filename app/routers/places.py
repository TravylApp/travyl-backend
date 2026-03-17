import asyncio
import json
import logging
from math import log2

import aiohttp
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.schemas import POI, MenuItem, MenuResponse
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
