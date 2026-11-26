# SensorGuard AI — Exact Provider Request Budget & Fail-Closed Rate Limiting (Phase 8.7)

**Modules:** [`src/sensorguard/providers.py`](../src/sensorguard/providers.py) · [`src/sensorguard/pilot_monitor.py`](../src/sensorguard/pilot_monitor.py) · [`src/sensorguard/api.py`](../src/sensorguard/api.py)  
**Test Evidence:** [`tests/test_pilot_monitor.py`](../tests/test_pilot_monitor.py) · [`tests/test_milestone8_api.py`](../tests/test_milestone8_api.py) · [`tests/test_live_pilot_dry_run.py`](../tests/test_live_pilot_dry_run.py)

---

## 1. Per-HTTP-Attempt Durable Quota Reservation

Inside [`OpenAQClient._execute_request`](../src/sensorguard/providers.py), `attempt_guard(method, endpoint, attempt)` fires in an isolated short SQL transaction **before every wire attempt**:
- Retries (`max_retries_per_request`), HTTP `429`, HTTP `5xx`, timeouts (`ReadTimeout`), network errors, and outer transaction rollbacks all permanently consume 1 unit from `pilot_quota_states`.
- When budget reaches 0 mid-retry, [`QuotaExhaustedError`](../src/sensorguard/providers.py) aborts before the next socket opens (`status = "QUOTA_EXHAUSTED"`) and persists exact `http_attempts` into `pilot_ingest_audit_runs`.

---

## 2. Worst-Case Request Budget Calculation (`max_requests_per_day = 48`)

| Target Scope | Active PM2.5 Sensors | Best-Case / Poll | Worst-Case / Poll (`retries=1`) | Max Safe Polls / UTC Day (`48/day` Cap) | Recommended Cadence |
|---|---:|---:|---:|---:|---|
| **One-Time Pre-Flight Location Check** *(Stage 1 candidate)* | `8` loc metadata calls | `8` req | `16` req | **1 manual pre-flight probe** | Single bounded check |
| **Verified LAQN Active Pair** (`154`, `157`) *(Stage 2 candidate)* | `2` | `2` req | `4` req | **`12` polls/day** worst-case (`24` best-case) | Every **2h** (100% retry-safe) or **1h** best-case |
| **Full 8-Station UK Subset** *(only if all 8 active)* | `8` | `8` req | `16` req | **`3` polls/day** worst-case (`6` best-case) | Every **8h** (worst-case safe) |

---

## 3. Fail-Closed Shared Server Rate Limiting (`_enforce_server_rate_limit`)

[`_enforce_server_rate_limit`](../src/sensorguard/api.py) backs FastAPI endpoints (`admin_api` `30/min`, `pilot_readiness_api` `120/min`, `forecast_api` `600/min`) via durable multi-worker rows in `pilot_api_rate_limits(scope, window_minute_utc)`:
- **Zero In-Memory Fallback in Production / Staging / PostgreSQL:** On any `SQLAlchemyError` (DB connection refused, timeout, or missing `pilot_api_rate_limits` table), endpoints fail closed with **`HTTP 503 Service Unavailable`** (`Retry-After: 10`).
- **Explicit Isolated Test Gate Only:** Process-local fallback `_FALLBACK_RATE_WINDOWS` is reachable **only** when ALL three hold simultaneously: (1) database dialect is `sqlite` (never `postgresql`), (2) `SENSORGUARD_ALLOW_IN_MEMORY_RATE_LIMIT_DEV=true`, AND (3) `SENSORGUARD_ENV` is `development` / `dev_test` / `test`.
- **Excess Requests:** Enforce shared global cap across all API worker processes and return **`HTTP 429 Too Many Requests`** (`Retry-After: 60`).
