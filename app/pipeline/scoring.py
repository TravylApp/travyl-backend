"""Stage 3: Filter & Score — deterministic ranking of POIs for the scheduler."""

import logging

from app.schemas import AcquisitionResult, POI, TripExtraction

log = logging.getLogger(__name__)

# interest tag weights — higher = stronger signal for that interest
_INTEREST_BOOST = {
    "history": {"museum", "monument", "memorial", "castle", "ruins", "archaeological_site"},
    "art": {"museum", "gallery", "artwork"},
    "culture": {"place_of_worship", "theatre", "museum", "gallery", "marketplace"},
    "nature": {"park", "garden", "nature_reserve", "beach", "viewpoint"},
    "adventure": {"theme_park", "nature_reserve", "viewpoint"},
    "food": {"restaurant", "cafe", "fast_food", "marketplace"},
    "local_cuisine": {"restaurant", "cafe", "marketplace"},
    "street_food": {"fast_food", "marketplace"},
    "nightlife": {"bar", "pub", "nightclub"},
    "shopping": {"mall", "department_store", "marketplace"},
    "relaxation": {"park", "garden", "beach"},
    "family": {"zoo", "theme_park", "aquarium"},
    "music": {"nightclub", "theatre"},
    "architecture": {"monument", "castle", "place_of_worship"},
    "beach": {"beach"},
    "entertainment": {"theatre", "cinema"},
}

_BUDGET_PRICE_TARGET = {"budget": 1, "moderate": 2, "comfortable": 3, "luxury": 4}

PACE_RANGES = {"relaxed": (2, 4), "moderate": (3, 5), "packed": (4, 7)}


def _score_poi(poi: POI, interests: set[str], budget_level: str | None) -> float:
    score = 0.0

    # interest match (0-30) — subcategory matches user interests
    for interest in interests:
        if poi.subcategory in _INTEREST_BOOST.get(interest, set()):
            score += 15
            break
    # tag overlap with user interests
    tag_overlap = len(set(poi.tags) & interests)
    score += tag_overlap * 5

    # rating (0-25)
    if poi.rating:
        score += poi.rating * 5

    # popularity (0-15)
    if poi.review_count:
        if poi.review_count > 10000:
            score += 15
        elif poi.review_count > 1000:
            score += 10
        elif poi.review_count > 100:
            score += 5

    # data completeness (0-10)
    if poi.description:
        score += 3
    if poi.photo_url:
        score += 3
    if poi.website:
        score += 2
    if poi.opening_hours:
        score += 2

    # budget alignment (0-10)
    if poi.price_level is not None and budget_level:
        target = _BUDGET_PRICE_TARGET.get(budget_level, 2)
        diff = abs(poi.price_level - target)
        score += max(0, 10 - diff * 3)

    # SerpAPI results are pre-filtered for relevance by Google
    if poi.source == "serpapi":
        score += 5

    return score


def _filter_restaurants(
    pois: list[POI],
    avoid_cuisines: list[str],
) -> list[POI]:
    """Filter restaurant-category POIs by cuisine avoidance list."""
    if not avoid_cuisines:
        return pois

    avoid_lower = {c.lower() for c in avoid_cuisines}

    filtered = []
    for poi in pois:
        if poi.category != "restaurant":
            filtered.append(poi)
            continue

        cuisine = (poi.cuisine or "").lower()
        if cuisine and any(a in cuisine for a in avoid_lower):
            continue

        filtered.append(poi)

    return filtered


def score_and_filter(
    extraction: TripExtraction,
    acquisition: AcquisitionResult,
) -> list[tuple[POI, float]]:
    """Filter and score POIs. Returns sorted (poi, score) list, capped for the scheduler."""
    interests = set(extraction.interests)
    avoid_cats = set(extraction.constraints.avoid_categories)
    budget_level = extraction.budget_level
    pace = extraction.pace

    # filter
    pois = [
        p for p in acquisition.pois
        if p.category not in avoid_cats and p.subcategory not in avoid_cats
    ]
    pois = _filter_restaurants(pois, extraction.meals.avoid_cuisines)

    # score
    scored = [(poi, _score_poi(poi, interests, budget_level)) for poi in pois]
    scored.sort(key=lambda x: x[1], reverse=True)

    # cap: enough POIs for the scheduler to fill all days
    num_days = extraction.duration_days or 1
    max_per_day = PACE_RANGES.get(pace, (3, 5))[1]
    cap = min(max_per_day * num_days * 2, 50)  # 2x headroom for the solver

    # ensure category diversity — don't let attractions crowd out restaurants
    result: list[tuple[POI, float]] = []
    cat_counts: dict[str, int] = {}
    cat_caps = {"attraction": cap // 2, "restaurant": max(num_days * 2, 6), "nightlife": num_days}

    for poi, score in scored:
        cat = poi.category
        cat_limit = cat_caps.get(cat, cap // 4)
        if cat_counts.get(cat, 0) >= cat_limit:
            continue
        result.append((poi, score))
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if len(result) >= cap:
            break

    log.info(
        "Stage 3: %d → %d POIs (from %d raw), categories: %s",
        len(acquisition.pois), len(result), len(pois),
        {k: v for k, v in cat_counts.items()},
    )
    return result
