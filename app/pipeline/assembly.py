"""Stage 5: Response Assembly — package scheduler output into the final response."""

import logging
import math

from app.schemas import (
    AcquisitionResult,
    DayPlan,
    HotelOption,
    PlanResponse,
    TripExtraction,
)

log = logging.getLogger(__name__)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def assemble(
    extraction: TripExtraction,
    acquisition: AcquisitionResult,
    itinerary: list[DayPlan],
) -> PlanResponse:
    """Package all pipeline outputs into the final PlanResponse."""
    # filter hotels by budget if we have price data
    hotels = acquisition.hotels
    if extraction.budget_level and hotels:
        hotels = _filter_hotels_by_budget(hotels, extraction.budget_level, extraction.daily_estimate_usd)

    prefs = extraction.hotel_preferences

    # filter by minimum star rating
    if prefs.min_stars is not None and hotels:
        filtered = [h for h in hotels if h.stars is not None and h.stars >= prefs.min_stars]
        if filtered:
            hotels = filtered

    # filter by required amenities (case-insensitive substring match)
    if prefs.required_amenities and hotels:
        filtered = [h for h in hotels if _has_all_amenities(h, prefs.required_amenities)]
        if filtered:
            hotels = filtered

    # collect POI coordinates for proximity scoring
    poi_coords = []
    for day in itinerary:
        for slot in day.slots:
            poi_coords.append((slot.poi.lat, slot.poi.lng))

    # sort by rating desc, then proximity asc
    hotels = sorted(hotels, key=lambda h: _hotel_sort_key(h, poi_coords), reverse=True)[:5]

    flights = sorted(
        acquisition.flights,
        key=lambda f: (f.price or float("inf"), f.duration_min),
    )[:5]

    log.info(
        "Stage 5: assembled %d-day itinerary, %d hotels, %d flights",
        len(itinerary), len(hotels), len(flights),
    )

    return PlanResponse(
        status="complete",
        extracted=extraction,
        itinerary=itinerary,
        hotels=hotels,
        flights=flights,
        destination_photo_url=acquisition.destination_photo_url,
        data=acquisition,
    )


def _has_all_amenities(hotel: HotelOption, required: list[str]) -> bool:
    hotel_amenities_lower = [a.lower() for a in hotel.amenities]
    for req in required:
        req_lower = req.lower()
        if not any(req_lower in a for a in hotel_amenities_lower):
            return False
    return True


def _hotel_sort_key(hotel: HotelOption, poi_coords: list[tuple[float, float]]) -> tuple[float, float]:
    """Return (rating, -avg_distance) so higher rating and lower distance sort first."""
    rating = hotel.rating or 0.0
    if hotel.lat is not None and hotel.lng is not None and poi_coords:
        avg_dist = sum(_haversine_km(hotel.lat, hotel.lng, lat, lng) for lat, lng in poi_coords) / len(poi_coords)
    else:
        avg_dist = float("inf")
    # negative distance so that closer hotels sort higher with reverse=True
    return (rating, -avg_dist)


def _filter_hotels_by_budget(hotels, budget_level: str, daily_estimate_usd: int):
    """Soft-filter hotels to prefer ones near the daily budget estimate."""
    if not daily_estimate_usd:
        return hotels

    # hotel should cost roughly 40-60% of daily budget
    target = daily_estimate_usd * 0.5
    lo = target * 0.3
    hi = target * 2.5

    in_range = [h for h in hotels if h.price_per_night and lo <= h.price_per_night <= hi]
    return in_range if len(in_range) >= 3 else hotels
