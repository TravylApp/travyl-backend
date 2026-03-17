"""Events search endpoint — thin wrapper around SerpAPI google_events."""

import logging
from datetime import datetime

import aiohttp
from fastapi import APIRouter, Query

from app.config import settings
from app.schemas import Event

router = APIRouter(prefix="/api/events", tags=["events"])
log = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


async def _serpapi_events(city: str, country: str, date_str: str) -> list[Event]:
    if not settings.serpapi_key:
        log.warning("SERPAPI_KEY not set, returning empty events")
        return []

    q = f"events in {city} {country}"
    if date_str:
        q += f" {date_str}"

    params = {
        "engine": "google_events",
        "q": q,
        "hl": "en",
        "api_key": settings.serpapi_key,
    }

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(_SERPAPI_URL, params=params) as resp:
                if resp.status == 429:
                    log.warning("SerpAPI rate limited")
                    return []
                resp.raise_for_status()
                data = await resp.json()
    except Exception as e:
        log.warning("SerpAPI events request failed: %s", e)
        return []

    events: list[Event] = []
    for i, item in enumerate(data.get("events_results", [])):
        title = item.get("title")
        if not title:
            continue

        date_info = item.get("date", {})
        when = date_info.get("when", "")
        start_date = date_info.get("start_date", "")

        address = item.get("address", [])
        venue_name = address[0] if address else None

        ticket_info = item.get("ticket_info", [])
        link = ticket_info[0].get("link") if ticket_info else item.get("link")

        events.append(Event(
            id=f"event_{i}_{abs(hash(title)) % 10000}",
            name=title,
            date=start_date or when,
            time=when if ":" in when else None,
            venue=venue_name,
            description=item.get("description"),
            price=None,
            category=None,
            photo_url=item.get("thumbnail"),
            link=link,
        ))

    log.info("SerpAPI events: %d results for %s %s", len(events), city, country)
    return events


def _build_date_str(start_date: str | None, end_date: str | None) -> str:
    if not start_date:
        return ""
    try:
        s = datetime.strptime(start_date, "%Y-%m-%d")
        date_str = s.strftime("%B %d")
        if end_date:
            e = datetime.strptime(end_date, "%Y-%m-%d")
            date_str += f" - {e.strftime('%B %d %Y')}"
        else:
            date_str += f" {s.year}"
        return date_str
    except ValueError:
        return ""


def _within_range(event_date: str, start_date: str, end_date: str) -> bool:
    """Check if an event's start_date falls within the requested range."""
    try:
        ed = datetime.strptime(event_date, "%Y-%m-%d")
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed_end = datetime.strptime(end_date, "%Y-%m-%d")
        return sd <= ed <= ed_end
    except ValueError:
        # unparseable date — keep the event rather than silently dropping
        return True


@router.get("/search", response_model=list[Event])
async def search_events(
    city: str = Query(..., description="City name"),
    country: str = Query(..., description="Country name"),
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
) -> list[Event]:
    date_str = _build_date_str(start_date, end_date)
    events = await _serpapi_events(city, country, date_str)

    if start_date and end_date:
        events = [e for e in events if _within_range(e.date, start_date, end_date)]

    return events
