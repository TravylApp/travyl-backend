import asyncio
import copy
import json
import logging
import threading
import urllib.request
from datetime import date, datetime
from typing import Any

import boto3
from geopy.geocoders import Nominatim

from app.config import settings
from app.schemas import ExtractionRequest, ExtractionResponse, TripExtraction

log = logging.getLogger(__name__)

_EXTRACT_TOOL = "extract_trip_params"
_FOLLOWUP_TOOL = "ask_follow_up"

_FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1, "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
                },
                "required": ["id", "question", "options"],
            },
        }
    },
    "required": ["questions"],
}

_SYSTEM_PROMPT = """\
You are an expert travel planner powering Travyl. Extract structured trip \
parameters from the user's query AND ask follow-up questions for important gaps.

USER CONTEXT:
- Current location: {city}, {country}
- Today's date: {today}

ALWAYS call extract_trip_params — fill in what you can, leave the rest null/empty.

Then ALSO call ask_follow_up if any of these are missing or vague:
- No clear destination → ask where
- No dates or timeframe → ask when (but if they say "this summer" or "sometime \
in March", infer dates and set flexible=true — don't ask)
- No traveler info and context is ambiguous → ask who's going
- Vague budget signals → ask budget preference

Do NOT ask about things you can confidently infer. "Honeymoon in Bali" gives you \
destination, composition, occasion, pace — don't ask about those.

EXTRACTION RULES:

DESTINATION: Resolve to city + country. "somewhere tropical" → Bali or Cancun. \
"Europe" → best fit for context.

DATES: If the user gives a timeframe, compute YYYY-MM-DD. "next Tuesday" = real date. \
"this summer" = June 1. "mid-March" = March 15. End = start + duration - 1. \
Set flexible=true for vague timeframes ("sometime in March"). \
If NO dates, season, or timeframe is mentioned at all, leave start and end as null \
and ask a follow-up about when they want to travel.

DURATION: Always set duration_days even if dates are null. \
"3 day trip" = 3. "2 weeks" = 14. "long weekend" = 3. Default 7.

TRAVELERS: Default count=1. "we"/"partner" = couple/2. "family" = family/4. \
"buddies" = friends/4. Set occasion only from explicit mention.

INTERESTS: Always infer 2-5 from destination. Rome = [ancient_ruins, history, \
architecture, art, local_cuisine]. Expand vague: "I like food" = [street_food, \
fine_dining, local_cuisine].

BUDGET: "backpacking" = budget, "5-star" = luxury. Null if unclear.

PACE: "chill" = relaxed, "pack it in" = packed. Null if unclear.

MEALS: "I love sushi" → cuisine_preferences=["japanese"]. "no breakfast"/"we'll eat on our own" → include_in_itinerary=false. Default include_in_itinerary=true. Only set cuisine_preferences from explicit mention.

ACCOMMODATION: "hostel"/"resort"/"Airbnb" → set type. booked=true only if stated.

FLIGHTS: "no Spirit" → flight_preferences.avoid_airlines=["Spirit"]. "prefer United" → flight_preferences.preferred_airlines=["United"]. "direct only" → flight_preferences.max_stops=0. "business class" → flight_preferences.travel_class="business". Only from explicit mention.

CONSTRAINTS: All from explicit input only. \
arrival_time/departure_time = HH:MM from flight mentions. \
daily_start_time/daily_end_time = HH:MM from schedule preferences. \
must_visit = explicitly named places only. \
mobility_level: "bad knee"/"wheelchair" → limited/wheelchair.

CROSS-REFERENCING:
- "honeymoon" → couple, 2, honeymoon occasion, relaxed pace
- "backpacking with buddies" → friends, budget
- "business trip" → group, conference, packed
- "family vacation" → family, 4

FOLLOW-UP QUESTION RULES:
Options must be concrete and human-friendly, NOT mirror schema values. Examples:
- Budget: "$50/day or less", "$100-200/day", "$200-400/day", "$400+/day"
- Dates: "This spring (Apr-May)", "Summer 2026", "Fall 2026", "I'm flexible"
- Pace: "Take it easy, lots of downtime", "A good mix", "See as much as possible"
- Accommodation: "Hotel", "Hostel/budget stay", "Airbnb/apartment", "Resort/spa"
Never use internal terms like "moderate", "comfortable", "packed" as option text."""


# Server-computed fields to strip from the Bedrock tool schema
_STRIP_FROM_DESTINATION = ("lat", "lng")
_STRIP_FROM_ROOT = ("daily_estimate_usd",)


def _strip_descriptions(obj: dict | list) -> None:
    """Recursively remove 'description' keys — system prompt has all guidance."""
    if isinstance(obj, dict):
        obj.pop("description", None)
        for v in obj.values():
            _strip_descriptions(v)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _strip_descriptions(item)


def _build_tool_schema():
    schema = copy.deepcopy(TripExtraction.model_json_schema())
    defs = schema.get("$defs", {})

    dest = defs.get("Destination", {})
    if "properties" in dest:
        for key in _STRIP_FROM_DESTINATION:
            dest["properties"].pop(key, None)
        req = dest.get("required", [])
        dest["required"] = [r for r in req if r not in _STRIP_FROM_DESTINATION]

    props = schema.get("properties", {})
    for key in _STRIP_FROM_ROOT:
        props.pop(key, None)

    _strip_descriptions(schema)
    return schema


_EXTRACT_TOOL_SPEC = {
    "toolSpec": {
        "name": _EXTRACT_TOOL,
        "description": "Extract structured trip parameters.",
        "inputSchema": {"json": _build_tool_schema()},
    }
}

_EXTRACT_ONLY_CONFIG = {
    "tools": [_EXTRACT_TOOL_SPEC],
    "toolChoice": {"tool": {"name": _EXTRACT_TOOL}},
}

_EXTRACT_AND_FOLLOWUP_CONFIG = {
    "tools": [
        _EXTRACT_TOOL_SPEC,
        {
            "toolSpec": {
                "name": _FOLLOWUP_TOOL,
                "description": "Ask 1-5 clarifying questions for missing trip details.",
                "inputSchema": {"json": _FOLLOWUP_SCHEMA},
            }
        },
    ],
    "toolChoice": {"any": {}},
}


# ---------------------------------------------------------------------------
# Singletons — pre-warmed on startup via warm_caches()
# ---------------------------------------------------------------------------

_bedrock_client = None
_geocoder = Nominatim(user_agent="travyl-backend", timeout=5)
_geo_cache: dict[str, dict] = {}
_cost_index: dict[str, float] | None = None


def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        if settings.aws_bearer_token:
            from botocore.config import Config
            session = boto3.Session(
                aws_access_key_id="unused",
                aws_secret_access_key="unused",
                region_name=settings.aws_region,
            )
            _bedrock_client = session.client(
                "bedrock-runtime",
                config=Config(signature_version="v4"),
            )
            _bedrock_client.meta.events.register_last(
                "before-send.bedrock-runtime.*",
                _inject_bearer_token,
            )
        else:
            _bedrock_client = boto3.client(
                "bedrock-runtime", region_name=settings.aws_region,
            )
    return _bedrock_client


def _inject_bearer_token(request, **kwargs):
    request.headers["Authorization"] = f"Bearer {settings.aws_bearer_token}"


def _geocode(city: str, country: str) -> dict:
    key = f"{city.lower()}|{country.lower()}"
    if key in _geo_cache:
        return _geo_cache[key]

    try:
        location = _geocoder.geocode(
            f"{city}, {country}", exactly_one=True, addressdetails=True,
        )
        if location:
            addr = location.raw.get("address", {})
            region = addr.get("state", "") or addr.get("region", "")
            result = {"lat": location.latitude, "lng": location.longitude, "region": region}
            _geo_cache[key] = result
            return result
    except Exception as e:
        log.warning("Geocoding failed for %s, %s: %s", city, country, e)

    return {"lat": 0.0, "lng": 0.0, "region": ""}


def _reverse_geocode(lat: float, lng: float) -> dict:
    key = f"{lat:.4f}|{lng:.4f}"
    if key in _geo_cache:
        return _geo_cache[key]

    try:
        location = _geocoder.reverse(
            (lat, lng), exactly_one=True, addressdetails=True,
        )
        if location:
            addr = location.raw.get("address", {})
            city = addr.get("city") or addr.get("town") or addr.get("village") or ""
            country = addr.get("country", "")
            result = {"city": city, "country": country}
            _geo_cache[key] = result
            return result
    except Exception as e:
        log.warning("Reverse geocoding failed for %s, %s: %s", lat, lng, e)

    return {"city": "Unknown", "country": "Unknown"}


def _fetch_cost_index() -> dict[str, float]:
    global _cost_index
    if _cost_index is not None:
        return _cost_index

    url = (
        "https://api.worldbank.org/v2/country/all/indicator/PA.NUS.PPPC.RF"
        "?date=2023&format=json&per_page=500"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "travyl-backend"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            pages = json.loads(resp.read())

        records = pages[1] if len(pages) > 1 else []

        raw: dict[str, tuple[str, float]] = {}
        for r in records:
            iso2 = r["country"]["id"]
            if r.get("value") is None or len(iso2) != 2:
                continue
            raw[iso2] = (r["country"]["value"], r["value"])

        spain_val = raw.get("ES", (None, 1.0))[1]

        _cost_index = {}
        for iso2, (name, val) in raw.items():
            ratio = round(val / spain_val, 2)
            _cost_index[name.lower()] = ratio
            _cost_index[iso2.lower()] = ratio

        log.info("Loaded cost index: %d countries from World Bank", len(raw))
        return _cost_index

    except Exception as e:
        log.warning("World Bank API failed, using neutral cost index: %s", e)
        _cost_index = {}
        return _cost_index


def _base_daily_usd() -> dict[str, int]:
    return {
        "budget": settings.budget_base_usd,
        "moderate": settings.moderate_base_usd,
        "comfortable": settings.comfortable_base_usd,
        "luxury": settings.luxury_base_usd,
    }


def warm_caches():
    def _warm():
        _get_bedrock()
        _fetch_cost_index()
        log.info("Caches warmed: bedrock client + cost index")
    threading.Thread(target=_warm, daemon=True).start()


def _unwrap_json_strings(raw: dict[str, Any]) -> None:
    for key, val in raw.items():
        if not isinstance(val, str) or not val.startswith(("{", "[")):
            continue
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and key in parsed:
            raw[key] = parsed[key]
        elif isinstance(parsed, list) and parsed:
            raw[key] = parsed[0]


def _enrich(raw: dict[str, Any]) -> TripExtraction:
    _unwrap_json_strings(raw)
    extraction = TripExtraction.model_validate(raw)

    geo = _geocode(extraction.destination.city, extraction.destination.country)
    extraction.destination.lat = geo["lat"]
    extraction.destination.lng = geo["lng"]
    if geo["region"]:
        extraction.destination.region = geo["region"]

    # Compute duration from dates if available (overrides model's value)
    if extraction.dates.start and extraction.dates.end:
        try:
            start = datetime.strptime(extraction.dates.start, "%Y-%m-%d")
            end = datetime.strptime(extraction.dates.end, "%Y-%m-%d")
            if end < start:
                end = start
                extraction.dates.end = extraction.dates.start
            extraction.duration_days = (end - start).days + 1
        except ValueError:
            pass
    # If no dates but model didn't set duration either, default to 7
    elif extraction.duration_days == 0:
        extraction.duration_days = 7

    if extraction.budget_level:
        cost_index = _fetch_cost_index()
        index = cost_index.get(extraction.destination.country.lower(), 1.0)
        base = _base_daily_usd()
        extraction.daily_estimate_usd = round(base[extraction.budget_level] * index)

    return extraction


def _call_bedrock(system_text: str, user_text: str, tool_config: dict) -> dict:
    return _get_bedrock().converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": system_text}],
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        toolConfig=tool_config,
        inferenceConfig={"temperature": 0.1},
    )


class BedrockExtractionError(Exception):
    pass


async def extract(request: ExtractionRequest) -> ExtractionResponse:
    # Resolve user location
    if request.city and request.country:
        city, country = request.city, request.country
    elif request.lat is not None and request.lng is not None:
        loc = await asyncio.to_thread(_reverse_geocode, request.lat, request.lng)
        city, country = loc["city"], loc["country"]
    else:
        city, country = "Unknown", "Unknown"

    today = date.today().isoformat()
    system_text = _SYSTEM_PROMPT.format(city=city, country=country, today=today)

    user_text = request.prompt
    has_answers = bool(request.answers)
    if has_answers:
        answer_lines = "\n".join(f"- {k}: {v}" for k, v in request.answers.items())
        user_text += f"\n\nAdditional info from user:\n{answer_lines}"

    # One round of follow-ups max: if answers are provided, force extract-only
    tool_config = _EXTRACT_ONLY_CONFIG if has_answers else _EXTRACT_AND_FOLLOWUP_CONFIG

    try:
        response = await asyncio.to_thread(_call_bedrock, system_text, user_text, tool_config)
    except Exception as e:
        raise BedrockExtractionError(f"Bedrock request failed: {e}") from e

    # Collect both tool calls — model can return extraction + follow-ups together
    extraction = None
    questions = []

    for block in response.get("output", {}).get("message", {}).get("content", []):
        if "toolUse" not in block:
            continue
        tool = block["toolUse"]

        if tool["name"] == _EXTRACT_TOOL and extraction is None:
            extraction = await asyncio.to_thread(_enrich, tool["input"])

        elif tool["name"] == _FOLLOWUP_TOOL and not questions:
            questions = tool["input"].get("questions", [])

    if extraction is None and not questions:
        raise BedrockExtractionError("Bedrock response did not contain a tool use block")

    if questions:
        return ExtractionResponse(
            status="needs_clarification",
            extracted=extraction,
            questions=questions,
        )

    return ExtractionResponse(status="complete", extracted=extraction)
