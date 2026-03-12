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
        hotels = _filter_hotels_by_budget(hotels, extraction.budget_level, extraction.daily_estimate_usd)

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
