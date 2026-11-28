# SensorGuard AI — Staging End-to-End Test Report
**Milestone 5.2 Comprehensive Integration & Verification**

- **Date:** November 25, 2026
- **Test Environment:** Windows Local Staging / Uvicorn ASGI / SQLite & PostgreSQL Dialects / Next.js 15.3.9
- **Overall Verdict:** **100% Passed (Staging Certified | Production Launch Withheld)**

---

## 1. Test Execution Summary

| Test Domain | Target / Command | Result | Details |
| :--- | :--- | :---: | :--- |
| **API Contract & Integration** | `pytest tests/test_api.py` | **17 / 17 Passed** | Health, catalog, observations, forecasts, reliability, model status, fail-closed 503, cache invalidation. |
| **SAGE & Conformal Uncertainty** | `pytest tests/test_sage.py` | **8 / 8 Passed** | Envelope separation, scenario disagreement, guarded decision rules, feature age abstention. |
| **Scientific Ablation Framework** | `pytest tests/test_ablations.py` | **10 / 10 Passed** | Outage history reconstruction, zero leakage, poisoned feature audit, finite denominator mask. |
| **Baseline Forecasting Models** | `pytest tests/test_baselines.py` | **15 / 15 Passed** | Persistence, Seasonal Naive, Ridge, LightGBM (Point, Rolling, Quantile/CQR, ACI). |
| **Comprehensive Python Suite** | `pytest tests/ -q` | **85 / 85 Passed** | 100% passing across all 85 unit, integration, and property tests in 46.6s. |
| **Code Style & Static Typing** | `python -m ruff check .` | **Clean** | 0 lint errors across all 68 files. |
| **Code Formatting** | `python -m ruff format --check .` | **Clean** | 100% formatted to PEP 8 standard. |
| **Database Migrations** | `alembic upgrade head` | **Success** | Schema migration up to date; PostgreSQL transactional DDL verified via `--sql`. |
| **Network Concurrency Load** | `python scripts/benchmark_network_concurrency.py` | **16 / 16 Tiers Passed** | 1,600 requests over live socket Uvicorn server ($c \in \{1, 5, 10, 20\}$); 0.0% error rate. |
| **Frontend Production Build** | `cd apps/web && npm run build` | **Success** | Next.js 15.3.9 static optimization; 4 routes statically exported with 0 errors. |

---

## 2. Network Concurrency Load Test Breakdown

Evaluated on live localhost TCP socket against a spawned Uvicorn ASGI instance (`100` requests per concurrency tier, `1,600` total requests):

```text
==========================================================================================
      SensorGuard AI — Network Socket Concurrent Latency Benchmark (Task E)
==========================================================================================
Endpoint                           | Conc  | p50 (ms)  | p95 (ms)  | p99 (ms)  | Throughput  | Errors
------------------------------------------------------------------------------------------
[/health] — Health Liveness & DB Check
                                   | 1     | 11.57     | 14.51     | 19.68     | 83.9 r/s    | 0.0%
                                   | 5     | 31.67     | 51.66     | 73.86     | 135.8 r/s   | 0.0%
                                   | 10    | 72.00     | 199.57    | 282.91    | 101.0 r/s   | 0.0%
                                   | 20    | 158.62    | 465.36    | 606.63    | 97.4 r/s    | 0.0%
------------------------------------------------------------------------------------------
[/api/v1/stations] — Station Catalog & Metadata
                                   | 1     | 11.51     | 14.88     | 17.00     | 82.8 r/s    | 0.0%
                                   | 5     | 56.47     | 85.78     | 383.27    | 72.3 r/s    | 0.0%
                                   | 10    | 103.54    | 208.62    | 309.28    | 69.2 r/s    | 0.0%
                                   | 20    | 247.90    | 567.92    | 848.43    | 63.9 r/s    | 0.0%
------------------------------------------------------------------------------------------
[/api/v1/stations/1/observations] — Observations Time Series
                                   | 1     | 22.84     | 33.56     | 45.48     | 39.8 r/s    | 0.0%
                                   | 5     | 64.90     | 83.44     | 84.98     | 74.8 r/s    | 0.0%
                                   | 10    | 160.63    | 191.44    | 214.87    | 61.5 r/s    | 0.0%
                                   | 20    | 319.32    | 509.48    | 728.28    | 60.4 r/s    | 0.0%
------------------------------------------------------------------------------------------
[/api/v1/stations/1/forecasts] — SAGE Guarded Forecasts
                                   | 1     | 30.19     | 34.67     | 42.14     | 32.6 r/s    | 0.0%
                                   | 5     | 130.61    | 172.07    | 215.15    | 37.0 r/s    | 0.0%
                                   | 10    | 321.54    | 635.80    | 891.77    | 28.3 r/s    | 0.0%
                                   | 20    | 618.14    | 1360.47   | 2010.08   | 27.0 r/s    | 0.0%
------------------------------------------------------------------------------------------
==========================================================================================
```

---

## 3. Fail-Closed Serving Verification

Automated tests in [tests/test_api.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_api.py) explicitly test the fail-closed invariant:
1. `test_fail_closed_model_artifact_missing_or_corrupted`:
   - Simulates model file deletion: API returns HTTP 503 (`status_code == 503`).
   - Simulates SHA-256 tampering: API detects digest mismatch, refuses deserialization, and returns HTTP 503.
   - Verifies that zero CPU training cycles or background model fitting occur on the request path.
2. `test_cache_invalidation_on_ingestion`:
   - Verifies that calling `admin_ingest()` or `poll_once()` immediately invalidates `_PILOT_DF_CACHE`, ensuring newly ingested observations are reflected in subsequent forecast inferences.

---

## 4. Operational Sign-Off & Next Steps
- **Staging Verification:** Complete and certified.
- **Production Status:** Withheld until multi-region longitudinal validation and external peer review are performed.
