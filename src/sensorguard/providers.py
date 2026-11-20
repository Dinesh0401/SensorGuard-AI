"""Read-only OpenAQ v3 adapter; no credential required for synthetic mode."""

from __future__ import annotations

import os

import httpx


class OpenAQClient:
    """Thin HTTP client preserving upstream payloads for subsequent validation.

    Never log or display the API key. Provider availability, terms, and station
    coverage must be audited before enabling a real-data pipeline.
    """

    def __init__(self, api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self._api_key = api_key or os.getenv("OPENAQ_API_KEY")
        if not self._api_key:
            raise ValueError("OPENAQ_API_KEY required for live source access")
        self._transport = transport

    def sensor_hours(self, sensor_id: int, limit: int = 100) -> dict:
        """Fetch a bounded page of hourly measurements for a known sensor ID."""
        if sensor_id < 1 or not 1 <= limit <= 1000:
            raise ValueError("sensor_id must be positive; limit must be 1..1000")
        with httpx.Client(base_url="https://api.openaq.org", timeout=15, transport=self._transport) as client:
            response = client.get(
                f"/v3/sensors/{sensor_id}/hours",
                params={"limit": limit},
                headers={"X-API-Key": self._api_key},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("unexpected OpenAQ response schema")
            return payload
