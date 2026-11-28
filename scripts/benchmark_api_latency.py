"""Latency and throughput benchmark for SensorGuard AI FastAPI endpoints.

Measures p50, p95, and p99 latency across 100 consecutive requests for:
- /healthz (liveness check)
- /api/v1/stations (station catalog)
- /api/v1/stations/{id}/observations (time series)
- /api/v1/stations/{id}/forecasts (SAGE point & interval forecasts)

Ensures that production endpoints satisfy sub-50ms latency guarantees.
"""

from __future__ import annotations

import time

import numpy as np
from fastapi.testclient import TestClient

from sensorguard.api import app

client = TestClient(app)


def benchmark_endpoint(path: str, n_requests: int = 100) -> dict[str, float]:
    """Execute n requests against an endpoint and measure latency percentiles in milliseconds."""
    # Warmup
    for _ in range(5):
        client.get(path)

    latencies_ms: list[float] = []
    for _ in range(n_requests):
        t0 = time.perf_counter()
        res = client.get(path)
        t1 = time.perf_counter()
        assert res.status_code == 200, f"Request to {path} failed with status {res.status_code}"
        latencies_ms.append((t1 - t0) * 1000.0)

    arr = np.array(latencies_ms)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "mean": float(np.mean(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def main() -> int:
    endpoints = [
        ("/healthz", "Health / Liveness Check"),
        ("/api/v1/stations", "Station Catalog & Metadata"),
        ("/api/v1/stations/1/observations?limit=24", "Station Observations (24h)"),
        ("/api/v1/stations/1/forecasts?limit=24", "SAGE Forecasts (24h)"),
    ]

    print("=========================================================================")
    print("       SensorGuard AI — Private Staging API Latency Benchmark (100 runs)  ")
    print("=========================================================================")
    print(f"{'Endpoint':<42} | {'p50 (ms)':<8} | {'p95 (ms)':<8} | {'p99 (ms)':<8} | {'Status':<6}")
    print("-" * 75)

    all_pass = True
    for path, label in endpoints:
        stats = benchmark_endpoint(path, n_requests=100)
        passes_sub50 = stats["p95"] < 50.0
        if not passes_sub50:
            all_pass = False
        status_str = "PASS" if passes_sub50 else "WARN"
        print(
            f"{label:<42} | {stats['p50']:<8.2f} | {stats['p95']:<8.2f} | {stats['p99']:<8.2f} | {status_str:<6}"
        )

    print("=========================================================================")
    if all_pass:
        print("[OK] Sub-50ms p95 latency invariant SATISFIED across all endpoints.")
        return 0
    else:
        print("[FAIL] One or more endpoints exceeded 50ms p95 latency threshold.")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
