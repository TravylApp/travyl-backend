"""Flight search endpoint — standalone search outside the trip pipeline."""

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.schemas import FlightOption

router = APIRouter(prefix="/api/flights", tags=["flights"])
log = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"
_UA = "TravylApp/1.0 (https://gotravyl.com; dev@gotravyl.com)"
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_HEADERS = {"User-Agent": _UA, "Accept-Encoding": "gzip, deflate"}

_TRAVEL_CLASS_MAP = {
    "economy": 1,
    "premium_economy": 2,
    "business": 3,
    "first": 4,
}


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
        log.warning("SerpAPI request failed (%s): %s", params.get("engine"), e)
        return None


async def _resolve_iata(session: aiohttp.ClientSession, city: str, country: str) -> str | None:
    """Resolve a city name to its primary IATA airport code via SerpAPI autocomplete."""
    data = await _serpapi(session, {
        "engine": "google_flights_autocomplete",
        "q": f"{city} {country}",
    })
    if not data:
        return None

    for suggestion in data.get("suggestions", []):
        for airport in suggestion.get("airports", []):
            iata = airport.get("id")
            if iata and len(iata) == 3:
                return iata
    return None


def _sort_key(flight: FlightOption, sort_by: str):
    if sort_by == "duration":
        return flight.duration_min
    if sort_by == "departure_time":
        return flight.departure_time
    # default: price
    return flight.price if flight.price is not None else float("inf")


@router.get("/search", response_model=list[FlightOption])
async def search_flights(
    origin: str = Query(..., description="Origin city name"),
    origin_country: str = Query(..., description="Origin country"),
    destination: str = Query(..., description="Destination city name"),
    destination_country: str = Query(..., description="Destination country"),
    departure_date: str = Query(..., description="Departure date (YYYY-MM-DD)"),
    return_date: str | None = Query(None, description="Return date for round trip"),
    passengers: int = Query(1, ge=1, le=9),
    travel_class: str | None = Query(None, description="economy, premium_economy, business, first"),
    max_stops: int | None = Query(None, ge=0),
    sort_by: str = Query("price", description="price, duration, or departure_time"),
):
    if not settings.serpapi_key:
        raise HTTPException(status_code=503, detail="Flight search unavailable")

    async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
        dep_iata, arr_iata = await asyncio.gather(
            _resolve_iata(session, origin, origin_country),
            _resolve_iata(session, destination, destination_country),
        )

        if not dep_iata:
            raise HTTPException(status_code=400, detail=f"Could not resolve airport for {origin}, {origin_country}")
        if not arr_iata:
            raise HTTPException(status_code=400, detail=f"Could not resolve airport for {destination}, {destination_country}")

        params: dict = {
            "engine": "google_flights",
            "departure_id": dep_iata,
            "arrival_id": arr_iata,
            "outbound_date": departure_date,
            "adults": str(passengers),
            "currency": "USD",
            "hl": "en",
            "type": "2",  # one-way
        }
        if return_date:
            params["return_date"] = return_date
            params["type"] = "1"  # round trip

        if travel_class:
            cls_int = _TRAVEL_CLASS_MAP.get(travel_class.lower())
            if cls_int:
                params["travel_class"] = str(cls_int)

        data = await _serpapi(session, params)
        if not data:
            raise HTTPException(status_code=502, detail="Flight search request failed")

    flights: list[FlightOption] = []
    for result in data.get("best_flights", []) + data.get("other_flights", []):
        legs = result.get("flights", [])
        if not legs:
            continue

        first_leg = legs[0]
        last_leg = legs[-1]
        total_duration = result.get("total_duration", 0)
        price = result.get("price")
        stops = len(legs) - 1

        if max_stops is not None and stops > max_stops:
            continue

        layovers = []
        for leg in legs[1:]:
            dep_airport = leg.get("departure_airport", {})
            layovers.append(dep_airport.get("id", dep_airport.get("name", "")))

        flights.append(FlightOption(
            airline=first_leg.get("airline", ""),
            flight_number=first_leg.get("flight_number"),
            departure_airport=first_leg.get("departure_airport", {}).get("id", dep_iata),
            arrival_airport=last_leg.get("arrival_airport", {}).get("id", arr_iata),
            departure_time=first_leg.get("departure_airport", {}).get("time", ""),
            arrival_time=last_leg.get("arrival_airport", {}).get("time", ""),
            duration_min=total_duration,
            price=price,
            currency="USD",
            stops=stops,
            layovers=layovers,
        ))

    flights.sort(key=lambda f: _sort_key(f, sort_by))
    return flights
