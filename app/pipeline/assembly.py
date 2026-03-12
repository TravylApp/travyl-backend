"""Stage 5: Response Assembly — package scheduler output into the final response."""

import logging

from app.schemas import (
    AcquisitionResult,
    DayPlan,
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
        hotels = _filter_hotels_by_budget(hotels, extraction.budget_level)

    # keep top 5 hotels and top 5 flights for the response
    hotels = sorted(hotels, key=lambda h: (h.rating or 0), reverse=True)[:5]
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


def _filter_hotels_by_budget(hotels, budget_level: str):
    """Soft-filter hotels to prefer ones matching the budget tier."""
    # price ranges per night (USD) — loose ranges, not hard cutoffs
    ranges = {
        "budget": (0, 100),
        "moderate": (50, 200),
        "comfortable": (100, 400),
        "luxury": (200, float("inf")),
    }
    lo, hi = ranges.get(budget_level, (0, float("inf")))

    in_range = [h for h in hotels if h.price_per_night and lo <= h.price_per_night <= hi]

    # if enough in-range hotels, use them; otherwise return all
    return in_range if len(in_range) >= 3 else hotels
