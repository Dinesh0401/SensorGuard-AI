"""Tests for OpenAQ v3 client: retries, rate-limits, pagination, and timestamp handling."""

from __future__ import annotations

import httpx

from sensorguard.providers import OpenAQClient, parse_openaq_datetime


def test_client_respects_rate_limit_and_retries_on_500():
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["X-API-Key"] == "test-key"
        if calls == 1:
            return httpx.Response(500, json={"message": "Temporary Server Error"})
        return httpx.Response(200, json={"results": [{"value": 25.4}], "meta": {}})

    transport = httpx.MockTransport(mock_handler)
    client = OpenAQClient(
        api_key="test-key",
        transport=transport,
        max_retries=2,
        base_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )
    payload, p_hash, fetched_at = client.get_sensor_hours_raw(sensor_id=101)
    assert calls == 2
    assert payload["results"][0]["value"] == 25.4
    assert len(p_hash) == 64
    assert fetched_at.tzinfo is not None


def test_client_handles_429_with_retry_after():
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "Rate limit exceeded"})
        return httpx.Response(200, json={"results": [{"value": 15.0}], "meta": {}})

    transport = httpx.MockTransport(mock_handler)
    client = OpenAQClient(
        api_key="test-key",
        transport=transport,
        max_retries=2,
        base_backoff_seconds=0.01,
    )
    payload, _, _ = client.get_sensor_hours_raw(sensor_id=102)
    assert calls == 2
    assert payload["results"][0]["value"] == 15.0


def test_client_pagination():
    page_requested = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        page_requested.append(page)
        if page == 1:
            return httpx.Response(200, json={"results": [{"value": 10}, {"value": 11}]})
        elif page == 2:
            return httpx.Response(200, json={"results": [{"value": 12}]})
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(mock_handler)
    client = OpenAQClient(api_key="test-key", transport=transport)
    pages = list(client.paginate_sensor_hours(sensor_id=55, page_size=2, max_pages=5))

    assert len(pages) == 2  # stops when page has fewer results than page_size
    assert page_requested == [1, 2]


def test_parse_openaq_datetime_variations():
    # String ISO with Z
    dt1 = parse_openaq_datetime("2025-01-15T08:00:00Z")
    assert dt1 is not None
    assert dt1.hour == 8

    # Dict format {"utc": "..."}
    dt2 = parse_openaq_datetime({"utc": "2025-01-15T09:00:00+00:00", "local": "2025-01-15T04:00:00-05:00"})
    assert dt2 is not None
    assert dt2.hour == 9

    # None or malformed
    assert parse_openaq_datetime(None) is None
    assert parse_openaq_datetime("invalid-date-string") is None
