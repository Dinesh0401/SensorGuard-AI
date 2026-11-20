"""OpenAQ credential and HTTP contract tests with an offline fake transport."""

import httpx
import pytest

from sensorguard.providers import OpenAQClient


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAQ_API_KEY"):
        OpenAQClient()


def test_reads_bounded_sensor_page_without_network():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "test-secret"
        assert request.url.path == "/v3/sensors/42/hours"
        return httpx.Response(200, json={"results": [{"value": 12}], "meta": {}})

    client = OpenAQClient(api_key="test-secret", transport=httpx.MockTransport(handler))
    assert client.sensor_hours(42)["results"][0]["value"] == 12
