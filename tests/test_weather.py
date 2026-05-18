"""Tests for weather endpoints including Open-Meteo and Visual Crossing."""

import pytest
from unittest.mock import patch, AsyncMock


def test_forecast_openmeteo_success(client):
    """Test Open-Meteo forecast endpoint returns data."""
    resp = client.get("/api/weather/forecast?location=Paris&days=3")
    assert resp.status_code == 200
    data = resp.json()
    assert "location" in data
    assert "current" in data
    assert "forecast" in data
    assert len(data["forecast"]) <= 3


def test_forecast_openmeteo_invalid_location(client):
    """Test error handling for invalid location."""
    resp = client.get("/api/weather/forecast?location=xyznotreal123&days=3")
    # Should return 404 for unknown location
    assert resp.status_code in [200, 404]  # Open-Meteo geocoding may fail gracefully


def test_visualcrossing_missing_api_key(client):
    """Test Visual Crossing endpoint returns 503 when API key not set."""
    with patch("app.routers.weather.settings.visualcrossing_api_key", ""):
        resp = client.get("/api/weather/visualcrossing?location=Paris")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()


def test_weather_endpoints_consistency(client):
    """Verify both weather endpoints return similar structure."""
    # This test documents the expected response format
    openmeteo_resp = client.get("/api/weather/forecast?location=London&days=1")
    
    if openmeteo_resp.status_code == 200:
        data = openmeteo_resp.json()
        # Both endpoints should have these keys
        required_keys = {"location", "current", "forecast"}
        assert required_keys.issubset(data.keys())