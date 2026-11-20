"""Tests for sensor discovery and coverage audit."""

from __future__ import annotations

import httpx

from sensorguard.audit import audit_candidate_location, audit_sensor_coverage
from sensorguard.providers import OpenAQClient


def test_audit_sensor_coverage_computes_gap_rate():
    # 5 hourly measurements across 5 hours = 0% missing gap rate
    mock_hourly = {
        "results": [{"datetime": {"utc": f"2025-01-01T{h:02d}:00:00Z"}, "value": 10.0 + h} for h in range(5)]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_hourly)

    client = OpenAQClient(api_key="test-key", transport=httpx.MockTransport(mock_handler))
    stats = audit_sensor_coverage(sensor_id=123, client=client)

    assert stats.total_records == 5
    assert stats.hours_spanned == 4.0
    assert stats.cadence_hours == 1.0
    assert stats.missing_gap_rate == 0.0


def test_audit_candidate_location():
    mock_location = {
        "results": [
            {
                "id": 404,
                "name": "Oakland West",
                "coordinates": {"latitude": 37.8, "longitude": -122.2},
                "country": {"code": "US"},
                "sensors": [
                    {"id": 1001, "parameter": {"name": "pm25", "units": "µg/m³"}},
                    {"id": 1002, "parameter": {"name": "o3", "units": "ppm"}},
                ],
            }
        ]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "/v3/locations/404" in request.url.path:
            return httpx.Response(200, json=mock_location)
        # Mock empty hourly results for sensor
        return httpx.Response(200, json={"results": []})

    client = OpenAQClient(api_key="test-key", transport=httpx.MockTransport(mock_handler))
    report = audit_candidate_location(location_id=404, client=client)

    assert report.location_id == 404
    assert report.station_name == "Oakland West"
    assert report.pm25_sensor_count == 1
    assert len(report.sensors) == 1
