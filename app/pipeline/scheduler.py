"""Stage 4: CP-SAT Scheduler — unified time-based assignment, routing, and refinement."""

import logging
import math
import time as _time
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from app.schemas import DayPlan, DayWeather, Meals, POI, ScheduleSlot, TripExtraction

log = logging.getLogger(__name__)

_AVG_TRAVEL_MIN = 15
_CLUSTER_BONUS = 500
_STAGE4_BUDGET_S = 2.0
_ROUTING_RESERVE_S = 0.5

# pace controls time utilization + breathing room, not POI count
PACE_UTILIZATION = {"relaxed": 0.55, "moderate": 0.75, "packed": 0.90}
PACE_BUFFER = {"relaxed": 20, "moderate": 10, "packed": 5}

BREAKFAST_WINDOW = (450, 570)  # 7:30-9:30
LUNCH_WINDOW = (690, 810)     # 11:30-13:30
DINNER_WINDOW = (1080, 1200)  # 18:00-20:00
NIGHTLIFE_AFTER = 1200        # 20:00

_TRAVEL_MODE_MAP = {
    "walking": "walking",
    "public_transit": "transit",
    "rental_car": "driving",
    "rideshare": "driving",
    "cycling": "bicycling",
}

_OUTDOOR_SUBCATEGORIES = {
    "park", "garden", "nature_reserve", "beach", "viewpoint",
    "marketplace", "playground", "zoo", "theme_park",
}

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Helpers (kept from previous version)
# ---------------------------------------------------------------------------

def _parse_time(t: str | None, default: str) -> int:
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


def _to_12h(hhmm: str) -> str:
    """Convert "09:00" -> "9:00 AM", "13:30" -> "1:30 PM", "00:00" -> "12:00 AM"."""
    parts = hhmm.split(":")
    h, m = int(parts[0]), int(parts[1])
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def _is_outdoor(poi: POI) -> bool:
    return poi.subcategory in _OUTDOOR_SUBCATEGORIES or poi.category == "nature"


def _compute_zones(pois: list[POI], num_zones: int) -> list[int]:
    if not pois or num_zones <= 1:
        return [0] * len(pois)

    lats = [p.lat for p in pois]
    lngs = [p.lng for p in pois]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    cols = max(1, round(math.sqrt(num_zones)))
    rows = max(1, math.ceil(num_zones / cols))

    lat_step = (max_lat - min_lat) / rows if max_lat > min_lat else 1.0
    lng_step = (max_lng - min_lng) / cols if max_lng > min_lng else 1.0

    zones = []
    for p in pois:
        r = min(int((p.lat - min_lat) / lat_step), rows - 1)
        c = min(int((p.lng - min_lng) / lng_step), cols - 1)
        zones.append(r * cols + c)
    return zones


def _parse_ampm(s: str) -> int | None:
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


def _poi_open_on_day(poi: POI, date_str: str) -> tuple[int, int] | None:
    if not poi.opening_hours:
        return None

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_name = _DAY_NAMES[dt.weekday()].lower()

    hours_map = {k.lower(): v for k, v in poi.opening_hours.items()}
    hours_str = hours_map.get(day_name)

    if not hours_str:
        return (0, 0)

    hours_lower = hours_str.lower().strip()
    if hours_lower in ("closed", "off"):
        return (0, 0)

    if "24 hours" in hours_lower or hours_lower == "open":
        return (0, 1440)

    normalized = hours_str.replace("–", "-").replace("—", "-")
    ranges = [r.strip() for r in normalized.split(",")]

    earliest_open = 1440
    latest_close = 0

    for time_range in ranges:
        parts = time_range.split("-")
        if len(parts) != 2:
            return None

        start_raw, end_raw = parts[0].strip(), parts[1].strip()
        end_upper = end_raw.upper().replace("\u202f", " ")
        start_upper = start_raw.upper().replace("\u202f", " ")

        start_has_marker = "AM" in start_upper or "PM" in start_upper
        if not start_has_marker:
            if "PM" in end_upper:
                start_raw = start_raw + " PM"
            elif "AM" in end_upper:
                start_raw = start_raw + " AM"

        open_min = _parse_ampm(start_raw)
        close_min = _parse_ampm(end_raw)

        if open_min is None or close_min is None:
            return None

        if close_min <= open_min:
            close_min = 1440

        earliest_open = min(earliest_open, open_min)
        latest_close = max(latest_close, close_min)

    return (earliest_open, latest_close)


def _find_must_visit_indices(
    scored_pois: list[tuple[POI, float]],
    must_visit_names: list[str],
) -> list[int]:
    if not must_visit_names:
        return []

    names_lower = [n.lower() for n in must_visit_names]
    indices = []
    for i, (poi, _) in enumerate(scored_pois):
        poi_name_lower = poi.name.lower()
        for target in names_lower:
            if target in poi_name_lower or (len(poi_name_lower) >= 4 and poi_name_lower in target):
                indices.append(i)
                break
    return indices


def _travel(matrix: dict[str, dict[str, int]], a_id: str, b_id: str) -> int:
    return matrix.get(a_id, {}).get(b_id, _AVG_TRAVEL_MIN)


def _is_poi_open(poi: POI, date_str: str) -> bool:
    result = _poi_open_on_day(poi, date_str)
    if result is None:
        return True  # no data = assume open
    return result != (0, 0)


# ---------------------------------------------------------------------------
# Phase 1: Time-aware greedy assignment
# ---------------------------------------------------------------------------

def _greedy_time_assign(
    scored_pois: list[tuple[POI, float]],
    num_days: int,
    pace: str,
    day_bounds: list[tuple[int, int]],
    travel_matrix: dict[str, dict[str, int]],
    weather: list[DayWeather],
    dates: list[str],
    must_visit_indices: list[int],
    evening_pref: str | None,
    meals: Meals | None = None,
) -> list[list[int]]:
    """Assign POI indices to days using real travel times. POI count is an output of time math."""
    utilization = PACE_UTILIZATION.get(pace, 0.75)
    buffer = PACE_BUFFER.get(pace, 10)

    effective_budgets = [int((end - start) * utilization) for start, end in day_bounds]
    day_time_used = [0] * num_days
    assignments: list[list[int]] = [[] for _ in range(num_days)]
    assigned_set: set[int] = set()

    weather_by_date = {w.date: w for w in weather}

    def _day_cost(p_idx: int, d: int) -> int | None:
        """Compute time cost of adding POI p to day d. Returns None if it doesn't fit."""
        poi = scored_pois[p_idx][0]

        if not _is_poi_open(poi, dates[d]):
            return None

        travel = 0
        if assignments[d]:
            last_id = scored_pois[assignments[d][-1]][0].id
            travel = _travel(travel_matrix, last_id, poi.id)

        cost = travel + poi.visit_duration_min + buffer
        if day_time_used[d] + cost > effective_budgets[d]:
            return None

        return cost

    def _weather_penalty(p_idx: int, d: int) -> float:
        poi = scored_pois[p_idx][0]
        if not _is_outdoor(poi):
            return 0.0
        w = weather_by_date.get(dates[d])
        if w and w.precipitation_prob > 50:
            return w.precipitation_prob  # 0-100 penalty
        return 0.0

    def _geo_affinity(p_idx: int, d: int) -> float:
        """Average travel time to POIs already on this day. Lower = better clustering."""
        if not assignments[d]:
            return 0.0
        poi_id = scored_pois[p_idx][0].id
        total = sum(
            _travel(travel_matrix, poi_id, scored_pois[q][0].id)
            for q in assignments[d]
        )
        return total / len(assignments[d])

    def _assign(p_idx: int, d: int):
        cost = _day_cost(p_idx, d)
        day_time_used[d] += cost  # caller guarantees cost is not None
        assignments[d].append(p_idx)
        assigned_set.add(p_idx)

    def _best_day(p_idx: int, candidate_days: list[int] | None = None) -> int | None:
        """Find best day for a POI considering fit, weather, and geographic affinity."""
        days_to_check = candidate_days if candidate_days is not None else list(range(num_days))
        options = []
        for d in days_to_check:
            cost = _day_cost(p_idx, d)
            if cost is None:
                continue
            weather_pen = _weather_penalty(p_idx, d)
            geo = _geo_affinity(p_idx, d)
            # lower is better: weather penalty + geographic distance
            options.append((d, weather_pen + geo * 2))
        if not options:
            return None
        options.sort(key=lambda x: x[1])
        return options[0][0]

    # step 1: reserve meal slots — dynamic count per day based on time bounds
    skip_meals = meals is not None and meals.include_in_itinerary is False
    restaurant_indices = [
        i for i in range(len(scored_pois))
        if scored_pois[i][0].category == "restaurant"
    ]

    # compute how many meal windows each day can accommodate
    meal_target = [0] * num_days
    if not skip_meals:
        for d in range(num_days):
            ds, de = day_bounds[d]
            if ds < BREAKFAST_WINDOW[1] and de > BREAKFAST_WINDOW[0]:
                meal_target[d] += 1
            if ds < LUNCH_WINDOW[1] and de > LUNCH_WINDOW[0]:
                meal_target[d] += 1
            if ds < DINNER_WINDOW[1] and de > DINNER_WINDOW[0]:
                meal_target[d] += 1

    meals_per_day = [0] * num_days
    for r_idx in restaurant_indices:
        if r_idx in assigned_set:
            continue
        for d in sorted(range(num_days), key=lambda d: meals_per_day[d]):
            if meals_per_day[d] >= meal_target[d]:
                break
            cost = _day_cost(r_idx, d)
            if cost is not None:
                _assign(r_idx, d)
                meals_per_day[d] += 1
                break

    # step 2: place must-visit POIs
    for p_idx in must_visit_indices:
        if p_idx in assigned_set:
            continue
        d = _best_day(p_idx)
        if d is not None:
            _assign(p_idx, d)

    # step 3: fill remaining capacity by score
    remaining = [
        i for i in range(len(scored_pois))
        if i not in assigned_set
    ]

    # nightlife goes last (schedule after main fill)
    nightlife = [i for i in remaining if scored_pois[i][0].category == "nightlife"]
    non_nightlife = [i for i in remaining if scored_pois[i][0].category != "nightlife"]

    for p_idx in non_nightlife:
        d = _best_day(p_idx)
        if d is not None:
            _assign(p_idx, d)

    # nightlife prefers later in the day — assign to days with remaining evening time
    if evening_pref in ("nightlife", "dining", None):
        for p_idx in nightlife:
            d = _best_day(p_idx)
            if d is not None:
                _assign(p_idx, d)

    return assignments


# ---------------------------------------------------------------------------
# Phase 2: Per-day circuit routing with meal-window constraints
# ---------------------------------------------------------------------------

def _route_day(
    poi_indices: list[int],
    scored_pois: list[tuple[POI, float]],
    travel_matrix: dict[str, dict[str, int]],
    day_start: int,
    day_end: int,
    date_str: str,
    buffer: int,
) -> list[tuple[int, int]]:
    """Route POIs within a day using AddCircuit + time constraints.
    Returns [(poi_index, start_minutes), ...] in visit order."""
    if not poi_indices:
        return []

    if len(poi_indices) == 1:
        return [(poi_indices[0], day_start)]

    n = len(poi_indices)
    pois = [scored_pois[i][0] for i in poi_indices]

    # identify restaurants for meal windows
    restaurants = [(local_i, poi_indices[local_i]) for local_i in range(n) if pois[local_i].category == "restaurant"]
    breakfast_candidates = []
    lunch_candidates = []
    dinner_candidates = []
    for local_i, global_i in restaurants:
        if not breakfast_candidates and day_start < BREAKFAST_WINDOW[1]:
            breakfast_candidates.append(local_i)
        elif not lunch_candidates:
            lunch_candidates.append(local_i)
        elif not dinner_candidates:
            dinner_candidates.append(local_i)

    model = cp_model.CpModel()

    # circuit: depot(0) + n POI nodes (1..n)
    total_nodes = n + 1
    arcs = []
    arc_vars = {}

    for i in range(total_nodes):
        for j in range(total_nodes):
            if i == j:
                continue
            var = model.new_bool_var(f"a_{i}_{j}")
            arcs.append((i, j, var))
            arc_vars[i, j] = var

    model.add_circuit(arcs)

    # start-time variable for each POI
    start = [model.new_int_var(day_start, day_end, f"s_{i}") for i in range(n)]

    # sequencing: if arc i->j is active, start[j] >= start[i] + duration[i] + travel + buffer
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            travel = _travel(travel_matrix, pois[i].id, pois[j].id)
            min_gap = pois[i].visit_duration_min + travel + buffer
            arc_key = (i + 1, j + 1)
            if arc_key in arc_vars:
                model.add(start[j] >= start[i] + min_gap).only_enforce_if(arc_vars[arc_key])

    # depot -> first POI: start >= day_start (already in domain)
    # last POI -> depot: end time <= day_end
    for i in range(n):
        model.add(start[i] + pois[i].visit_duration_min <= day_end)

    # meal-window constraints
    for local_i in breakfast_candidates:
        model.add(start[local_i] >= BREAKFAST_WINDOW[0])
        model.add(start[local_i] <= BREAKFAST_WINDOW[1])

    for local_i in lunch_candidates:
        model.add(start[local_i] >= LUNCH_WINDOW[0])
        model.add(start[local_i] <= LUNCH_WINDOW[1])

    for local_i in dinner_candidates:
        model.add(start[local_i] >= DINNER_WINDOW[0])
        model.add(start[local_i] <= DINNER_WINDOW[1])

    # nightlife constraint
    for local_i in range(n):
        if pois[local_i].category == "nightlife":
            model.add(start[local_i] >= NIGHTLIFE_AFTER)

    # opening hours constraints
    for local_i in range(n):
        hours = _poi_open_on_day(pois[local_i], date_str)
        if hours and hours != (0, 0) and hours != (0, 1440):
            open_min, close_min = hours
            model.add(start[local_i] >= open_min)
            model.add(start[local_i] + pois[local_i].visit_duration_min <= close_min)

    # objective: minimize total travel time
    obj_terms = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            arc_key = (i + 1, j + 1)
            if arc_key in arc_vars:
                travel = _travel(travel_matrix, pois[i].id, pois[j].id)
                obj_terms.append(arc_vars[arc_key] * travel)
    if obj_terms:
        model.minimize(sum(obj_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 0.3
    solver.parameters.num_workers = 2
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # extract tour order from circuit
        result = []
        for i in range(n):
            result.append((poi_indices[i], solver.value(start[i])))
        result.sort(key=lambda x: x[1])
        return result

    # fallback: nearest-neighbor with time computation
    return _nearest_neighbor_with_times(poi_indices, scored_pois, travel_matrix, day_start, day_end, buffer)


def _nearest_neighbor_with_times(
    poi_indices: list[int],
    scored_pois: list[tuple[POI, float]],
    travel_matrix: dict[str, dict[str, int]],
    day_start: int,
    day_end: int,
    buffer: int,
) -> list[tuple[int, int]]:
    """Fallback: nearest-neighbor ordering with time slot computation."""
    if not poi_indices:
        return []

    remaining = list(poi_indices)
    ordered = [remaining.pop(0)]

    while remaining:
        last_id = scored_pois[ordered[-1]][0].id
        best_idx, best_time = 0, math.inf
        for i, p_idx in enumerate(remaining):
            t = _travel(travel_matrix, last_id, scored_pois[p_idx][0].id)
            if t < best_time:
                best_time = t
                best_idx = i
        ordered.append(remaining.pop(best_idx))

    # compute start times
    result = []
    current = day_start
    for i, p_idx in enumerate(ordered):
        poi = scored_pois[p_idx][0]
        if i > 0:
            prev_id = scored_pois[ordered[i - 1]][0].id
            current += _travel(travel_matrix, prev_id, poi.id) + buffer

        if current + poi.visit_duration_min > day_end:
            break

        result.append((p_idx, current))
        current += poi.visit_duration_min

    return result


# ---------------------------------------------------------------------------
# Phase 3: CP-SAT refinement (optional quality boost)
# ---------------------------------------------------------------------------

def _refine_cpsat(
    greedy_assignments: list[list[int]],
    scored_pois: list[tuple[POI, float]],
    num_days: int,
    day_bounds: list[tuple[int, int]],
    travel_matrix: dict[str, dict[str, int]],
    dates: list[str],
    must_visit_indices: list[int],
    buffer: int,
    time_budget: float,
) -> list[list[int]] | None:
    """Improve greedy by allowing POI swaps between days. Returns None if no improvement."""
    n = len(scored_pois)
    if n < 10 or time_budget < 0.5:
        return None

    # flatten greedy assignments to compute hint map
    hint_map: dict[int, int] = {}  # poi_index -> day
    all_assigned = set()
    for d, day_pois in enumerate(greedy_assignments):
        for p_idx in day_pois:
            hint_map[p_idx] = d
            all_assigned.add(p_idx)

    model = cp_model.CpModel()

    # x[p,d] = 1 if POI p assigned to day d
    x = {}
    for p in range(n):
        for d in range(num_days):
            x[p, d] = model.new_bool_var(f"x_{p}_{d}")

    # hints from greedy
    for p in range(n):
        for d in range(num_days):
            model.add_hint(x[p, d], 1 if hint_map.get(p) == d else 0)

    # each POI visited at most once
    for p in range(n):
        model.add(sum(x[p, d] for d in range(num_days)) <= 1)

    # at least 1 POI per day
    if n >= num_days:
        for d in range(num_days):
            model.add(sum(x[p, d] for p in range(n)) >= 1)

    # time budget per day (using real avg travel estimate per assigned POI)
    for d in range(num_days):
        start, end = day_bounds[d]
        daily_min = end - start
        model.add(
            sum(
                x[p, d] * (scored_pois[p][0].visit_duration_min + _AVG_TRAVEL_MIN + buffer)
                for p in range(n)
            )
            <= daily_min
        )

    # max 3 restaurants per day (breakfast + lunch + dinner)
    rest_indices = [p for p in range(n) if scored_pois[p][0].category == "restaurant"]
    if rest_indices:
        for d in range(num_days):
            model.add(sum(x[p, d] for p in rest_indices) <= 3)

    # must-visit forced
    for p in must_visit_indices:
        model.add(sum(x[p, d] for d in range(num_days)) == 1)

    # opening hours
    for p in range(n):
        poi = scored_pois[p][0]
        if not poi.opening_hours:
            continue
        for d in range(num_days):
            if not _is_poi_open(poi, dates[d]):
                model.add(x[p, d] == 0)

    # objective: maximize score + clustering bonus
    zones = _compute_zones([poi for poi, _ in scored_pois], num_days)
    zone_set = set(zones)
    objective_terms = []

    for p in range(n):
        score_int = int(scored_pois[p][1] * 100)
        for d in range(num_days):
            objective_terms.append(x[p, d] * score_int)

    # clustering bonus
    if len(zone_set) > 1:
        for d in range(num_days):
            for z in zone_set:
                pois_in_zone = [p for p in range(n) if zones[p] == z]
                if len(pois_in_zone) < 2:
                    continue
                count_var = model.new_int_var(0, len(pois_in_zone), f"zc_{z}_{d}")
                model.add(count_var == sum(x[p, d] for p in pois_in_zone))
                objective_terms.append(count_var * _CLUSTER_BONUS)

    model.maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_budget
    solver.parameters.num_workers = 8
    solver.parameters.relative_gap_limit = 0.05
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    # extract assignments
    result: list[list[int]] = [[] for _ in range(num_days)]
    for d in range(num_days):
        for p in range(n):
            if solver.value(x[p, d]):
                result[d].append(p)

    # only use refined result if it placed at least as many POIs
    greedy_total = sum(len(d) for d in greedy_assignments)
    refined_total = sum(len(d) for d in result)
    if refined_total < greedy_total:
        return None

    log.info(
        "Phase 3 refinement: %s, %d → %d POIs",
        solver.status_name(status), greedy_total, refined_total,
    )
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def schedule(
    scored_pois: list[tuple[POI, float]],
    extraction: TripExtraction,
    travel_matrix: dict[str, dict[str, int]],
    weather: list[DayWeather],
) -> list[DayPlan]:
    """Build a day-by-day itinerary from scored POIs using time-based scheduling."""
    t0 = _time.monotonic()
    num_days = extraction.duration_days or 1
    pace = extraction.pace or "moderate"
    buffer = PACE_BUFFER.get(pace, 10)

    day_start = _parse_time(extraction.constraints.daily_start_time, "09:00")
    day_end = _parse_time(extraction.constraints.daily_end_time, "21:00")

    # per-day time bounds (arrival/departure days shortened)
    day_bounds: list[tuple[int, int]] = [(day_start, day_end)] * num_days
    if extraction.constraints.arrival_time and num_days > 0:
        arrival = _parse_time(extraction.constraints.arrival_time, "09:00")
        day_bounds[0] = (max(arrival, day_start), day_bounds[0][1])
    if extraction.constraints.departure_time and num_days > 0:
        departure = _parse_time(extraction.constraints.departure_time, "21:00")
        day_bounds[-1] = (day_bounds[-1][0], min(departure, day_end))

    start_date = extraction.dates.start
    try:
        base_date = datetime.strptime(start_date, "%Y-%m-%d") if start_date else datetime.now()
    except ValueError:
        base_date = datetime.now()

    dates = [(base_date + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(num_days)]
    must_visit = _find_must_visit_indices(scored_pois, extraction.constraints.must_visit)

    # phase 1: time-aware greedy assignment
    assignments = _greedy_time_assign(
        scored_pois, num_days, pace, day_bounds,
        travel_matrix, weather, dates,
        must_visit, extraction.evening_preference,
        meals=extraction.meals,
    )

    # phase 3 (before routing): optional refinement
    elapsed = _time.monotonic() - t0
    remaining = max(0, _STAGE4_BUDGET_S - elapsed - _ROUTING_RESERVE_S)
    refined = _refine_cpsat(
        assignments, scored_pois, num_days, day_bounds,
        travel_matrix, dates, must_visit, buffer, remaining,
    )
    if refined is not None:
        assignments = refined

    # phase 2: per-day circuit routing with meal-window constraints
    weather_by_date = {w.date: w for w in weather}
    plans: list[DayPlan] = []

    for d in range(num_days):
        ds, de = day_bounds[d]

        routed = _route_day(
            assignments[d], scored_pois, travel_matrix,
            ds, de, dates[d], buffer,
        )

        # build schedule slots
        travelmode = _TRAVEL_MODE_MAP.get(extraction.travel_mode_preference, "walking")
        slots: list[ScheduleSlot] = []
        for i, (p_idx, start_min) in enumerate(routed):
            poi = scored_pois[p_idx][0]
            travel = 0
            dir_url = None
            if i > 0:
                prev_poi = scored_pois[routed[i - 1][0]][0]
                travel = _travel(travel_matrix, prev_poi.id, poi.id)
                dir_url = (
                    f"https://www.google.com/maps/dir/?api=1"
                    f"&origin={prev_poi.lat},{prev_poi.lng}"
                    f"&destination={poi.lat},{poi.lng}"
                    f"&travelmode={travelmode}"
                )

            start_hhmm = _minutes_to_hhmm(start_min)
            end_hhmm = _minutes_to_hhmm(start_min + poi.visit_duration_min)
            slots.append(ScheduleSlot(
                poi=poi,
                start_time=start_hhmm,
                end_time=end_hhmm,
                start_time_12h=_to_12h(start_hhmm),
                end_time_12h=_to_12h(end_hhmm),
                travel_from_prev_min=travel,
                directions_url=dir_url,
            ))

        plans.append(DayPlan(
            day=d + 1,
            date=dates[d],
            slots=slots,
            weather=weather_by_date.get(dates[d]),
        ))

    total_scheduled = sum(len(p.slots) for p in plans)
    elapsed_ms = int((_time.monotonic() - t0) * 1000)
    log.info("Stage 4: scheduled %d POIs across %d days in %dms", total_scheduled, num_days, elapsed_ms)
    return plans
