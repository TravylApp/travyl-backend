"""Place image service — fetch candidate images via SerpAPI, pick the best with Claude."""

import asyncio
import logging

import aiohttp

from app.config import settings
from app.services.bedrock import _get_bedrock

log = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"


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
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(_SERPAPI_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                log.warning("SerpAPI images returned %d for %r", resp.status, query)
                return []
            data = await resp.json()

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


async def get_place_image(name: str, city: str | None = None) -> dict | None:
    """Fetch and AI-select the best image for a place. Returns {url, thumbnail, title, source}."""
    candidates = await _fetch_image_candidates(name, city)
    if not candidates:
        return None
    result = await asyncio.to_thread(_pick_best_image, candidates, name)
    return result
