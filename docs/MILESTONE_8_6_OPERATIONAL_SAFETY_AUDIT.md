# SensorGuard AI — Milestone 8.6 Operational Safety & Prospective Validation Audit

**Audit Date:** 2026-11-26  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Starting Commit:** [`454c12573c79bc9f80eff1e4f832db9667e9c618`](https://github.com/Dinesh0401/SensorGuard-AI/commit/454c12573c79bc9f80eff1e4f832db9667e9c618)  
**Schema Migration:** [`alembic/versions/007_m86_safety_fencing.py`](../alembic/versions/007_m86_safety_fencing.py)

---

## 1. Executive Audit Summary (All P0 & P1 Acceptance Findings Addressed)

Milestone 8.6 resolves every operational-safety and scientific-validity blocker raised against the initial Milestone 8 delivery while keeping external polling fail-closed locked:

| Priority | Original Milestone 8 Finding | Root-Cause Correction Implemented | Verification Evidence |
|---|---|---|---|
| **P0** | `shadow_eval.py` set `ForecastRecord.created_at = decision_cutoff`, and tests labeled past/already-observed cutoffs as prospective. | Added `generated_at`, `persisted_at`, `evaluation_mode`, `model_created_at`, `artifact_immutable_ref`, and DB uniqueness constraints (`idempotency_key`, `uq_forecasts_run_station_target`). Rejects post-target or already-observed DB targets cleanly with `ValueError` in [`shadow_eval.py`](../src/sensorguard/shadow_eval.py) and isolates [`generate_retrospective_replay_forecasts`](../src/sensorguard/shadow_eval.py) from `prospective_shadow` metrics. | [`tests/test_shadow_eval.py`](../tests/test_shadow_eval.py) (`test_backdated_prospective_forecasts_rejected_and_retrospective_isolated`, `test_genuine_prospective_shadow_forecasts_before_future_target_arrival`) |
| **P0** | `DurablePilotQuotaTracker` reserved `max_pages_per_sensor` once, ignoring `OpenAQClient` retries. | Attached atomic per-attempt `attempt_guard` callback inside [`OpenAQClient._execute_request`](../src/sensorguard/providers.py) committing 1 unit to `pilot_quota_states` in an independent short SQL transaction **before** every outbound HTTP call (including retries, 429, 5xx, timeouts, network failures) with never-refund semantics and [`QuotaExhaustedError`](../src/sensorguard/providers.py). | [`tests/test_pilot_monitor.py`](../tests/test_pilot_monitor.py) (`test_per_attempt_quota_counts_retries_5xx_429_timeouts_and_midnight_rollover`) |
| **P1** | `require_db_unlock_for_live=False` bypassed `PilotControlState` when absent. | Removed `require_db_unlock_for_live` from [`BoundedPilotConfig`](../src/sensorguard/pilot_monitor.py); created fail-closed default row via `ensure_default_control_state`; enforced `kill_switch_active=False`, `opt_in_enabled=True`, non-null `authorized_at`, and non-default `updated_by` unconditionally across all runs (including mock transports). | [`tests/test_pilot_monitor.py`](../tests/test_pilot_monitor.py) (`test_opt_in_poll_blocks_default_and_enforces_daily_quota`, tamper check) |
| **P1** | Leases expired after 300s without renewal heartbeat during long runs. | Added monotonic `fencing_token` to `pilot_poll_leases`, [`renew_poll_lease`](../src/sensorguard/pilot_monitor.py) before each sensor batch, commit-time [`verify_fencing_token`](../src/sensorguard/pilot_monitor.py) before `session.commit()`, fenced `release_poll_lease`, and `SKIPPED_STALE_FENCING_TOKEN` rollback protection. | [`tests/test_pilot_monitor.py`](../tests/test_pilot_monitor.py) (`test_renewable_lease_heartbeat_and_stale_fencing_prevents_commit`) |
| **P1** | Process-local rate limit list in `api.py`; 100-outcome threshold ignored time span, subgroups, and matched baseline parity. | Replaced in-process list with shared DB-backed `pilot_api_rate_limits` fixed-window counter in [`src/sensorguard/api.py`](../src/sensorguard/api.py) across `admin_api`, `pilot_readiness_api`, and `forecast_api`. Added `min_unique_hours=24`, station-day block bootstrap 95% CI, high-pollution / long-outage subgroups, provider/quality incident counts, and matched-availability comparisons vs `LOCALLY-WEIGHTED-MAR`, `PERSISTENCE-SPLIT`, and `LIGHTGBM-CQR` in [`shadow_eval.py`](../src/sensorguard/shadow_eval.py). | [`tests/test_milestone8_api.py`](../tests/test_milestone8_api.py) · [`tests/test_shadow_eval.py`](../tests/test_shadow_eval.py) |

---

## 2. Database Schema Evolution (`007_m86_safety_fencing`)

- **`forecast_runs`:** `evaluation_mode` (`"serving" | "prospective_shadow" | "retrospective_replay"`), `model_created_at`, `artifact_immutable_ref`, unique `idempotency_key`, indexed `generated_at` and `persisted_at`.
- **`forecasts`:** `generated_at`, `persisted_at`, and unique index `uq_forecasts_run_station_target` on `(run_id, station_id, target_time)`.
- **`pilot_poll_leases`:** `fencing_token` monotonic integer incremented atomically in SQL upon every `acquire_poll_lease`.
- **`pilot_control_states`:** `authorized_at` timestamp required alongside non-default operator identity (`updated_by`).
- **`pilot_ingest_audit_runs`:** `fencing_token` and `http_attempts` per poll audit record.
- **`pilot_api_rate_limits`:** server-side `(scope, window_minute_utc)` counter enforcing HTTP 429 (`Retry-After: 60`) across independent API workers.

---

## 3. Authorizations Still Strictly Locked

- **External OpenAQ polling remains LOCKED** (`kill_switch_active = True`, `opt_in_enabled = False`, `authorized_at = None`).
- **Adaptive SAGE (`AdaptiveSAGEForecaster`) remains UNPROMOTED** (`promoted_to_primary = False`; `adaptive_sage_promotion_eligible = False`) — primary API exclusively serves Static `SAGEForecaster` (`sage_pilot_q1_2024@1.0.0`).
- **Companion Milestone 8.6 Docs:**
  - [`PROSPECTIVE_SHADOW_TIMESTAMP_INVARIANTS.md`](PROSPECTIVE_SHADOW_TIMESTAMP_INVARIANTS.md)
  - [`EXACT_PROVIDER_REQUEST_BUDGET.md`](EXACT_PROVIDER_REQUEST_BUDGET.md)
  - [`PILOT_ACTIVATION_APPROVAL_GATE.md`](PILOT_ACTIVATION_APPROVAL_GATE.md)
