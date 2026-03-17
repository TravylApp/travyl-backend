"""Stage 5: Response Assembly — package scheduler output into the final response."""

import logging

from timezonefinder import TimezoneFinder

from app.schemas import (
    AcquisitionResult,
    DayPlan,
    FlightOption,
    PlanResponse,
    TripExtraction,
)

log = logging.getLogger(__name__)

_tf = TimezoneFinder()
_DEFAULT_DAILY_START = 9 * 60   # 09:00
_DEFAULT_DAILY_END = 21 * 60    # 21:00


def _parse_time(s: str) -> int | None:
    if not s:
        return None
    s = s.strip().replace("\u202f", " ")
    upper = s.upper()

    is_pm = "PM" in upper
    is_am = "AM" in upper
    cleaned = upper.replace("PM", "").replace("AM", "").strip().rstrip(".")

    try:
        if ":" in cleaned:
            parts = cleaned.split(":")
            h, m = int(parts[0]), int(parts[1])
        else:
            h, m = int(cleaned), 0

        if is_pm and h != 12:
            h += 12
        elif is_am and h == 12:
            h = 0

        return h * 60 + m
    except (ValueError, IndexError):
        return None


def _outbound_timing_penalty(arrival_min: int, daily_start: int) -> float:
    if arrival_min <= daily_start:
        return 0.0
    if arrival_min <= 18 * 60:
        span = (18 * 60) - daily_start
        return min((arrival_min - daily_start) / span, 1.0) if span > 0 else 1.0
    if arrival_min <= 22 * 60:
        return 1.0 + (arrival_min - 18 * 60) / (4 * 60)
    return 3.0


def _return_timing_penalty(departure_min: int, daily_end: int) -> float:
    if departure_min >= daily_end:
        return 0.0
    if departure_min >= 12 * 60:
        span = daily_end - 12 * 60
        return min((daily_end - departure_min) / span, 1.0) if span > 0 else 1.0
    if departure_min >= 7 * 60:
        return 1.0 + (12 * 60 - departure_min) / (5 * 60)
    return 3.0


def _flight_sort_key(
    f: FlightOption,
    dest_airport: str,
    daily_start: int,
    daily_end: int,
    preferred_airlines: set[str],
) -> tuple[int, float]:
    price = f.price or float("inf")
    is_outbound = f.arrival_airport.upper() == dest_airport.upper() if dest_airport else True

    if is_outbound:
        arrival = _parse_time(f.arrival_time)
        penalty = _outbound_timing_penalty(arrival, daily_start) if arrival is not None else 1.0
    else:
        departure = _parse_time(f.departure_time)
        penalty = _return_timing_penalty(departure, daily_end) if departure is not None else 1.0

    preferred_rank = 0 if f.airline.lower() in preferred_airlines else 1
    return (preferred_rank, price + penalty * 100)


def assemble(
    extraction: TripExtraction,
    acquisition: AcquisitionResult,
    itinerary: list[DayPlan],
) -> PlanResponse:
    """Package all pipeline outputs into the final PlanResponse."""
    hotels = acquisition.hotels
    if extraction.budget_level and hotels:
        hotels = _filter_hotels_by_budget(hotels, extraction.budget_level, extraction.daily_estimate_usd)

    hotels = sorted(hotels, key=lambda h: (h.rating or 0), reverse=True)[:5]

    # filter + sort flights
    flights = _filter_and_sort_flights(acquisition.flights, extraction)[:5]

    # resolve destination timezone
    tz = None
    if extraction.destination.lat and extraction.destination.lng:
        tz = _tf.timezone_at(lat=extraction.destination.lat, lng=extraction.destination.lng)

    log.info(
        "Stage 5: assembled %d-day itinerary, %d hotels, %d flights",
        len(itinerary), len(hotels), len(flights),
    )

    # strip raw hotels/flights from data to avoid duplicating the filtered versions
    acq_data = acquisition.model_copy(update={"hotels": [], "flights": []})

    return PlanResponse(
        status="complete",
        extracted=extraction,
        itinerary=itinerary,
        hotels=hotels,
        flights=flights,
        destination_photo_url=acquisition.destination_photo_url,
        timezone=tz,
        data=acq_data,
    )


def _filter_hotels_by_budget(hotels, budget_level: str, daily_estimate_usd: int):
    if not daily_estimate_usd:
        return hotels

    target = daily_estimate_usd * 0.5
    lo = target * 0.3
    hi = target * 2.5

    in_range = [h for h in hotels if h.price_per_night and lo <= h.price_per_night <= hi]
    return in_range if len(in_range) >= 3 else hotels


def _filter_and_sort_flights(flights: list[FlightOption], extraction: TripExtraction) -> list[FlightOption]:
    prefs = extraction.flight_preferences
    result = list(flights)

    # remove avoided airlines
    if prefs.avoid_airlines:
        avoid = {a.lower() for a in prefs.avoid_airlines}
        result = [f for f in result if f.airline.lower() not in avoid]

    # filter by max stops
    if prefs.max_stops is not None:
        result = [f for f in result if f.stops <= prefs.max_stops]

    # timing + preference sort
    daily_start = _parse_time(extraction.constraints.daily_start_time) or _DEFAULT_DAILY_START
    daily_end = _parse_time(extraction.constraints.daily_end_time) or _DEFAULT_DAILY_END
    preferred = {a.lower() for a in prefs.preferred_airlines}

    dest_airport = ""
    if result:
        dest_airport = result[0].arrival_airport or ""

    result.sort(key=lambda f: _flight_sort_key(f, dest_airport, daily_start, daily_end, preferred))
    return result
