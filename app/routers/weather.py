"""Weather forecast endpoint — free Open-Meteo API (no key required)."""

import logging
import re

import aiohttp
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/weather", tags=["weather"])
log = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = aiohttp.ClientTimeout(total=20)

# WMO Weather interpretation codes (WMO 4677)
# Maps code -> (condition text, icon name)
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "clear"),
    1: ("Mainly clear", "mostly-clear"),
    2: ("Partly cloudy", "partly-cloudy"),
    3: ("Overcast", "overcast"),
    45: ("Fog", "fog"),
    48: ("Depositing rime fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Moderate drizzle", "drizzle"),
    55: ("Dense drizzle", "drizzle"),
    56: ("Light freezing drizzle", "freezing-drizzle"),
    57: ("Dense freezing drizzle", "freezing-drizzle"),
    61: ("Slight rain", "rain"),
    63: ("Moderate rain", "rain"),
    65: ("Heavy rain", "heavy-rain"),
    66: ("Light freezing rain", "freezing-rain"),
    67: ("Heavy freezing rain", "freezing-rain"),
    71: ("Slight snowfall", "snow"),
    73: ("Moderate snowfall", "snow"),
    75: ("Heavy snowfall", "heavy-snow"),
    77: ("Snow grains", "snow"),
    80: ("Slight rain showers", "rain-showers"),
    81: ("Moderate rain showers", "rain-showers"),
    82: ("Violent rain showers", "heavy-rain"),
    85: ("Slight snow showers", "snow-showers"),
    86: ("Heavy snow showers", "heavy-snow"),
    95: ("Thunderstorm", "thunderstorm"),
    96: ("Thunderstorm with slight hail", "thunderstorm-hail"),
    99: ("Thunderstorm with heavy hail", "thunderstorm-hail"),
}

_LATLON_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$")


def _decode_wmo(code: int | None) -> tuple[str, str]:
    """Return (condition text, icon name) for a WMO weather code."""
    if code is None:
        return ("Unknown", "unknown")
    return WMO_CODES.get(code, ("Unknown", "unknown"))


async def _geocode(session: aiohttp.ClientSession, location: str) -> tuple[float, float, str]:
    """Resolve a location name to (lat, lng, display_name).

    If *location* already looks like "lat,lng", parse directly.
    Otherwise, call the Open-Meteo geocoding API.
    """
    match = _LATLON_RE.match(location.strip())
    if match:
        lat, lng = float(match.group(1)), float(match.group(2))
        return lat, lng, f"{lat},{lng}"

    try:
        async with session.get(_GEOCODE_URL, params={"name": location, "count": 1}) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception as e:
        log.warning("Open-Meteo geocoding failed: %s", e)
        raise HTTPException(status_code=502, detail="Geocoding request failed")

    results = data.get("results")
    if not results:
        raise HTTPException(status_code=404, detail=f"Location not found: {location}")

    result = results[0]
    name_parts = [result.get("name", location)]
    if result.get("admin1"):
        name_parts.append(result["admin1"])
    if result.get("country"):
        name_parts.append(result["country"])

    return result["latitude"], result["longitude"], ", ".join(name_parts)


async def _fetch_forecast(
    session: aiohttp.ClientSession, lat: float, lng: float, days: int
) -> dict:
    """Fetch weather forecast from Open-Meteo."""
    params = {
        "latitude": str(lat),
        "longitude": str(lng),
        "daily": "temperature_2m_max,temperature_2m_min,weathercode,precipitation_probability_max,sunrise,sunset",
        "current": "temperature_2m,apparent_temperature,weathercode,relative_humidity_2m,wind_speed_10m",
        "timezone": "auto",
        "forecast_days": str(days),
    }
    try:
        async with session.get(_FORECAST_URL, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception as e:
        log.warning("Open-Meteo forecast request failed: %s", e)
        raise HTTPException(status_code=502, detail="Weather forecast request failed")


@router.get("/forecast")
async def get_forecast(
    location: str = Query(..., description="City name or 'lat,lng'"),
    days: int = Query(default=7, ge=1, le=14, description="Forecast days (1-14)"),
):
    """Return current conditions and daily forecast for a location."""
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        lat, lng, display_name = await _geocode(session, location)
        data = await _fetch_forecast(session, lat, lng, days)

    # --- current conditions ---
    current_data = data.get("current", {})
    current_code = current_data.get("weathercode")
    current_cond, current_icon = _decode_wmo(current_code)

    current = {
        "temp": current_data.get("temperature_2m"),
        "feelslike": current_data.get("apparent_temperature"),
        "conditions": current_cond,
        "icon": current_icon,
        "humidity": current_data.get("relative_humidity_2m"),
        "windspeed": current_data.get("wind_speed_10m"),
    }

    # --- daily forecast ---
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    codes = daily.get("weathercode", [])
    precip_probs = daily.get("precipitation_probability_max", [])
    sunrises = daily.get("sunrise", [])
    sunsets = daily.get("sunset", [])

    forecast = []
    for i, date in enumerate(dates):
        code = codes[i] if i < len(codes) else None
        cond, icon = _decode_wmo(code)
        forecast.append({
            "date": date,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "conditions": cond,
            "icon": icon,
            "precipprob": precip_probs[i] if i < len(precip_probs) else None,
            "sunrise": sunrises[i] if i < len(sunrises) else None,
            "sunset": sunsets[i] if i < len(sunsets) else None,
        })

    return {
        "location": display_name,
        "timezone": data.get("timezone", ""),
        "current": current,
        "forecast": forecast,
    }
