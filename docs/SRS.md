# SensorGuard AI — Software Requirements Specification (SRS)

**Version:** 1.1  
**Date:** 2026-11-25

---

## 1. Purpose & System Scope

SensorGuard AI is a reproducible ML/MLOps research prototype for hourly PM₂.₅ forecasting and uncertainty evaluation under non-random missingness:
- **Python package (`src/sensorguard/`):** Data loading, causal spatiotemporal features, missingness simulator, 6 forecasting baselines, conformal calibrators (Split, CQR, Rolling, ACI), SAGE sensitivity envelope, FastAPI server, and background ingestion scheduler.
- **Database (`alembic/`, `src/sensorguard/db/`):** SQLAlchemy 2.0 + Alembic migrations compatible with SQLite (local testing/staging) and PostgreSQL (containerized multi-worker deployment with connection pooling).
- **Web frontend (`apps/web/`):** Static Next.js 15 + TypeScript + Tailwind CSS dashboard (`output: "export"`) with explicit archived-pilot attribution and demonstration toggle.

---

## 2. Timestamp & Data Provenance Rules

| Term | Exact Definition |
|---|---|
| `observed_at` | UTC physical measurement interval end timestamp |
| `received_at` | UTC upstream publication timestamp (`NULL` if omitted by upstream; never inferred) |
| `fetched_at` | UTC local worker receipt timestamp upon HTTP completion |
| `persisted_at` | UTC database row persistence timestamp |
| `decision_cutoff` / `issued_at` | Latest observation boundary visible at forecast time |
| `target_time` | Forecasted target timestamp (`decision_cutoff + 1 hour`) |

---

## 3. Core Functional & Security Requirements

### 3.1 Ingestion & Schema (`F01–F04`)
- **SR-01:** Raw upstream HTTP payloads stored in `raw_ingest_batches` with SHA-256 `response_hash` and optional `raw_bytes`.
- **SR-02:** Observations enforce `observed_at <= fetched_at` and support append-only revisions via `UniqueConstraint("sensor_id", "observed_at", "version")`.
- **SR-03:** Immutable point-in-time feature snapshots stored in `forecast_feature_snapshots`.
- **SR-04:** Missingness states (`HEALTHY`, `LATE`, `NO_REPORT`, `OFFLINE_CONFIRMED`) never conflate API silence with physical hardware failure.

### 3.2 Feature Engineering & Simulation (`F05–F07`)
- **SR-05:** Features use `ffill().shift(1)` on reported observations so no target-time or future measurements leak.
- **SR-06:** Simulated outage evaluation drops all precomputed feature columns upfront, preserves ground truth in separate evaluator arrays, and fills causal cold-start missing history only with fixed historical `train_mean` and `age_hours = 999.0`.

### 3.3 Model Serving & Safety Guardrails (`F08, F12`)
- **SR-07:** Serving model loads from `artifacts/models/sage_pilot_q1_2024.joblib` verified against `manifest.json` (`sha256` constant-time digest check); missing or corrupted artifacts fail closed with HTTP `503`. No on-demand training occurs in HTTP handlers.
- **SR-08:** SAGE computes per-$\gamma$ calibration half-widths over $\gamma \in \{0.0, 0.5, 1.0, 1.5, 2.0\}$, outputs `[lower_bound, upper_bound]` envelope and `scenario_bounds`, and withholds publication (`status = "abstain"`) on `age_hours > 12.0`, `effective_sample_size < 15`, `D_sensitivity > 8.0`, or `envelope_width > 50.0`.

### 3.4 Exact REST API Endpoints (`src/sensorguard/api.py`)
- `GET /health`, `GET /healthz` — liveness & DB check
- `GET /health/ready`, `GET /api/v1/health/ready` — DB + verified model readiness probe
- `GET /api/v1/model/status` — manifest metadata & cryptographic verification status
- `GET /api/v1/stations` (optional `bbox`) — 8 UK AURN pilot stations (`is_archived_pilot=True`)
- `GET /api/v1/stations/{station_id}` — single station metadata
- `GET /api/v1/stations/{station_id}/observations` (`from`, `to`, `limit`) — observations with `observed_at`, `received_at`, `fetched_at`
- `GET /api/v1/stations/{station_id}/forecasts` (`limit`) — SAGE point & interval forecasts + `scenario_bounds`
- `GET /api/v1/stations/{station_id}/reliability` — gap rate, longest outage, benchmark summary
- `GET /api/v1/research/experiments`, `GET /api/v1/research/experiments/{experiment_id}` — audit JSON results + research caveats
- `GET /v1/demo/experiment` — deterministic synthetic simulator endpoint
- `POST /api/v1/admin/ingest`, `POST /api/v1/admin/evaluate` — protected by `X-Admin-Key` (`secrets.compare_digest`), fails closed (`503`) if `ADMIN_API_KEY` unset, rate-limited (30/min), concurrency-locked via `asyncio.Lock()`.

### 3.5 Evaluation & Frontend Integrity (`F09–F11`)
- **SR-09:** `compute_interval_metrics()` applies one unified finite mask across all metrics so `published_picp == overall_picp` whenever `n_abstained == 0`.
- **SR-10:** Frontend must display explicit archived-pilot attribution banners, never interpolate/fill gaps as measurements, and never fall back silently to mock tables while operating in live API mode.
- **SR-11:** No default DB passwords or exposed DB host ports in deployment manifests; `.env` remains git-ignored.
