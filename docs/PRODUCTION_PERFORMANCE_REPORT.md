# SensorGuard AI — Production Performance & Concurrency Report

**Milestone:** 7.2 — Bottleneck Profiling, Inference Caching & Multi-Worker Evaluation  
**Date:** 2026-11-25  
**Hardware:** `AMD64`, `16` logical CPUs, `Windows-11-10.0.26300-SP0`, local `PostgreSQL 18.0` (`127.0.0.1:5433`, SQLAlchemy `pool_size=20`, `max_overflow=10`)  
**Benchmark Tool:** [`scripts/benchmark_network_concurrency.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/benchmark_network_concurrency.py) (`100` live HTTP requests per concurrency tier)  

---

## 1. Root-Cause Profiling Before Optimization

Profiling `GET /api/v1/stations/1/forecasts?limit=24` (`100` direct in-process calls + `cProfile` on PostgreSQL 18 baseline) established a mean execution cost of **`26.61 ms/call`** (`35.4 req/s` HTTP `c=1`, degrading to **`1236.79 ms` p95** at `c=20`), broken down across four sequential per-request operations:

| Stage in `get_station_forecasts` (Baseline `315e75b`) | Measured Cost per Request | Root Cause |
|---|---:|---|
| Pydantic model instantiation + response serialization (`145` objects) | `~15.07 ms` | Rebuilding `24 × 5` `ForecastScenarioBounds` + `24` `ForecastRecord` models per HTTP GET |
| `SAGEForecaster.predict_guarded(st_df)` (`n_jobs=-1`) | `7.77 ms` (`4.94 ms` w/ `n_jobs=1`) | OpenMP threadpool spin-up on 24 rows + sklearn `SimpleImputer`/`StandardScaler` inspect overhead |
| Full-matrix pandas string equality scan (`comp_method_OBJECT_ARRAY`) | `2.70 ms` | Scanning & sorting all `~17k` rows of `_CACHED_FEATURE_DF` per call |
| Synchronous SQLAlchemy DB lookup (`db.get(Station, 1)`) + AnyIO threadpool hop | `1.07 ms` (+ threadpool dispatch) | Blocking event-loop-adjacent threadpool handoff even when catalog/features are warm |

---

## 2. Implemented Optimizations ([src/sensorguard/api.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/api.py))

1. **Per-Station Pre-Indexed Feature Slices (`_CACHED_STATION_SLICES`) & Grouped SQL Latest Query:** Replaced the per-request full-DataFrame equality scan/sort and full-table `order_by` observation query with a one-time grouped SQL `MAX(observed_at)` lookup and pre-sorted per-station DataFrames under `_CACHE_LOCK`.
2. **Single-Thread Online Inference (`n_jobs=1`):** Configured in-memory `LGBMRegressor` inference with `n_jobs=1` immediately after SHA-256 verification (preserving disk artifact digest `e552ca09...` untouched). Reduced standalone `predict_guarded` on 24 rows to **`p50 = 5.77 ms`, `p95 = 9.39 ms`**, and uncached HTTP `/forecasts` (cache cleared before every request) to **`p50 = 17.03 ms`, `p95 = 17.97 ms`**.
3. **Async Fast Path with Pre-Serialized JSON Cache (`_CACHED_FORECAST_JSON`) & Cache-Miss Threadpool Offload:** Preserves fail-closed `get_serving_model()` (`503`) first, skips DB checkout and AnyIO threadpool overhead on warm cache hits, offloads cold misses via `run_in_threadpool`, and invalidates atomically inside `invalidate_pilot_feature_cache()`.
4. **Worker Startup `lifespan` Pre-Warming:** Eliminated lazy worker cold-start stalls (`3,212.88 ms` p99 spike and `1.0%` HTTP timeout seen on `/api/v1/stations` during initial 4-worker testing prior to `lifespan` warmup).

---

## 3. Live Socket Latency & Throughput Comparison (PostgreSQL 18.0)

### 3.1 `/api/v1/stations/1/forecasts` Across Configurations (`100` reqs/tier)

| Configuration | Conc (`c`) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput | Error Rate | Audit JSON |
|---|---:|---:|---:|---:|---:|---:|---|
| **Baseline (1 Worker, PG18)** | `1` | 27.67 | 32.84 | 35.66 | 35.4 r/s | 0.0% | [`pg18_baseline_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_baseline_benchmark.json) |
| **Baseline (1 Worker, PG18)** | `5` | 120.50 | 169.79 | 184.92 | 40.8 r/s | 0.0% | [`pg18_baseline_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_baseline_benchmark.json) |
| **Baseline (1 Worker, PG18)** | `10` | 217.27 | 512.71 | 615.93 | 38.1 r/s | 0.0% | [`pg18_baseline_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_baseline_benchmark.json) |
| **Baseline (1 Worker, PG18)** | `20` | 472.91 | **1236.79** | 1664.45 | **34.3 r/s** | 0.0% | [`pg18_baseline_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_baseline_benchmark.json) |
| **Optimized (1 Worker, Warm Async Cache)** | `1` | 5.32 | **5.90** | 6.09 | **184.3 r/s** | 0.0% | [`pg18_optimized_1w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_1w_v2_benchmark.json) |
| **Optimized (1 Worker, Warm Async Cache)** | `5` | 26.56 | 53.01 | 57.86 | 143.7 r/s | 0.0% | [`pg18_optimized_1w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_1w_v2_benchmark.json) |
| **Optimized (1 Worker, Warm Async Cache)** | `10` | 77.93 | 201.70 | 211.61 | 109.4 r/s | 0.0% | [`pg18_optimized_1w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_1w_v2_benchmark.json) |
| **Optimized (1 Worker, Warm Async Cache)** | `20` | 134.84 | **364.39** | 477.34 | **115.9 r/s** | 0.0% | [`pg18_optimized_1w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_1w_v2_benchmark.json) |
| **Optimized (1 Worker, Warm Async Cache)** | `40` | 231.66 | 718.60 | 779.85 | 115.7 r/s | 0.0% | [`pg18_optimized_1w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_1w_v2_benchmark.json) |
| **Optimized (4 Workers + Lifespan Warm)** | `1` | 5.41 | 6.15 | 6.70 | 179.1 r/s | 0.0% | [`pg18_optimized_4w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_4w_v2_benchmark.json) |
| **Optimized (4 Workers + Lifespan Warm)** | `20` | 124.06 | **362.33** | 490.29 | **119.8 r/s** | 0.0% | [`pg18_optimized_4w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_4w_v2_benchmark.json) |
| **Optimized (4 Workers + Lifespan Warm)** | `40` | 254.71 | 697.21 | 786.71 | 118.2 r/s | 0.0% | [`pg18_optimized_4w_v2_benchmark.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/pg18_optimized_4w_v2_benchmark.json) |

### 3.2 All Four Endpoints Under Final 4-Worker Lifespan Configuration (`pg18_optimized_4w_v2_benchmark.json`)

| Endpoint | `c=1` p95 | `c=5` p95 | `c=10` p95 | `c=20` p95 | `c=40` p95 | Peak Throughput | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| `/health` | 12.04 ms | 37.53 ms | 171.06 ms | 344.32 ms | 606.31 ms | 190.8 r/s | `0.0%` |
| `/api/v1/stations` | 10.22 ms | 41.63 ms | 134.31 ms | 306.20 ms | 652.74 ms | 169.8 r/s | `0.0%` |
| `/api/v1/stations/1/observations` | 18.65 ms | 28.42 ms | 138.23 ms | 324.98 ms | 660.18 ms | **199.5 r/s** | `0.0%` |
| `/api/v1/stations/1/forecasts` | 6.15 ms | 49.72 ms | 152.03 ms | **362.33 ms** | 697.21 ms | 179.1 r/s | `0.0%` |

---

## 4. Honest Limitations & OS Scheduling Notes

- **Cold-Cache vs Warm-Cache Distinction:** The `c=20` `362–364 ms` p95 numbers apply to warm static-feature forecast serving between ingestion cycles. When caches are cold (`invalidate_pilot_feature_cache()` after an ingestion run), single-station uncached inference measures **`17.97 ms` p95** (`p50 = 17.03 ms`) once features are indexed; full matrix rebuild (`14,556` rows) takes `~3.2s` once per worker and is shifted to worker startup (`lifespan`) and background ingestion boundaries rather than user GETs.
- **Windows vs Linux Multi-Worker Behavior:** Before adding `lifespan` warmup, 4-worker Uvicorn exposed a `3212.88 ms` p99 tail and `1.0%` client timeout on `/api/v1/stations` (`pg18_optimized_4w_benchmark.json`). Even after `lifespan` fixes errors to `0.0%`, Windows Uvicorn multi-worker shares a single loopback listening socket without Linux kernel `SO_REUSEPORT` load balancing, limiting single-host loopback saturation around `~120–200 req/s` across both `1` and `4` workers (`c=40` p95 remains `~606–719 ms`). Production multi-worker scaling requires Linux workers behind Nginx/Envoy and dedicated load testing off-host.
