"""Stage 4: CP-SAT Scheduler — assign scored POIs to days, then order within each day."""

import logging
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from app.pipeline.scoring import PACE_LIMITS
from app.schemas import DayPlan, DayWeather, POI, ScheduleSlot, TripExtraction

log = logging.getLogger(__name__)

# average travel time estimate (min) used in the assignment model
# actual travel times are applied during ordering
_AVG_TRAVEL_MIN = 15


def _parse_time(t: str | None, default: str) -> int:
    """Parse "HH:MM" to minutes since midnight."""
    s = t or default
    try:
        parts = s.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        parts = default.split(":")
        return int(parts[0]) * 60 + int(parts[1])


def _minutes_to_hhmm(mins: int) -> str:
    h, m = divmod(mins, 60)
    return f"{h:02d}:{m:02d}"


def _nearest_neighbor_order(
    pois: list[POI],
    travel_matrix: dict[str, dict[str, int]],
) -> list[POI]:
    """Order POIs within a day using nearest-neighbor heuristic."""
    if len(pois) <= 1:
        return pois

    remaining = list(pois)
    ordered = [remaining.pop(0)]

    while remaining:
        last = ordered[-1]
        best_idx, best_time = 0, 999999
        for i, candidate in enumerate(remaining):
            t = travel_matrix.get(last.id, {}).get(candidate.id, 30)
            if t < best_time:
                best_time = t
                best_idx = i
        ordered.append(remaining.pop(best_idx))

    return ordered


def _slot_restaurants(ordered: list[POI], day_start: int) -> list[POI]:
    """Move restaurants to lunch (12:00-13:30) and dinner (18:30-20:00) slots if possible."""
    restaurants = [p for p in ordered if p.category == "restaurant"]
    non_restaurants = [p for p in ordered if p.category != "restaurant"]

    if not restaurants:
        return ordered

    # estimate rough slot times for non-restaurants
    result: list[POI] = []
    current_min = day_start
    lunch_placed = False
    dinner_placed = False

    for poi in non_restaurants:
        # insert lunch restaurant before afternoon activities
        if not lunch_placed and restaurants and current_min >= 720:  # noon
            placed = restaurants.pop(0)
            result.append(placed)
            lunch_placed = True
            current_min += placed.visit_duration_min + _AVG_TRAVEL_MIN

        # insert dinner restaurant in the evening
        if not dinner_placed and restaurants and current_min >= 1110:  # 18:30
            placed = restaurants.pop(0)
            result.append(placed)
            dinner_placed = True
            current_min += placed.visit_duration_min + _AVG_TRAVEL_MIN

        result.append(poi)
        current_min += poi.visit_duration_min + _AVG_TRAVEL_MIN

    # append any remaining restaurants at the end
    result.extend(restaurants)
    return result


def _assign_pois_cpsat(
    scored_pois: list[tuple[POI, float]],
    num_days: int,
    max_per_day: int,
    daily_minutes: int,
) -> list[list[tuple[POI, float]]] | None:
    """Use CP-SAT to assign POIs to days. Returns list of per-day (poi, score) lists."""
    n = len(scored_pois)
    if n == 0:
        return [[] for _ in range(num_days)]

    model = cp_model.CpModel()

    # x[p][d] = 1 if POI p is visited on day d
    x = {}
    for p in range(n):
        for d in range(num_days):
            x[p, d] = model.new_bool_var(f"x_{p}_{d}")

    # each POI visited at most once
    for p in range(n):
        model.add(sum(x[p, d] for d in range(num_days)) <= 1)

    # max POIs per day
    for d in range(num_days):
        model.add(sum(x[p, d] for p in range(n)) <= max_per_day)

    # time budget per day (visit + avg travel per POI)
    for d in range(num_days):
        model.add(
            sum(
                x[p, d] * (scored_pois[p][0].visit_duration_min + _AVG_TRAVEL_MIN)
                for p in range(n)
            )
            <= daily_minutes
        )

    # distribute restaurants across days: max 2 per day
    rest_indices = [p for p in range(n) if scored_pois[p][0].category == "restaurant"]
    if rest_indices:
        for d in range(num_days):
            model.add(sum(x[p, d] for p in rest_indices) <= 2)

    # balance: at least 1 POI per day (if we have enough)
    if n >= num_days:
        for d in range(num_days):
            model.add(sum(x[p, d] for p in range(n)) >= 1)

    # objective: maximize total score (scaled to int for CP-SAT)
    model.maximize(
        sum(
            x[p, d] * int(scored_pois[p][1] * 100)
            for p in range(n)
            for d in range(num_days)
        )
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    days: list[list[tuple[POI, float]]] = [[] for _ in range(num_days)]
    for d in range(num_days):
        for p in range(n):
            if solver.value(x[p, d]):
                days[d].append(scored_pois[p])

    return days


def _greedy_assign(
    scored_pois: list[tuple[POI, float]],
    num_days: int,
    max_per_day: int,
    daily_minutes: int,
) -> list[list[tuple[POI, float]]]:
    """Fallback: round-robin greedy assignment."""
    days: list[list[tuple[POI, float]]] = [[] for _ in range(num_days)]
    day_times = [0] * num_days
    day_counts = [0] * num_days

    for poi, score in scored_pois:
        # find day with most remaining time
        best_d = min(range(num_days), key=lambda d: (day_counts[d], day_times[d]))
        cost = poi.visit_duration_min + _AVG_TRAVEL_MIN

        if day_counts[best_d] >= max_per_day or day_times[best_d] + cost > daily_minutes:
            continue

        days[best_d].append((poi, score))
        day_times[best_d] += cost
        day_counts[best_d] += 1

    return days


def schedule(
    scored_pois: list[tuple[POI, float]],
    extraction: TripExtraction,
    travel_matrix: dict[str, dict[str, int]],
    weather: list[DayWeather],
) -> list[DayPlan]:
    """Build a day-by-day itinerary from scored POIs."""
    num_days = extraction.duration_days or 1
    pace = extraction.pace
    max_per_day = PACE_LIMITS.get(pace, 6)

    day_start = _parse_time(extraction.constraints.daily_start_time, "09:00")
    day_end = _parse_time(extraction.constraints.daily_end_time, "21:00")
    daily_minutes = day_end - day_start

    # phase 1: assign POIs to days (CP-SAT or greedy fallback)
    assigned = _assign_pois_cpsat(scored_pois, num_days, max_per_day, daily_minutes)
    if assigned is None:
        log.info("CP-SAT infeasible, falling back to greedy")
        assigned = _greedy_assign(scored_pois, num_days, max_per_day, daily_minutes)

    # phase 2: order within each day + compute times
    start_date = extraction.dates.start
    try:
        base_date = datetime.strptime(start_date, "%Y-%m-%d") if start_date else datetime.now()
    except ValueError:
        base_date = datetime.now()

    weather_by_date = {w.date: w for w in weather}
    plans: list[DayPlan] = []

    for d in range(num_days):
        date = (base_date + timedelta(days=d)).strftime("%Y-%m-%d")
        day_pois = [poi for poi, _ in assigned[d]]

        # order by nearest neighbor, then slot restaurants at meal times
        ordered = _nearest_neighbor_order(day_pois, travel_matrix)
        ordered = _slot_restaurants(ordered, day_start)

        # compute time slots — stop when we exceed the day's end time
        slots: list[ScheduleSlot] = []
        current = day_start

        for i, poi in enumerate(ordered):
            travel = 0
            if i > 0:
                prev = ordered[i - 1]
                travel = travel_matrix.get(prev.id, {}).get(poi.id, _AVG_TRAVEL_MIN)
                current += travel

            # don't schedule past end of day
            if current + poi.visit_duration_min > day_end:
                break

            start_time = _minutes_to_hhmm(current)
            current += poi.visit_duration_min
            end_time = _minutes_to_hhmm(current)

            slots.append(ScheduleSlot(
                poi=poi,
                start_time=start_time,
                end_time=end_time,
                travel_from_prev_min=travel,
            ))

        plans.append(DayPlan(
            day=d + 1,
            date=date,
            slots=slots,
            weather=weather_by_date.get(date),
        ))

    total_scheduled = sum(len(p.slots) for p in plans)
    log.info("Stage 4: scheduled %d POIs across %d days", total_scheduled, num_days)
    return plans
