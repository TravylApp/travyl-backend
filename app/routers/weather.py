"""Weather forecast endpoint — free Open-Meteo API (no key required)."""

import logging
import re

import aiohttp
from fastapi import APIRouter, HTTPException, Query

from app.config import settings

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


# =============================================================================
# Visual Crossing Weather API (Issue #579)
# Alternative provider with air quality + astronomy data
# =============================================================================

_VISUAL_CROSSING_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"


@router.get("/visualcrossing")
async def get_visualcrossing_forecast(
    location: str = Query(..., description="City name or 'lat,lng'"),
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD), defaults to today"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD), defaults to start + 7 days"),
    include_aqi: bool = Query(default=True, description="Include air quality data"),
    include_astronomy: bool = Query(default=True, description="Include sunrise/sunset/moon phase"),
):
    """Fetch weather from Visual Crossing API with air quality & astronomy.
    
    This endpoint provides enhanced weather data including:
    - Air quality index (AQI) and pollutant levels
    - UV index and solar radiation
    - Moon phase and illumination
    - More detailed precipitation types
    
    Requires VISUALCROSSING_API_KEY in environment.
    """
    if not settings.visualcrossing_api_key:
        raise HTTPException(status_code=503, detail="Visual Crossing API key not configured")
    
    # Build date range
    if start_date:
        s = start_date
        if end_date:
            e = end_date
        else:
            # Default to 7 days from start
            from datetime import datetime, timedelta
            s_dt = datetime.strptime(start_date, "%Y-%m-%d")
            e_dt = s_dt + timedelta(days=7)
            e = e_dt.strftime("%Y-%m-%d")
    else:
        # Default to today + 7 days
        from datetime import datetime, timedelta
        today = datetime.now()
        s = today.strftime("%Y-%m-%d")
        e_dt = today + timedelta(days=7)
        e = e_dt.strftime("%Y-%m-%d")
    
    # Build URL with all the extra data elements
    location_encoded = location.replace(" ", "%20")
    url = f"{_VISUAL_CROSSING_URL}/{location_encoded}/{s}/{e}"
    
    params = {
        "key": settings.visualcrossing_api_key,
        "unitGroup": "us",  # Fahrenheit, mph
        "include": "days,current",
    }
    
    if include_aqi:
        params["elements"] = params.get("elements", "") + ",aqi,pm2p5,pm10,o3,no2,so2,co"
    if include_astronomy:
        params["elements"] = params.get("elements", "") + ",sunrise,sunset,moonrise,moonset,moonphase"
    
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 401:
                    raise HTTPException(status_code=503, detail="Invalid Visual Crossing API key")
                if resp.status == 429:
                    raise HTTPException(status_code=429, detail="Visual Crossing rate limit exceeded")
                resp.raise_for_status()
                data = await resp.json()
    except aiohttp.ClientError as e:
        log.warning("Visual Crossing request failed: %s", e)
        raise HTTPException(status_code=502, detail="Visual Crossing API request failed")
    
    # Parse response
    days = data.get("days", [])
    current = data.get("currentConditions", {})
    
    # Build enriched forecast
    forecast = []
    for day in days:
        day_data = {
            "date": day.get("datetime"),
            "high": day.get("tempmax"),
            "low": day.get("tempmin"),
            "conditions": day.get("conditions"),
            "description": day.get("description"),
            "icon": day.get("icon"),
            "precip": day.get("precip"),
            "precipprob": day.get("precipprob"),
            "preciptype": day.get("preciptype"),
            "humidity": day.get("humidity"),
            "windspeed": day.get("windspeed"),
            "uvindex": day.get("uvindex"),
            "visibility": day.get("visibility"),
        }
        
        # Add air quality if available
        if "aqi" in day:
            day_data["air_quality"] = {
                "aqi": day.get("aqi"),
                "pm2_5": day.get("pm2p5"),
                "pm10": day.get("pm10"),
                "o3": day.get("o3"),
                "no2": day.get("no2"),
                "so2": day.get("so2"),
                "co": day.get("co"),
            }
        
        # Add astronomy if available
        if "sunrise" in day:
            day_data["astronomy"] = {
                "sunrise": day.get("sunrise"),
                "sunset": day.get("sunset"),
                "moonrise": day.get("moonrise"),
                "moonset": day.get("moonset"),
                "moonphase": day.get("moonphase"),
                "moonillumination": day.get("moonillumination"),
            }
        
        forecast.append(day_data)
    
    # Current conditions
    current_out = {
        "temp": current.get("temp"),
        "feelslike": current.get("feelslike"),
        "conditions": current.get("conditions"),
        "humidity": current.get("humidity"),
        "windspeed": current.get("windspeed"),
        "uvindex": current.get("uvindex"),
    }
    
    if "aqi" in current:
        current_out["air_quality"] = {
            "aqi": current.get("aqi"),
            "pm2_5": current.get("pm2p5"),
            "pm10": current.get("pm10"),
        }
    
    log.info("Visual Crossing: %d days for %s", len(forecast), data.get("resolvedAddress", location))
    
    return {
        "location": data.get("resolvedAddress", location),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone"),
        "source": "visualcrossing",
        "current": current_out,
        "forecast": forecast,
    }
