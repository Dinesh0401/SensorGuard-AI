"""Network-level concurrent latency and throughput benchmark for SensorGuard AI FastAPI.

Spawns a real Uvicorn ASGI server on a local TCP socket, launches concurrent async
HTTP client pools (concurrency levels: 1, 5, 10, 20) with 100+ requests per test,
and computes socket-level p50, p95, p99 latency percentiles, request throughput (req/s),
and error rates.

Endpoints evaluated:
- /health (liveness and DB connection check)
- /api/v1/stations (station catalog & availability)
- /api/v1/stations/1/observations (time series retrieval)
- /api/v1/stations/1/forecasts (SAGE point & interval forecasts)

Outputs summary JSON to docs/audit/network_benchmark_results.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sensorguard.benchmark")

CONCURRENCY_LEVELS = [1, 5, 10, 20]
DEFAULT_REQUESTS = 100
ENDPOINTS = [
    ("/health", "Health Liveness & DB Check"),
    ("/api/v1/stations", "Station Catalog & Metadata"),
    ("/api/v1/stations/1/observations", "Observations Time Series"),
    ("/api/v1/stations/1/forecasts", "SAGE Guarded Forecasts"),
]


def find_available_port(default_port: int = 8000) -> int:
    """Find an open TCP port on localhost."""
    for port in range(default_port, default_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return default_port


async def wait_for_server(base_url: str, timeout_seconds: float = 15.0) -> bool:
    """Poll health endpoint until server responds or timeout expires."""
    url = f"{base_url}/health"
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return True
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, httpx.HTTPError, OSError):
            pass
        await asyncio.sleep(0.4)
    return False


async def run_concurrent_tier(
    client: httpx.AsyncClient,
    url: str,
    concurrency: int,
    total_requests: int,
) -> dict[str, Any]:
    """Execute total_requests using an asyncio Semaphore to enforce max concurrency."""
    sem = asyncio.Semaphore(concurrency)
    latencies_ms: list[float] = []
    error_count = 0

    async def single_request() -> tuple[float, int, bool]:
        nonlocal error_count
        async with sem:
            t0 = time.perf_counter()
            try:
                resp = await client.get(url)
                duration_ms = (time.perf_counter() - t0) * 1000.0
                is_ok = 200 <= resp.status_code < 400
                if not is_ok:
                    error_count += 1
                return duration_ms, resp.status_code, is_ok
            except Exception:  # noqa: BLE001
                duration_ms = (time.perf_counter() - t0) * 1000.0
                error_count += 1
                return duration_ms, 500, False

    # Warmup
    for _ in range(min(5, concurrency)):
        try:
            await client.get(url)
        except Exception:  # noqa: BLE001, S110
            pass

    wall_start = time.perf_counter()
    tasks = [single_request() for _ in range(total_requests)]
    results = await asyncio.gather(*tasks)
    wall_duration = time.perf_counter() - wall_start

    for lat, _, _ in results:
        latencies_ms.append(lat)

    arr = np.array(latencies_ms)
    throughput = len(latencies_ms) / wall_duration if wall_duration > 0 else 0.0
    error_rate = error_count / len(latencies_ms) if latencies_ms else 0.0

    return {
        "concurrency": concurrency,
        "total_requests": len(latencies_ms),
        "wall_duration_seconds": round(wall_duration, 3),
        "throughput_req_per_sec": round(throughput, 2),
        "error_rate": round(error_rate, 4),
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "p90_ms": round(float(np.percentile(arr, 90)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "p99_ms": round(float(np.percentile(arr, 99)), 2),
        "mean_ms": round(float(np.mean(arr)), 2),
        "min_ms": round(float(np.min(arr)), 2),
        "max_ms": round(float(np.max(arr)), 2),
    }


async def benchmark_all_endpoints(
    base_url: str,
    concurrency_levels: list[int],
    requests_per_tier: int,
) -> dict[str, Any]:
    """Run concurrent load test matrix across all candidate endpoints."""
    results: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "requests_per_tier": requests_per_tier,
        "concurrency_levels": concurrency_levels,
        "endpoints": {},
    }

    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=15.0) as client:
        for path, label in ENDPOINTS:
            logger.info("Benchmarking endpoint: %s (%s)...", path, label)
            endpoint_results = {
                "label": label,
                "path": path,
                "tiers": {},
            }
            for c in concurrency_levels:
                tier_stats = await run_concurrent_tier(
                    client=client,
                    url=path,
                    concurrency=c,
                    total_requests=requests_per_tier,
                )
                endpoint_results["tiers"][str(c)] = tier_stats
                logger.info(
                    "  [c=%2d] p50=%6.2fms | p95=%6.2fms | p99=%6.2fms | %.1f req/s | err=%.2f%%",
                    c,
                    tier_stats["p50_ms"],
                    tier_stats["p95_ms"],
                    tier_stats["p99_ms"],
                    tier_stats["throughput_req_per_sec"],
                    tier_stats["error_rate"] * 100,
                )
            results["endpoints"][path] = endpoint_results

    return results


def print_summary_table(results: dict[str, Any]) -> None:
    """Render formatted console summary table of benchmark results."""
    print("\n" + "=" * 90)
    print("      SensorGuard AI — Network Socket Concurrent Latency Benchmark (Task E)")
    print("=" * 90)
    print(
        f"{'Endpoint':<34} | {'Conc':<5} | {'p50 (ms)':<9} | {'p95 (ms)':<9} | {'p99 (ms)':<9} | {'Throughput':<11} | {'Errors'}"
    )
    print("-" * 90)

    for path, ep_data in results.get("endpoints", {}).items():
        label = ep_data.get("label", path)
        print(f"[{path}] — {label}")
        for conc_str, stats in ep_data.get("tiers", {}).items():
            conc = int(conc_str)
            p50 = stats["p50_ms"]
            p95 = stats["p95_ms"]
            p99 = stats["p99_ms"]
            tp = f"{stats['throughput_req_per_sec']:.1f} r/s"
            err = f"{stats['error_rate'] * 100:.1f}%"
            print(f"  {'':<32} | {conc:<5} | {p50:<9.2f} | {p95:<9.2f} | {p99:<9.2f} | {tp:<11} | {err}")
        print("-" * 90)
    print("=" * 90)


def main() -> int:
    parser = argparse.ArgumentParser(description="SensorGuard AI Network Concurrency Benchmark")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Localhost port for Uvicorn server (auto-detected if omitted)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Use existing running server at this base URL instead of spawning Uvicorn",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=DEFAULT_REQUESTS,
        help="Requests per concurrency tier (default 100)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("docs/audit/network_benchmark_results.json"),
        help="Output JSON destination path",
    )
    args = parser.parse_args()

    server_process: subprocess.Popen | None = None
    base_url = args.base_url

    if not base_url:
        port = args.port or find_available_port(8000)
        base_url = f"http://127.0.0.1:{port}"
        logger.info("Spawning real Uvicorn server on %s...", base_url)
        env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "--app-dir",
            "src",
            "sensorguard.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        server_process = subprocess.Popen(cmd, env=env)

    try:
        logger.info("Waiting for server readiness on %s/health...", base_url)
        ready = asyncio.run(wait_for_server(base_url, timeout_seconds=15.0))
        if not ready:
            logger.error("Server on %s failed to become ready within timeout", base_url)
            return 1

        logger.info("Server is ready! Running concurrency benchmark matrix...")
        results = asyncio.run(
            benchmark_all_endpoints(
                base_url=base_url,
                concurrency_levels=CONCURRENCY_LEVELS,
                requests_per_tier=args.requests,
            )
        )

        # Write output JSON
        out_path = args.output_json
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        logger.info("Saved network benchmark results to %s", out_path)

        print_summary_table(results)
        return 0

    finally:
        if server_process is not None:
            logger.info("Shutting down background Uvicorn server...")
            server_process.terminate()
            try:
                server_process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                server_process.kill()
            logger.info("Uvicorn server stopped.")


if __name__ == "__main__":
    sys.exit(main())
