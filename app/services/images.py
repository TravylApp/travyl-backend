"""Place image service — fetch candidate images via SerpAPI, pick the best with Claude.
Guaranteed to return an image: SerpAPI → Unsplash (specific) → Unsplash (city) → Unsplash (generic travel)."""

import asyncio
import logging

import aiohttp

from app.config import settings
from app.services.bedrock import _get_bedrock

log = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"
_UNSPLASH_API = "https://api.unsplash.com/search/photos"
_UNSPLASH_RANDOM = "https://api.unsplash.com/photos/random"


async def _fetch_image_candidates(name: str, city: str | None = None, limit: int = 8) -> list[dict]:
    """Fetch image candidates from SerpAPI Google Images."""
    if not settings.serpapi_key:
        return []

    query = f"{name} {city}" if city else name
    params = {
        "engine": "google_images",
        "q": query,
        "num": limit,
        "api_key": settings.serpapi_key,
        "hl": "en",
        "safe": "active",
    }

    headers = {"Accept-Encoding": "gzip, deflate"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(_SERPAPI_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning("SerpAPI images returned %d for %r", resp.status, query)
                    return []
                data = await resp.json()
    except Exception as e:
        log.warning("SerpAPI request failed for %r: %s", query, e)
        return []

    results = []
    for img in data.get("images_results", [])[:limit]:
        results.append({
            "url": img.get("original"),
            "thumbnail": img.get("thumbnail"),
            "title": img.get("title", ""),
            "source": img.get("source", ""),
            "width": img.get("original_width", 0),
            "height": img.get("original_height", 0),
        })
    return [r for r in results if r["url"]]


def _pick_best_image(candidates: list[dict], place_name: str) -> dict | None:
    """Use Claude Haiku to pick the most relevant/appealing image for a place."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    descriptions = []
    for i, c in enumerate(candidates):
        descriptions.append(
            f'{i}: title="{c["title"]}", source="{c["source"]}", '
            f'size={c["width"]}x{c["height"]}'
        )
    candidates_text = "\n".join(descriptions)

    prompt = (
        f'Pick the single best image to represent "{place_name}" for a travel app. '
        f"Choose the image that is most visually appealing, shows the actual place "
        f"(not a logo, map, or generic stock photo), and would look best as a card thumbnail.\n\n"
        f"Candidates:\n{candidates_text}\n\n"
        f"Reply with ONLY the number (0-{len(candidates)-1}) of the best image. Nothing else."
    )

    try:
        response = _get_bedrock().converse(
            modelId=settings.bedrock_model_id,
            system=[{"text": "You are an image selector. Reply with only a number."}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 5},
        )
        text = response["output"]["message"]["content"][0]["text"].strip()
        idx = int(text)
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except Exception as e:
        log.warning("AI image pick failed for %r: %s", place_name, e)

    # fallback: pick largest image
    return max(candidates, key=lambda c: c["width"] * c["height"])


def _unsplash_photo_to_result(photo: dict, fallback_title: str) -> dict:
    urls = photo.get("urls", {})
    return {
        "url": urls.get("regular") or urls.get("full", ""),
        "thumbnail": urls.get("small") or urls.get("thumb", ""),
        "title": photo.get("description") or photo.get("alt_description") or fallback_title,
        "source": f"unsplash.com/@{photo.get('user', {}).get('username', 'unknown')}",
        "width": photo.get("width", 1200),
        "height": photo.get("height", 800),
    }


async def _unsplash_search(query: str) -> dict | None:
    """Search Unsplash for a landscape photo matching the query."""
    if not settings.unsplash_access_key:
        return None

    headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}
    params = {"query": query, "per_page": "1", "orientation": "landscape"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _UNSPLASH_API, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                results = data.get("results", [])
                if not results:
                    return None
                return _unsplash_photo_to_result(results[0], query)
    except Exception as e:
        log.warning("Unsplash search failed for %r: %s", query, e)
        return None


async def _unsplash_random(query: str) -> dict | None:
    """Get a random Unsplash photo matching a broad topic query."""
    if not settings.unsplash_access_key:
        return None

    headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}
    params = {"query": query, "orientation": "landscape"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _UNSPLASH_RANDOM, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    return None
                photo = await resp.json()
                return _unsplash_photo_to_result(photo, query)
    except Exception as e:
        log.warning("Unsplash random failed for %r: %s", query, e)
        return None


async def get_place_image(name: str, city: str | None = None) -> dict | None:
    """Fetch the best image for a place. Cascades through multiple API sources:
    1. SerpAPI (specific query)
    2. Unsplash search (specific: "name city")
    3. Unsplash search (city only)
    4. Unsplash random (generic travel photo)
    Always returns an image unless every API is down."""

    # 1. SerpAPI — best quality, AI-picked
    candidates = await _fetch_image_candidates(name, city)
    if candidates:
        result = await asyncio.to_thread(_pick_best_image, candidates, name)
        if result:
            return result

    # 2. Unsplash search — specific place + city
    query = f"{name} {city}" if city else name
    result = await _unsplash_search(query)
    if result:
        return result

    # 3. Unsplash search — just the city
    if city:
        result = await _unsplash_search(f"{city} travel")
        if result:
            return result

    # 4. Unsplash random — generic travel destination photo
    result = await _unsplash_random("travel destination landscape")
    if result:
        return result

    return None
