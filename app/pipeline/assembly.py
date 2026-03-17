"""Stage 5: Response Assembly — package scheduler output into the final response."""

import logging

from app.schemas import (
    AcquisitionResult,
    DayPlan,
    FlightOption,
    PlanResponse,
    TripExtraction,
)

log = logging.getLogger(__name__)


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

    # keep top 5 hotels and top 5 flights for the response
    hotels = sorted(hotels, key=lambda h: (h.rating or 0), reverse=True)[:5]
    flights = _filter_flights(acquisition.flights, extraction)[:5]

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


def _filter_flights(flights: list[FlightOption], extraction: TripExtraction) -> list[FlightOption]:
    prefs = extraction.flight_preferences
    result = list(flights)

    # remove avoided airlines
    if prefs.avoid_airlines:
        avoid = {a.lower() for a in prefs.avoid_airlines}
        result = [f for f in result if f.airline.lower() not in avoid]

    # filter by max stops
    if prefs.max_stops is not None:
        result = [f for f in result if f.stops <= prefs.max_stops]

    # sort: preferred airlines first, then by price
    preferred = {a.lower() for a in prefs.preferred_airlines}
    result.sort(key=lambda f: (
        0 if f.airline.lower() in preferred else 1,
        f.price or float("inf"),
        f.duration_min,
    ))

    return result
