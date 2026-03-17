"""Stage 2: Data Acquisition — parallel fan-out to external APIs."""

import asyncio
import hashlib
import logging
import pickle
from datetime import datetime
from math import atan2, cos, radians, sin, sqrt
from urllib.parse import quote_plus

import aiohttp
import redis.asyncio as aioredis

from app.config import settings
from app.schemas import (
    AcquisitionResult,
    DayWeather,
    Event,
    FlightOption,
    HotelOption,
    POI,
    TripExtraction,
)

log = logging.getLogger(__name__)

_UA = "TravylApp/1.0 (https://gotravyl.com; dev@gotravyl.com)"
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_HEADERS = {"User-Agent": _UA, "Accept-Encoding": "gzip, deflate"}

_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]
_SERPAPI_URL = "https://serpapi.com/search"
_OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
_OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
_ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
_UNSPLASH_URL = "https://api.unsplash.com/search/photos"


_redis: aioredis.Redis | None = None
_redis_failed = False
_KEY_PREFIX = "travyl:"


def _get_redis() -> aioredis.Redis | None:
    global _redis, _redis_failed
    if _redis_failed:
        return None
    if _redis is None:
        try:
            _redis = aioredis.from_url(
                settings.redis_url, decode_responses=False, socket_timeout=2,
            )
        except Exception:
            log.warning("Redis connection failed, caching disabled")
            _redis_failed = True
            return None
    return _redis


async def _cget(key: str) -> object | None:
    r = _get_redis()
    if not r:
        return None
    try:
        data = await r.get(f"{_KEY_PREFIX}{key}")
        return pickle.loads(data) if data else None
    except Exception:
        return None


async def _cset(key: str, val: object, ttl: int) -> None:
    r = _get_redis()
    if not r:
        return
    try:
        await r.setex(f"{_KEY_PREFIX}{key}", ttl, pickle.dumps(val))
    except Exception:
        pass


_TTL_OVERPASS = 86_400
_TTL_WIKIDATA = 2_592_000
_TTL_SERP = 604_800
_TTL_SERP_EVENTS = 86_400
_TTL_SERP_HOTELS = 21_600
_TTL_SERP_FLIGHTS = 21_600
_TTL_WEATHER = 21_600
_TTL_IATA = 2_592_000


_OSM_CATEGORIES: dict[tuple[str, str], tuple[str, str]] = {
    ("tourism", "museum"): ("attraction", "museum"),
    ("tourism", "attraction"): ("attraction", "attraction"),
    ("tourism", "viewpoint"): ("attraction", "viewpoint"),
    ("tourism", "gallery"): ("attraction", "gallery"),
    ("tourism", "zoo"): ("attraction", "zoo"),
    ("tourism", "theme_park"): ("attraction", "theme_park"),
    ("tourism", "aquarium"): ("attraction", "aquarium"),
    ("tourism", "artwork"): ("attraction", "artwork"),
    ("historic", "monument"): ("attraction", "monument"),
    ("historic", "memorial"): ("attraction", "memorial"),
    ("historic", "castle"): ("attraction", "castle"),
    ("historic", "ruins"): ("attraction", "ruins"),
    ("historic", "archaeological_site"): ("attraction", "archaeological_site"),
    ("amenity", "restaurant"): ("restaurant", "restaurant"),
    ("amenity", "cafe"): ("restaurant", "cafe"),
    ("amenity", "fast_food"): ("restaurant", "fast_food"),
    ("amenity", "bar"): ("nightlife", "bar"),
    ("amenity", "pub"): ("nightlife", "pub"),
    ("amenity", "nightclub"): ("nightlife", "nightclub"),
    ("leisure", "park"): ("nature", "park"),
    ("leisure", "garden"): ("nature", "garden"),
    ("leisure", "nature_reserve"): ("nature", "nature_reserve"),
    ("natural", "beach"): ("nature", "beach"),
    ("amenity", "place_of_worship"): ("attraction", "place_of_worship"),
    ("amenity", "theatre"): ("entertainment", "theatre"),
    ("amenity", "cinema"): ("entertainment", "cinema"),
    ("shop", "mall"): ("shopping", "mall"),
    ("shop", "department_store"): ("shopping", "department_store"),
    ("amenity", "marketplace"): ("shopping", "marketplace"),
}

_SUBCATEGORY_INTEREST_TAGS: dict[str, list[str]] = {
    "museum": ["history", "culture", "art"],
    "gallery": ["art", "culture"],
    "monument": ["history", "architecture"],
    "memorial": ["history"],
    "castle": ["history", "architecture"],
    "ruins": ["history", "ancient_ruins"],
    "archaeological_site": ["history", "ancient_ruins"],
    "attraction": ["culture"],
    "viewpoint": ["nature", "adventure"],
    "zoo": ["nature", "family"],
    "theme_park": ["adventure", "family"],
    "aquarium": ["nature", "family"],
    "artwork": ["art", "culture"],
    "restaurant": ["food", "local_cuisine"],
    "cafe": ["food"],
    "fast_food": ["food", "street_food"],
    "bar": ["nightlife"],
    "pub": ["nightlife"],
    "nightclub": ["nightlife", "music"],
    "park": ["nature", "relaxation"],
    "garden": ["nature", "relaxation"],
    "nature_reserve": ["nature", "adventure"],
    "beach": ["beach", "relaxation", "nature"],
    "place_of_worship": ["religion", "architecture", "culture"],
    "theatre": ["culture", "entertainment", "music"],
    "cinema": ["entertainment"],
    "mall": ["shopping"],
    "department_store": ["shopping"],
    "marketplace": ["shopping", "food", "street_food"],
}

_VISIT_DURATION: dict[str, int] = {
    "museum": 90, "gallery": 60, "monument": 20, "memorial": 15,
    "castle": 90, "ruins": 60, "archaeological_site": 90,
    "attraction": 60, "viewpoint": 20, "zoo": 180, "theme_park": 240,
    "aquarium": 90, "artwork": 10,
    "restaurant": 75, "cafe": 40, "fast_food": 25,
    "bar": 60, "pub": 75, "nightclub": 120,
    "park": 45, "garden": 45, "nature_reserve": 120, "beach": 120,
    "place_of_worship": 30,
    "theatre": 150, "cinema": 150,
    "mall": 90, "department_store": 60, "marketplace": 60,
}

_WMO_CONDITIONS: dict[int, str] = {
    0: "clear", 1: "mostly_clear", 2: "partly_cloudy", 3: "overcast",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle",
    61: "rain", 63: "rain", 65: "heavy_rain",
    71: "snow", 73: "snow", 75: "heavy_snow",
    80: "rain_showers", 81: "rain_showers", 82: "heavy_rain",
    85: "snow_showers", 86: "snow_showers",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}

_ORS_PROFILES = {
    "walking": "foot-walking",
    "cycling": "cycling-regular",
    "public_transit": "driving-car",
    "rental_car": "driving-car",
    "rideshare": "driving-car",
    None: "driving-car",
}

_MAX_POIS = {"attraction": 100, "restaurant": 60, "nightlife": 30,
             "nature": 30, "shopping": 20, "entertainment": 20}


async def _noop(default):
    return default


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _travel_min_haversine(lat1: float, lng1: float, lat2: float, lng2: float, mode: str = "driving") -> int:
    km = _haversine_km(lat1, lng1, lat2, lng2) * 1.4
    speed = 5.0 if mode == "walking" else 25.0
    return max(1, round(km / speed * 60))


def _commons_thumb_url(filename: str, width: int = 400) -> str:
    filename = filename.replace(" ", "_")
    md5 = hashlib.md5(filename.encode()).hexdigest()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "svg":
        return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{md5[0]}/{md5[:2]}/{filename}/{width}px-{filename}.png"
    return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{md5[0]}/{md5[:2]}/{filename}/{width}px-{filename}"


def _completeness(poi: POI) -> int:
    score = 0
    if poi.description:
        score += 5
    if poi.photo_url:
        score += 3
    if poi.rating is not None:
        score += 3
    if poi.website:
        score += 1
    if poi.opening_hours:
        score += 1
    if poi.review_count and poi.review_count > 100:
        score += 2
    return score


def _classify_osm(tags: dict) -> tuple[str, str]:
    for key, val in tags.items():
        mapped = _OSM_CATEGORIES.get((key, val))
        if mapped:
            return mapped
    return ("other", "other")


def _parse_price_level(price_str: str | None) -> int | None:
    if not price_str:
        return None
    count = price_str.count("$")
    return min(count, 4) if count else None


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# split into batches to avoid Overpass rate limits on large union queries
_OVERPASS_BATCHES = [
    [  # attractions
        '["tourism"="museum"]', '["tourism"="attraction"]', '["tourism"="viewpoint"]',
        '["tourism"="gallery"]', '["tourism"="zoo"]', '["tourism"="aquarium"]',
        '["historic"="monument"]', '["historic"="memorial"]', '["historic"="castle"]',
        '["historic"="ruins"]', '["historic"="archaeological_site"]',
    ],
    [  # food + nightlife
        '["amenity"="restaurant"]', '["amenity"="cafe"]',
        '["amenity"="bar"]', '["amenity"="pub"]', '["amenity"="nightclub"]',
    ],
    [  # nature + culture + shopping
        '["leisure"="park"]', '["leisure"="garden"]', '["natural"="beach"]',
        '["amenity"="place_of_worship"]', '["amenity"="theatre"]',
        '["amenity"="marketplace"]', '["shop"="mall"]',
    ],
]


def _build_overpass_query(lat: float, lng: float, tags: list[str], radius: int = 8000) -> str:
    parts = []
    for tag in tags:
        parts.append(f'  node{tag}["name"](around:{radius},{lat},{lng});')
        parts.append(f'  way{tag}["name"](around:{radius},{lat},{lng});')
    union = "\n".join(parts)
    return f"[out:json][timeout:30];\n(\n{union}\n);\nout center tags;"


def _parse_overpass_element(el: dict) -> tuple[POI, dict] | None:
    tags = el.get("tags", {})
    name = tags.get("name") or tags.get("name:en")
    if not name:
        return None

    if el["type"] == "node":
        lat, lng = el.get("lat", 0), el.get("lon", 0)
    elif "center" in el:
        lat, lng = el["center"].get("lat", 0), el["center"].get("lon", 0)
    else:
        return None

    if lat == 0 and lng == 0:
        return None

    category, subcategory = _classify_osm(tags)
    if category == "other":
        return None

    osm_id = f"osm_{el['type']}_{el['id']}"
    interest_tags = _SUBCATEGORY_INTEREST_TAGS.get(subcategory, [])
    duration = _VISIT_DURATION.get(subcategory, 60)

    # bump duration for notable POIs (have wikidata entry)
    if "wikidata" in tags and subcategory in ("museum", "castle", "ruins", "place_of_worship"):
        duration = max(duration, 90)

    poi = POI(
        id=osm_id,
        name=name,
        lat=lat,
        lng=lng,
        category=category,
        subcategory=subcategory,
        website=tags.get("website"),
        visit_duration_min=duration,
        cuisine=tags.get("cuisine"),
        tags=interest_tags,
        source="osm",
    )
    return poi, tags


async def _run_overpass_batch(session: aiohttp.ClientSession, query: str) -> list[dict]:
    # try primary, fall back to mirror on failure/rate-limit
    for url in _OVERPASS_URLS:
        try:
            async with session.post(
                url, data={"data": query}, timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 429:
                    log.info("Overpass 429 on %s, trying mirror", url.split("//")[1].split("/")[0])
                    continue
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return data.get("elements", [])
        except Exception as e:
            log.warning("Overpass batch failed on %s: %s", url.split("//")[1].split("/")[0], e)
            continue
    return []


async def _fetch_overpass(session: aiohttp.ClientSession, extraction: TripExtraction) -> tuple[list[POI], dict[str, dict]]:
    lat, lng = extraction.destination.lat, extraction.destination.lng
    cache_key = f"overpass:{lat:.3f}:{lng:.3f}"
    cached = await _cget(cache_key)
    if cached:
        return cached

    # Overpass fair-use: max 2 concurrent requests, then a third after a delay
    queries = [_build_overpass_query(lat, lng, tags) for tags in _OVERPASS_BATCHES]
    batch_1_2 = await asyncio.gather(
        _run_overpass_batch(session, queries[0]),
        _run_overpass_batch(session, queries[1]),
        return_exceptions=True,
    )
    await asyncio.sleep(1)
    batch_3 = await _run_overpass_batch(session, queries[2])

    all_elements: list[dict] = []
    for result in [*batch_1_2, batch_3]:
        if isinstance(result, Exception):
            log.warning("Overpass batch error: %s", result)
            continue
        all_elements.extend(result)

    seen_ids: set[str] = set()
    pois: list[POI] = []
    raw_tags: dict[str, dict] = {}
    category_counts: dict[str, int] = {}

    for el in all_elements:
        result = _parse_overpass_element(el)
        if not result:
            continue
        poi, tags = result
        if poi.id in seen_ids:
            continue

        cap = _MAX_POIS.get(poi.category, 30)
        count = category_counts.get(poi.category, 0)
        if count >= cap:
            continue

        seen_ids.add(poi.id)
        pois.append(poi)
        raw_tags[poi.id] = tags
        category_counts[poi.category] = count + 1

    def tag_richness(p: POI) -> int:
        t = raw_tags.get(p.id, {})
        score = len(t)
        if "wikidata" in t:
            score += 10
        if "opening_hours" in t:
            score += 3
        if "website" in t:
            score += 2
        return score

    pois.sort(key=tag_richness, reverse=True)
    result = (pois, raw_tags)
    await _cset(cache_key, result, _TTL_OVERPASS)
    log.info("Overpass: %d POIs for %s", len(pois), extraction.destination.city)
    return result


async def _wikidata_batch(session: aiohttp.ClientSession, qids: list[str]) -> dict:
    params = {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "labels|descriptions|claims|sitelinks",
        "languages": "en",
        "sitefilter": "enwiki",
        "format": "json",
    }
    try:
        async with session.get(_WIKIDATA_API, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("entities", {})
    except Exception as e:
        log.warning("Wikidata batch failed: %s", e)
        return {}


async def _wikipedia_summary(session: aiohttp.ClientSession, title: str) -> dict | None:
    url = f"{_WIKIPEDIA_SUMMARY_URL}/{quote_plus(title.replace(' ', '_'))}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:
        return None


async def _enrich_pois_wikidata(
    session: aiohttp.ClientSession,
    pois: list[POI],
    raw_tags: dict[str, dict],
) -> None:
    """Mutate pois in-place with Wikidata descriptions, images, and Wikipedia summaries."""
    qid_to_indices: dict[str, list[int]] = {}
    for i, poi in enumerate(pois):
        qid = raw_tags.get(poi.id, {}).get("wikidata")
        if qid:
            qid_to_indices.setdefault(qid, []).append(i)

    if not qid_to_indices:
        return

    wiki_titles: dict[int, str] = {}

    for batch in _chunks(list(qid_to_indices.keys()), 50):
        # batch cache lookup via mget instead of N serial gets
        cached_entities: dict = {}
        uncached: list[str] = []
        r = _get_redis()
        if r:
            try:
                keys = [f"{_KEY_PREFIX}wd:{qid}" for qid in batch]
                values = await r.mget(keys)
                for qid, raw in zip(batch, values):
                    if raw:
                        cached_entities[qid] = pickle.loads(raw)
                    else:
                        uncached.append(qid)
            except Exception:
                uncached = list(batch)
        else:
            uncached = list(batch)

        fetched = await _wikidata_batch(session, uncached) if uncached else {}
        for qid, entity in fetched.items():
            await _cset(f"wd:{qid}", entity, _TTL_WIKIDATA)

        entities = {**cached_entities, **fetched}

        for qid in batch:
            entity = entities.get(qid, {})
            indices = qid_to_indices.get(qid, [])

            desc = entity.get("descriptions", {}).get("en", {}).get("value")
            claims = entity.get("claims", {})

            # P18: image filename
            image_url = None
            if "P18" in claims:
                try:
                    filename = claims["P18"][0]["mainsnak"]["datavalue"]["value"]
                    image_url = _commons_thumb_url(filename)
                except (KeyError, IndexError):
                    pass

            # sitelinks for Wikipedia summary
            sitelinks = entity.get("sitelinks", {})
            wiki_title = sitelinks.get("enwiki", {}).get("title")

            for idx in indices:
                poi = pois[idx]
                if desc and not poi.description:
                    poi.description = desc
                if image_url and not poi.photo_url:
                    poi.photo_url = image_url
                if wiki_title:
                    wiki_titles[idx] = wiki_title

    # fetch Wikipedia summaries for the top 20 most notable POIs
    if wiki_titles:
        ranked = sorted(wiki_titles.keys(), key=lambda i: _completeness(pois[i]), reverse=True)[:20]
        tasks = [_wikipedia_summary(session, wiki_titles[i]) for i in ranked]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, result in zip(ranked, results):
            if isinstance(result, (Exception, type(None))):
                continue
            poi = pois[idx]
            extract = result.get("extract")
            if extract and (not poi.description or len(extract) > len(poi.description)):
                poi.description = extract
            thumb = result.get("thumbnail", {})
            if isinstance(thumb, dict) and thumb.get("source") and not poi.photo_url:
                poi.photo_url = thumb["source"]

    log.info("Wikidata enrichment: %d QIDs, %d Wikipedia summaries", len(qid_to_indices), len(wiki_titles))


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


def _parse_serp_maps_results(data: dict, query_type: str) -> list[POI]:
    pois: list[POI] = []
    for item in data.get("local_results", []):
        title = item.get("title")
        gps = item.get("gps_coordinates", {})
        if not title or not gps:
            continue

        place_id = item.get("place_id", "")
        item_type = (item.get("type") or "").lower()

        if query_type == "restaurants":
            cat, subcat = "restaurant", "restaurant"
        else:
            cat, subcat = "attraction", "attraction"
            for keyword in ("museum", "park", "church", "temple", "castle", "gallery"):
                if keyword in item_type:
                    subcat = keyword
                    break

        poi = POI(
            id=f"serp_{place_id}" if place_id else f"serp_{title[:30]}",
            name=title,
            lat=gps.get("latitude", 0),
            lng=gps.get("longitude", 0),
            category=cat,
            subcategory=subcat,
            rating=item.get("rating"),
            review_count=item.get("reviews"),
            price_level=_parse_price_level(item.get("price")),
            opening_hours=item.get("operating_hours"),
            description=item.get("description"),
            photo_url=item.get("thumbnail"),
            website=item.get("website"),
            visit_duration_min=_VISIT_DURATION.get(subcat, 60),
            cuisine=None,
            tags=_SUBCATEGORY_INTEREST_TAGS.get(subcat, []),
            source="serpapi",
        )
        pois.append(poi)
    return pois


async def _fetch_serp_maps(
    session: aiohttp.ClientSession,
    extraction: TripExtraction,
    query_type: str,
) -> list[POI]:
    city = extraction.destination.city
    country = extraction.destination.country
    cache_key = f"serp_maps:{city}:{country}:{query_type}"
    cached = await _cget(cache_key)
    if cached:
        return cached

    if query_type == "restaurants":
        q = f"best restaurants in {city} {country}"
    else:
        q = f"things to do in {city} {country}"

    lat, lng = extraction.destination.lat, extraction.destination.lng
    data = await _serpapi(session, {
        "engine": "google_maps",
        "q": q,
        "ll": f"@{lat},{lng},14z",
        "hl": "en",
    })

    pois: list[POI] = _parse_serp_maps_results(data, query_type) if data else []

    # cuisine-specific queries for restaurants
    if query_type == "restaurants" and extraction.meals.cuisine_preferences:
        cuisine_coros = [
            _serpapi(session, {
                "engine": "google_maps",
                "q": f"best {cuisine} restaurants in {city} {country}",
                "ll": f"@{lat},{lng},14z",
                "hl": "en",
            })
            for cuisine in extraction.meals.cuisine_preferences[:5]
        ]
        cuisine_results = await asyncio.gather(*cuisine_coros, return_exceptions=True)

        seen_ids = {p.id for p in pois}
        for result in cuisine_results:
            if isinstance(result, (Exception, type(None))):
                continue
            for poi in _parse_serp_maps_results(result, "restaurants"):
                if poi.id not in seen_ids:
                    seen_ids.add(poi.id)
                    pois.append(poi)

    await _cset(cache_key, pois, _TTL_SERP)
    log.info("SerpAPI maps (%s): %d results for %s", query_type, len(pois), city)
    return pois


async def _fetch_serp_events(session: aiohttp.ClientSession, extraction: TripExtraction) -> list[Event]:
    city = extraction.destination.city
    country = extraction.destination.country
    start = extraction.dates.start or ""
    end = extraction.dates.end or ""
    cache_key = f"serp_events:{city}:{start}:{end}"
    cached = await _cget(cache_key)
    if cached:
        return cached

    date_str = ""
    if start:
        try:
            s = datetime.strptime(start, "%Y-%m-%d")
            date_str = s.strftime("%B %d")
            if end:
                e = datetime.strptime(end, "%Y-%m-%d")
                date_str += f" - {e.strftime('%B %d %Y')}"
            else:
                date_str += f" {s.year}"
        except ValueError:
            pass

    q = f"events in {city} {country}"
    if date_str:
        q += f" {date_str}"

    data = await _serpapi(session, {
        "engine": "google_events",
        "q": q,
        "hl": "en",
    })
    if not data:
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

    await _cset(cache_key, events, _TTL_SERP_EVENTS)
    log.info("SerpAPI events: %d results for %s", len(events), city)
    return events


async def _fetch_serp_hotels(session: aiohttp.ClientSession, extraction: TripExtraction) -> list[HotelOption]:
    if extraction.accommodation.booked:
        return []

    city = extraction.destination.city
    country = extraction.destination.country
    start = extraction.dates.start
    end = extraction.dates.end
    if not start or not end:
        return []

    adults = extraction.travelers.count or 1
    cache_key = f"serp_hotels:{city}:{start}:{end}:{adults}"
    cached = await _cget(cache_key)
    if cached:
        return cached

    data = await _serpapi(session, {
        "engine": "google_hotels",
        "q": f"{city} {country}",
        "check_in_date": start,
        "check_out_date": end,
        "adults": str(adults),
        "currency": "USD",
        "hl": "en",
        "gl": "us",
    })
    if not data:
        return []

    hotels: list[HotelOption] = []
    for item in data.get("properties", []):
        name = item.get("name")
        if not name:
            continue

        rate = item.get("rate_per_night", {})
        total = item.get("total_rate", {})
        gps = item.get("gps_coordinates", {})
        images = item.get("images", [])

        # hotel_class comes as "4-star hotel" or int — normalize to int
        raw_stars = item.get("hotel_class")
        stars = None
        if isinstance(raw_stars, int):
            stars = raw_stars
        elif isinstance(raw_stars, str):
            digits = "".join(c for c in raw_stars if c.isdigit())
            stars = int(digits) if digits else None

        hotels.append(HotelOption(
            name=name,
            price_per_night=rate.get("extracted_lowest"),
            total_price=total.get("extracted_lowest"),
            currency="USD",
            rating=item.get("overall_rating"),
            review_count=item.get("reviews"),
            stars=stars,
            address=item.get("description"),
            lat=gps.get("latitude"),
            lng=gps.get("longitude"),
            photo_url=images[0].get("thumbnail") if images else item.get("thumbnail"),
            amenities=item.get("amenities", []),
            link=item.get("link"),
        ))

    await _cset(cache_key, hotels, _TTL_SERP_HOTELS)
    log.info("SerpAPI hotels: %d results for %s", len(hotels), city)
    return hotels


async def _resolve_iata(session: aiohttp.ClientSession, city: str, country: str) -> str | None:
    """Resolve a city name to its primary IATA airport code via SerpAPI autocomplete."""
    cache_key = f"iata:{city.lower()}:{country.lower()}"
    cached = await _cget(cache_key)
    if cached:
        return cached

    data = await _serpapi(session, {
        "engine": "google_flights_autocomplete",
        "q": f"{city} {country}",
    })
    if not data:
        return None

    # response: suggestions[].airports[].id
    for suggestion in data.get("suggestions", []):
        for airport in suggestion.get("airports", []):
            iata = airport.get("id")
            if iata and len(iata) == 3:
                await _cset(cache_key, iata, _TTL_IATA)
                log.info("IATA resolved: %s %s → %s", city, country, iata)
                return iata

    log.info("IATA resolution failed for %s %s", city, country)
    return None


async def _fetch_serp_flights(
    session: aiohttp.ClientSession,
    extraction: TripExtraction,
    origin_city: str | None,
    origin_country: str | None,
) -> list[FlightOption]:
    if not origin_city or not origin_country:
        return []
    if not extraction.dates.start:
        return []
    # skip domestic trips
    if origin_country.lower() == extraction.destination.country.lower():
        return []

    dest_city = extraction.destination.city
    dest_country = extraction.destination.country

    # resolve both cities to IATA codes (parallel)
    dep_iata, arr_iata = await asyncio.gather(
        _resolve_iata(session, origin_city, origin_country),
        _resolve_iata(session, dest_city, dest_country),
    )
    if not dep_iata or not arr_iata:
        return []

    start = extraction.dates.start
    end = extraction.dates.end
    cache_key = f"serp_flights:{dep_iata}:{arr_iata}:{start}:{end}"
    cached = await _cget(cache_key)
    if cached:
        return cached

    adults = extraction.travelers.count or 1

    params = {
        "engine": "google_flights",
        "departure_id": dep_iata,
        "arrival_id": arr_iata,
        "outbound_date": start,
        "adults": str(adults),
        "currency": "USD",
        "hl": "en",
        "type": "2",  # one-way outbound
    }
    if end:
        params["return_date"] = end
        params["type"] = "1"  # round trip

    # map travel_class string to SerpAPI int
    _CLASS_MAP = {"economy": 1, "business": 3, "first": 4}
    tc = extraction.flight_preferences.travel_class
    if tc and tc in _CLASS_MAP:
        params["travel_class"] = str(_CLASS_MAP[tc])

    data = await _serpapi(session, params)
    if not data:
        return []

    flights: list[FlightOption] = []
    for result in data.get("best_flights", []) + data.get("other_flights", []):
        legs = result.get("flights", [])
        if not legs:
            continue

        first_leg = legs[0]
        last_leg = legs[-1]

        # total duration across all legs
        total_duration = result.get("total_duration", 0)
        price = result.get("price")

        # layover airports (connecting cities between legs)
        layovers = []
        for i, leg in enumerate(legs[1:], 1):
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
            stops=len(legs) - 1,
            layovers=layovers,
        ))

    # sort by price, then duration
    flights.sort(key=lambda f: (f.price or float("inf"), f.duration_min))

    await _cset(cache_key, flights, _TTL_SERP_FLIGHTS)
    log.info("SerpAPI flights: %d results %s→%s", len(flights), dep_iata, arr_iata)
    return flights


async def _fetch_weather(session: aiohttp.ClientSession, extraction: TripExtraction) -> list[DayWeather]:
    lat, lng = extraction.destination.lat, extraction.destination.lng
    start = extraction.dates.start
    end = extraction.dates.end
    if not start or not end:
        return []

    cache_key = f"weather:{lat:.2f}:{lng:.2f}:{start}:{end}"
    cached = await _cget(cache_key)
    if cached:
        return cached

    # use forecast if within 16 days, otherwise use historical data from last year
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        days_ahead = (start_dt - datetime.now()).days
    except ValueError:
        return []

    use_forecast = days_ahead <= 15

    if use_forecast:
        url = _OPEN_METEO_FORECAST
        s, e = start, end
        # forecast API param names
        daily_params = "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode,windspeed_10m_max"
    else:
        # archive API: different param names, no precipitation_probability
        url = _OPEN_METEO_ARCHIVE
        s_dt = start_dt.replace(year=start_dt.year - 1)
        e_dt = datetime.strptime(end, "%Y-%m-%d").replace(year=start_dt.year - 1)
        s, e = s_dt.strftime("%Y-%m-%d"), e_dt.strftime("%Y-%m-%d")
        daily_params = "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code,wind_speed_10m_max"

    params = {
        "latitude": str(lat),
        "longitude": str(lng),
        "daily": daily_params,
        "start_date": s,
        "end_date": e,
        "timezone": "auto",
    }

    try:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception as e_err:
        log.warning("Open-Meteo failed: %s", e_err)
        return []

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])

    if use_forecast:
        precip_prob = daily.get("precipitation_probability_max", [])
        codes = daily.get("weathercode", [])
        wind = daily.get("windspeed_10m_max", [])
    else:
        precip_prob = []
        codes = daily.get("weather_code", [])
        wind = daily.get("wind_speed_10m_max", [])

    weather: list[DayWeather] = []
    for i, d in enumerate(dates):
        actual_date = d
        if not use_forecast:
            # map historical dates back to the actual trip year
            try:
                hist_dt = datetime.strptime(d, "%Y-%m-%d")
                actual_date = hist_dt.replace(year=hist_dt.year + 1).strftime("%Y-%m-%d")
            except ValueError:
                pass

        # estimate precip probability from historical amount when unavailable
        if i < len(precip_prob):
            prob = precip_prob[i]
        elif i < len(precip) and precip[i] is not None:
            prob = min(precip[i] / 5.0 * 100, 100.0) if precip[i] > 0 else 0.0
        else:
            prob = 0.0

        weather.append(DayWeather(
            date=actual_date,
            temp_high_c=highs[i] if i < len(highs) else 20.0,
            temp_low_c=lows[i] if i < len(lows) else 10.0,
            precipitation_mm=precip[i] if i < len(precip) else 0.0,
            precipitation_prob=prob,
            condition=_WMO_CONDITIONS.get(codes[i] if i < len(codes) else 0, "clear"),
            wind_speed_kmh=wind[i] if i < len(wind) else 0.0,
        ))

    await _cset(cache_key, weather, _TTL_WEATHER)
    source_label = "forecast" if use_forecast else "historical"
    log.info("Weather (%s): %d days for %s", source_label, len(weather), extraction.destination.city)
    return weather


async def compute_travel_matrix(
    pois: list[POI],
    mode: str | None = None,
) -> dict[str, dict[str, int]]:
    """Compute travel time matrix (minutes) between POIs.

    Call this AFTER Stage 3 scoring, with the filtered top 40-50 POIs.
    Returns {poi_id: {poi_id: minutes}}.
    Falls back to haversine if ORS is unavailable.
    """
    if len(pois) < 2:
        return {}

    # cap at 50 to stay within ORS free tier (50x50 = 2500 elements < 3500 limit)
    pois = pois[:50]

    profile = _ORS_PROFILES.get(mode, "driving-car")
    drive_mode = "walking" if profile == "foot-walking" else "driving"

    if settings.openrouteservice_api_key:
        async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
            matrix = await _ors_matrix(session, pois, profile)
        if matrix:
            return matrix
        log.info("ORS matrix failed, falling back to haversine")

    # haversine fallback — symmetric, so compute half and mirror
    result: dict[str, dict[str, int]] = {p.id: {} for p in pois}
    for i, a in enumerate(pois):
        result[a.id][a.id] = 0
        for b in pois[i + 1:]:
            mins = _travel_min_haversine(a.lat, a.lng, b.lat, b.lng, drive_mode)
            result[a.id][b.id] = mins
            result[b.id][a.id] = mins
    return result


async def _ors_matrix(session: aiohttp.ClientSession, pois: list[POI], profile: str) -> dict[str, dict[str, int]] | None:
    url = f"{_ORS_MATRIX_URL}/{profile}"
    locations = [[poi.lng, poi.lat] for poi in pois]
    body = {"locations": locations, "metrics": ["duration"]}
    headers = {
        "Authorization": settings.openrouteservice_api_key,
        "Content-Type": "application/json",
    }

    try:
        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status == 429:
                log.warning("ORS rate limited")
                return None
            resp.raise_for_status()
            data = await resp.json()
    except Exception as e:
        log.warning("ORS matrix failed: %s", e)
        return None

    durations = data.get("durations")
    if not durations:
        return None

    result: dict[str, dict[str, int]] = {}
    for i, a in enumerate(pois):
        result[a.id] = {}
        for j, b in enumerate(pois):
            secs = durations[i][j]
            result[a.id][b.id] = max(1, round(secs / 60)) if secs is not None else 999
    return result


async def _fetch_destination_photo(session: aiohttp.ClientSession, extraction: TripExtraction) -> str | None:
    if not settings.unsplash_access_key:
        return None

    city = extraction.destination.city
    country = extraction.destination.country

    try:
        headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}
        params = {"query": f"{city} {country} travel", "per_page": "1", "orientation": "landscape"}
        async with session.get(_UNSPLASH_URL, headers=headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            results = data.get("results", [])
            if results:
                return results[0].get("urls", {}).get("regular")
    except Exception as e:
        log.warning("Unsplash failed: %s", e)

    return None


def _merge_pois(serp_pois: list[POI], overpass_pois: list[POI]) -> list[POI]:
    """Merge SerpAPI and Overpass POIs, deduplicating by proximity."""
    if not serp_pois:
        return overpass_pois
    if not overpass_pois:
        return serp_pois

    merged = list(serp_pois)
    serp_coords = [(p.lat, p.lng) for p in serp_pois]

    # ~0.001 degrees ≈ 111m at equator, close enough for dedup
    threshold = 0.001
    for poi in overpass_pois:
        is_dup = any(
            abs(poi.lat - slat) < threshold and abs(poi.lng - slng) < threshold
            for slat, slng in serp_coords
        )
        if not is_dup:
            merged.append(poi)

    return merged


async def acquire(
    extraction: TripExtraction,
    origin_city: str | None = None,
    origin_country: str | None = None,
) -> AcquisitionResult:
    """Fetch all external data for a trip. Returns partial results on API failures."""
    # detect local trips — skip hotels/flights when destination is the origin city
    dest = extraction.destination
    is_local = (
        origin_city
        and origin_country
        and dest.city.lower() == origin_city.lower()
        and dest.country.lower() == origin_country.lower()
    )

    async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
        # always fetch: POIs, events, weather, hero photo
        # conditionally fetch: hotels (not local, not pre-booked), flights (not local, international)
        coros = [
            _fetch_overpass(session, extraction),
            _fetch_serp_maps(session, extraction, "attractions"),
            _fetch_serp_maps(session, extraction, "restaurants"),
            _fetch_serp_events(session, extraction),
            _fetch_serp_hotels(session, extraction) if not is_local else _noop([]),
            _fetch_serp_flights(session, extraction, origin_city, origin_country) if not is_local else _noop([]),
            _fetch_weather(session, extraction),
            _fetch_destination_photo(session, extraction),
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        def _safe(val, default=None):
            if isinstance(val, Exception):
                log.warning("Stage 2 task failed: %s", val)
                return default
            return val

        (raw_overpass, raw_attractions, raw_restaurants,
         raw_events, raw_hotels, raw_flights, raw_weather, raw_photo) = results

        overpass_result = _safe(raw_overpass, ([], {}))
        if not isinstance(overpass_result, tuple):
            overpass_result = ([], {})
        overpass_pois, raw_tags = overpass_result
        serp_attractions = _safe(raw_attractions, [])
        serp_restaurants = _safe(raw_restaurants, [])
        events = _safe(raw_events, [])
        hotels = _safe(raw_hotels, [])
        flights = _safe(raw_flights, [])
        weather = _safe(raw_weather, [])
        hero_url = _safe(raw_photo, None)

        if overpass_pois:
            try:
                await _enrich_pois_wikidata(session, overpass_pois, raw_tags)
            except Exception as e:
                log.warning("Wikidata enrichment failed: %s", e)

        serp_pois = serp_attractions + serp_restaurants
        pois = _merge_pois(serp_pois, overpass_pois)

        log.info(
            "Stage 2 complete%s: %d POIs (%d serp + %d osm), %d events, %d hotels, %d flights, %d weather days",
            " (local)" if is_local else "",
            len(pois), len(serp_pois), len(overpass_pois), len(events), len(hotels), len(flights), len(weather),
        )

        return AcquisitionResult(
            pois=pois,
            events=events,
            hotels=hotels,
            flights=flights,
            weather=weather,
            destination_photo_url=hero_url,
        )
