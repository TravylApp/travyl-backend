"""Budget estimation using World Bank PPP price-level ratios.

Computes daily_estimate_usd = base_rate_usd * price_level_ratio.
The price level ratio is relative to the US (US ≈ 1.0).
"""

import logging

import aiohttp

from app.config import settings

log = logging.getLogger(__name__)

_WB_PPP_URL = "https://api.worldbank.org/v2/country/{iso2}/indicator/PA.NUS.PPPC.RF"
_WB_COUNTRIES_URL = "https://api.worldbank.org/v2/country"

# in-memory caches — populated on first call, live for process lifetime
_country_to_iso2: dict[str, str] | None = None
_ppp_cache: dict[str, float] = {}

_BASE_RATES = {
    "budget": "budget_base_usd",
    "moderate": "moderate_base_usd",
    "comfortable": "comfortable_base_usd",
    "luxury": "luxury_base_usd",
}


async def _load_country_map() -> dict[str, str]:
    """Fetch World Bank country list and build {name_lower: iso2} mapping."""
    global _country_to_iso2
    if _country_to_iso2 is not None:
        return _country_to_iso2

    mapping: dict[str, str] = {}
    try:
        async with aiohttp.ClientSession() as session:
            params = {"format": "json", "per_page": "400"}
            async with session.get(_WB_COUNTRIES_URL, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

        for entry in data[1]:
            iso2 = entry.get("iso2Code", "")
            name = entry.get("name", "")
            if len(iso2) == 2 and name:
                mapping[name.lower()] = iso2
    except Exception as e:
        log.warning("Failed to load World Bank country list: %s", e)

    # common aliases the API doesn't include
    mapping.setdefault("usa", "US")
    mapping.setdefault("united states", "US")
    mapping.setdefault("uk", "GB")
    mapping.setdefault("england", "GB")
    mapping.setdefault("south korea", "KR")
    mapping.setdefault("russia", "RU")

    _country_to_iso2 = mapping
    log.info("Loaded %d country→ISO2 mappings", len(mapping))
    return mapping


async def _fetch_ppp(iso2: str) -> float | None:
    """Fetch price level ratio for a country. Returns None on failure."""
    if iso2 in _ppp_cache:
        return _ppp_cache[iso2]

    try:
        url = _WB_PPP_URL.format(iso2=iso2)
        async with aiohttp.ClientSession() as session:
            params = {"format": "json", "mrv": "5", "per_page": "5"}
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

        # take first non-null value
        for entry in data[1]:
            val = entry.get("value")
            if val is not None:
                _ppp_cache[iso2] = float(val)
                return float(val)
    except Exception as e:
        log.warning("World Bank PPP fetch failed for %s: %s", iso2, e)

    return None


async def estimate_daily_budget(country: str, budget_level: str | None) -> int:
    """Compute cost-of-living-adjusted daily budget estimate in USD.

    Returns 0 if budget_level is not set or data is unavailable.
    """
    if not budget_level:
        return 0

    attr = _BASE_RATES.get(budget_level)
    if not attr:
        return 0
    base_rate = getattr(settings, attr, 100)

    country_map = await _load_country_map()
    iso2 = country_map.get(country.lower())
    if not iso2:
        # fallback: use base rate without adjustment
        log.info("No ISO2 for '%s', using unadjusted base rate", country)
        return base_rate

    ppp = await _fetch_ppp(iso2)
    if ppp is None:
        return base_rate

    adjusted = round(base_rate * ppp)
    log.info("Budget: %s @ %s = $%d/day (PPP %.2f for %s)", budget_level, country, adjusted, ppp, iso2)
    return adjusted
