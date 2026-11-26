# SensorGuard AI — Exact Provider Request Budget & Retry Accounting (Phase 8.6)

**Modules:** [`src/sensorguard/providers.py`](../src/sensorguard/providers.py) · [`src/sensorguard/pilot_monitor.py`](../src/sensorguard/pilot_monitor.py) · [`src/sensorguard/api.py`](../src/sensorguard/api.py)  
**Test Evidence:** [`tests/test_pilot_monitor.py`](../tests/test_pilot_monitor.py) · [`tests/test_milestone8_api.py`](../tests/test_milestone8_api.py)

---

## 1. Per-HTTP-Attempt Durable Quota Reservation

Previously, polling deducted only expected successful page counts (`max_pages_per_sensor`), allowing retries on HTTP 429, HTTP 5xx, timeouts, and network failures to issue uncounted requests.

Under Milestone 8.6:
1. **`OpenAQClient._execute_request` (`attempt_guard` Hook):** Prior to opening `httpx.Client` for **every attempt** `0..max_retries`, `OpenAQClient` invokes `attempt_guard(method, endpoint, attempt)`.
2. **Independent Short SQL Transaction Commit:** `_reserve_single_http_attempt` inside `run_bounded_opt_in_poll` opens an isolated transaction on `session_factory()`, acquires `_ATTEMPT_LOCK`, and executes an atomic SQL conditional update (`PilotQuotaState.requests_used + 1 <= effective_cap`).
3. **Never-Refund Accounting:** Because the quota commit completes **before** the wire call leaves the host and outside the main ingestion transaction:
   - Retries (`max_retries_per_request`), HTTP `429`, HTTP `500/502/503/504`, `httpx.ReadTimeout`, `NetworkError`, malformed payloads, and outer session rollbacks **still permanently charge the daily budget**.
   - When budget hits zero mid-retry, [`QuotaExhaustedError`](../src/sensorguard/providers.py) aborts cleanly **before** the next HTTP socket opens (`status = "QUOTA_EXHAUSTED"`), logging exact `http_attempts` into `pilot_ingest_audit_runs`.

---

## 2. Worst-Case Request Budget Calculation (`max_requests_per_day = 48`)

Given `max_pages_per_sensor = 1` and `max_retries_per_request = 1`, each sensor poll consumes:
- **Best case:** `1` HTTP attempt per sensor.
- **Worst case (every sensor retries once):** `1 + max_retries_per_request = 2` HTTP attempts per sensor.

| Target Scope | Active PM2.5 Sensors | Best-Case / Poll | Worst-Case / Poll (`retries=1`) | Max Safe Polls / UTC Day (`48/day` Cap) | Recommended Cadence |
|---|---:|---:|---:|---:|---|
| **Full 8-Station UK Subset** *(if all 8 verified active)* | `8` | `8` req | `16` req | **`3` polls/day** worst-case (`6` best-case) | Every **8h** (worst-case safe) or **4h** best-case |
| **Verified LAQN Active Pair** (`154`, `157`) | `2` | `2` req | `4` req | **`12` polls/day** worst-case (`24` best-case) | Every **2h** (100% immune to worst-case retry spikes) or **1h** best-case |
| **One-Time Pre-Flight Location Probe** | `8` loc metadata calls | `8` req | `16` req | **1 single manual pre-flight check** | Run once upon explicit authorization |

---

## 3. Server-Side API Rate Limiting Independent of Model Inference Workers

[`_enforce_server_rate_limit`](../src/sensorguard/api.py) backs FastAPI endpoints with durable multi-worker SQL rows in `pilot_api_rate_limits(scope, window_minute_utc)` rather than per-process memory lists:
- `admin_api`: `30` requests/min (`SENSORGUARD_ADMIN_RATE_LIMIT_PER_MIN`)
- `pilot_readiness_api`: `120` requests/min (`SENSORGUARD_PILOT_API_RATE_LIMIT_PER_MIN`)
- `forecast_api`: `600` requests/min (`SENSORGUARD_FORECAST_API_RATE_LIMIT_PER_MIN`)

Returns HTTP `429 Too Many Requests` with `Retry-After: 60` consistently across all worker processes and falls back cleanly only on legacy un-migrated SQLite fixtures lacking `pilot_api_rate_limits`.
