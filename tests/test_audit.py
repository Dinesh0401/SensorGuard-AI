"""Tests for sensor discovery, paginated window coverage audit, and coordinate validation."""

from __future__ import annotations

import httpx
import pytest

from sensorguard.audit import (
    audit_candidate_location,
    audit_network_feasibility,
    audit_sensor_coverage,
)
from sensorguard.providers import OpenAQClient


def test_audit_sensor_coverage_paginated_window():
    mock_hourly = {
        "results": [{"datetime": {"utc": f"2025-01-01T{h:02d}:00:00Z"}, "value": 10.0 + h} for h in range(5)]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_hourly)

    client = OpenAQClient(api_key="test-key", transport=httpx.MockTransport(mock_handler))
    stats = audit_sensor_coverage(
        sensor_id=123,
        client=client,
        datetime_from="2025-01-01T00:00:00Z",
        datetime_to="2025-01-01T04:00:00Z",
    )

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
        return httpx.Response(200, json={"results": []})

    client = OpenAQClient(api_key="test-key", transport=httpx.MockTransport(mock_handler))
    report = audit_candidate_location(location_id=404, client=client)

    assert report.location_id == 404
    assert report.station_name == "Oakland West"
    assert report.latitude == 37.8
    assert report.longitude == -122.2
    assert report.pm25_sensor_count == 1
    assert len(report.sensors) == 1


def test_audit_candidate_location_rejects_invalid_coords():
    mock_bad_location = {
        "results": [
            {
                "id": 505,
                "name": "Null Island",
                "coordinates": {"latitude": 0.0, "longitude": 0.0},
                "sensors": [],
            }
        ]
    }

    client = OpenAQClient(
        api_key="test-key",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=mock_bad_location)),
    )
    with pytest.raises(ValueError, match="null island"):
        audit_candidate_location(location_id=505, client=client)


def test_audit_network_feasibility_dynamic_decision():
    # 8 locations, each with 1 PM2.5 sensor reporting at all 5 hourly intervals
    def mock_network_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/v3/locations/" in url_str:
            loc_id = int(url_str.split("/v3/locations/")[1].split("?")[0])
            loc_data = {
                "results": [
                    {
                        "id": loc_id,
                        "name": f"Station-{loc_id}",
                        "coordinates": {
                            "latitude": 51.5 + (loc_id * 0.01),
                            "longitude": -0.1 + (loc_id * 0.01),
                        },
                        "country": {"code": "GB"},
                        "sensors": [{"id": 1000 + loc_id, "parameter": {"name": "pm25", "units": "µg/m³"}}],
                    }
                ]
            }
            return httpx.Response(200, json=loc_data)
        if "/v3/sensors/" in url_str and "/hours" in url_str:
            # Return 5 continuous hourly observations
            records = [
                {
                    "datetime": {"utc": f"2025-01-01T{h:02d}:00:00Z"},
                    "value": 15.0,
                    "parameter": {"name": "pm25", "units": "µg/m³"},
                }
                for h in range(5)
            ]
            return httpx.Response(200, json={"results": records})
        return httpx.Response(200, json={"results": []})

    client = OpenAQClient(api_key="test-key", transport=httpx.MockTransport(mock_network_handler))

    report = audit_network_feasibility(
        location_ids=list(range(1, 9)),  # 8 stations
        client=client,
        datetime_from="2025-01-01T00:00:00Z",
        datetime_to="2025-01-01T04:00:00Z",
        min_eligible_stations=8,
        min_overlap_4plus_pct=0.60,
    )

    assert report.total_audited_stations == 8
    assert report.eligible_stations_count == 8
    assert report.total_expected_hours == 5
    assert report.overlap_hours_4plus == 5
    assert report.overlap_pct_4plus == 1.0
    assert report.selection_decision == "APPROVED"
    assert "Approved: 8/8 stations meet" in report.decision_rationale

    # Test rejection when station threshold is not met (e.g., requires 9 stations)
    rejected_report = audit_network_feasibility(
        location_ids=list(range(1, 9)),
        client=client,
        datetime_from="2025-01-01T00:00:00Z",
        datetime_to="2025-01-01T04:00:00Z",
        min_eligible_stations=9,  # Only 8 available -> MUST REJECT!
    )
    assert rejected_report.selection_decision == "REJECTED"
    assert "Rejected: 8/8 stations meet criteria (minimum 9 required)" in rejected_report.decision_rationale
