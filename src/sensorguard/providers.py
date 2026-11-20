"""Production-grade OpenAQ v3 client with rate-limiting, retries, and pagination.

Implements secure API key configuration via server-side headers, bounded exponential
backoff with jitter, token bucket rate control, and end-of-period timestamp normalization.
Credentials are never logged or exposed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, NamedTuple

import httpx

logger = logging.getLogger(__name__)

OPENAQ_API_BASE = "https://api.openaq.org"


class RateLimiter:
    """Thread-safe token bucket rate limiter for external provider calls."""

    def __init__(self, rate: float = 2.0, capacity: float = 5.0) -> None:
        """Initialize the rate limiter.

        Args:
            rate: Token generation rate per second.
            capacity: Maximum burst capacity of the token bucket.
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()

    def acquire(self) -> None:
        """Block until a token is available in the bucket."""
        while True:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            sleep_time = (1.0 - self.tokens) / self.rate
            time.sleep(max(0.01, sleep_time))


class ApiResponse(NamedTuple):
    """Container for API response data, cryptographic checksum, and timing metadata.

    Distinguishes retrieval time (when HTTP response bytes arrived over the network)
    from downstream database availability (when transaction commits and writes to disk).
    """

    payload: dict[str, Any]
    response_hash: str
    fetched_at: datetime
    raw_text: str


class OpenAQClient:
    """Resilient HTTP client for OpenAQ v3 REST API.

    Features:
    - Bounded exponential backoff with jitter on HTTP 429 and 5xx errors.
    - Honors upstream 'Retry-After' headers.
    - Token-bucket rate limiting to stay well within provider limits.
    - Deterministic SHA-256 payload hashing for audit and provenance.
    - Explicit separation of observation and publication timestamps.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = OPENAQ_API_BASE,
        transport: httpx.BaseTransport | None = None,
        rate_limit_per_second: float = 2.0,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        base_backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 10.0,
    ) -> None:
        """Initialize the client.

        Args:
            api_key: OpenAQ v3 API key. If omitted, loaded from OPENAQ_API_KEY env var.
            base_url: Base URL of the API.
            transport: Custom HTTPX transport (useful for offline mock testing).
            rate_limit_per_second: Rate limit in requests/sec.
            timeout_seconds: Request timeout in seconds.
            max_retries: Maximum number of retry attempts for retryable errors.
            base_backoff_seconds: Initial backoff duration in seconds.
            max_backoff_seconds: Upper cap on backoff duration.
        """
        resolved_key = api_key or os.getenv("OPENAQ_API_KEY")
        if not resolved_key:
            raise ValueError("OPENAQ_API_KEY required for live source access")
        self._api_key = resolved_key
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.base_backoff = base_backoff_seconds
        self.max_backoff = max_backoff_seconds
        self._rate_limiter = RateLimiter(rate=rate_limit_per_second, capacity=5.0)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Accept": "application/json",
            "User-Agent": "SensorGuard-AI/0.2 (Research MLOps)",
        }

    def _execute_request(
        self, method: str, endpoint: str, params: dict[str, Any] | None = None
    ) -> ApiResponse:
        """Execute an HTTP request with rate limiting and exponential backoff retry.

        Returns:
            ApiResponse with parsed payload, response sha256, network receipt fetched_at, and raw_text.
        """
        last_exception: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._rate_limiter.acquire()
            try:
                with httpx.Client(
                    base_url=self.base_url, timeout=self.timeout, transport=self._transport
                ) as client:
                    response = client.request(
                        method=method,
                        url=endpoint,
                        params=params,
                        headers=self._headers(),
                    )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else min(self.max_backoff, self.base_backoff * (2**attempt))
                        + random.uniform(0.1, 0.5)
                    )
                    if attempt < self.max_retries:
                        time.sleep(delay)
                        continue
                    response.raise_for_status()

                if response.status_code in {500, 502, 503, 504}:
                    delay = min(self.max_backoff, self.base_backoff * (2**attempt)) + random.uniform(0.1, 0.5)
                    if attempt < self.max_retries:
                        time.sleep(delay)
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                # Record receipt timestamp when bytes have successfully arrived over the network:
                fetched_at = datetime.now(UTC)
                raw_text = response.text
                payload_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                payload = response.json()

                if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                    raise TypeError("unexpected OpenAQ response schema")

                return ApiResponse(
                    payload=payload, response_hash=payload_hash, fetched_at=fetched_at, raw_text=raw_text
                )

            except (httpx.TimeoutException, httpx.NetworkError) as err:
                last_exception = err
                if attempt < self.max_retries:
                    delay = min(self.max_backoff, self.base_backoff * (2**attempt)) + random.uniform(0.1, 0.5)
                    time.sleep(delay)
                    continue
                raise

        if last_exception:
            raise last_exception
        raise RuntimeError("Request failed after maximum retries")

    def sensor_hours(self, sensor_id: int, limit: int = 100) -> dict:
        """Fetch a bounded page of hourly measurements (backwards compatibility)."""
        resp = self.get_sensor_hours_raw(sensor_id, limit=limit)
        return resp.payload

    def get_sensor_hours_raw(
        self,
        sensor_id: int,
        datetime_from: str | None = None,
        datetime_to: str | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> ApiResponse:
        """Fetch raw hourly measurements page from /v3/sensors/{id}/hours with hash and timestamp."""
        if sensor_id < 1 or not 1 <= limit <= 1000 or page < 1:
            raise ValueError("sensor_id must be >= 1; limit 1..1000; page >= 1")

        params: dict[str, Any] = {"limit": limit, "page": page}
        if datetime_from:
            params["datetime_from"] = datetime_from
        if datetime_to:
            params["datetime_to"] = datetime_to

        return self._execute_request("GET", f"/v3/sensors/{sensor_id}/hours", params=params)

    def get_locations(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        countries_id: int | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> ApiResponse:
        """Query monitoring locations/stations catalog.

        Args:
            bbox: Optional bounding box (min_lon, min_lat, max_lon, max_lat).
            countries_id: Optional country identifier filter.
            limit: Page size (1..1000).
            page: Page number.
        """
        params: dict[str, Any] = {"limit": limit, "page": page}
        if bbox:
            params["bbox"] = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
        if countries_id is not None:
            params["countries_id"] = countries_id

        return self._execute_request("GET", "/v3/locations", params=params)

    def get_location(self, location_id: int) -> ApiResponse:
        """Fetch detail and attached sensors for a single monitoring location."""
        if location_id < 1:
            raise ValueError("location_id must be positive")
        return self._execute_request("GET", f"/v3/locations/{location_id}")

    def paginate_sensor_hours(
        self,
        sensor_id: int,
        datetime_from: str | None = None,
        datetime_to: str | None = None,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> Iterator[ApiResponse]:
        """Yield pages of sensor hours up to max_pages.

        Yields:
            ApiResponse with payload, hash, fetched_at, and raw_text.
        """
        for page in range(1, max_pages + 1):
            resp = self.get_sensor_hours_raw(
                sensor_id=sensor_id,
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                limit=page_size,
                page=page,
            )
            results = resp.payload.get("results", [])
            if not results:
                break
            yield resp
            if len(results) < page_size:
                break


def parse_openaq_datetime(dt_field: Any) -> datetime | None:
    """Safely parse OpenAQ date string or date object into UTC datetime.

    OpenAQ v3 can return either an ISO string or a nested dict with 'utc' key.
    """
    if dt_field is None:
        return None
    raw_str: str | None = None
    if isinstance(dt_field, dict):
        raw_str = dt_field.get("utc")
    elif isinstance(dt_field, str):
        raw_str = dt_field
    if not raw_str:
        return None
    try:
        dt = datetime.fromisoformat(raw_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None
