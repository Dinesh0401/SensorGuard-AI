# SensorGuard AI — Software Requirements Specification (SRS)

**Version:** 1.0  
**Date:** 2026-11-25

---

## 1. Introduction

### 1.1 Purpose
This document specifies the software requirements for SensorGuard AI, a research-grade air-quality forecasting system that handles non-random sensor missingness.

### 1.2 Scope
SensorGuard AI comprises:
- A Python research library (`src/sensorguard/`) for data ingestion, feature engineering, baseline forecasting, conformal calibration, SAGE algorithm, and evaluation
- A FastAPI REST backend (`src/sensorguard/api.py`)
- A Next.js frontend (`apps/web/`)
- A PostgreSQL database (schema managed by Alembic)
- A background scheduler worker
- CI/CD via GitHub Actions

### 1.3 Definitions
| Term | Definition |
|---|---|
| `observed_at` | UTC timestamp of the physical measurement (end of averaging interval) |
| `fetched_at` | UTC timestamp when the SensorGuard worker fetched the HTTP response |
| `received_at` | UTC upstream publication/update timestamp (NULL if not provided by upstream) |
| `target_at` | The forecasted future timestamp |
| `issued_at` | UTC timestamp when the forecast was computed and stored |
| MNAR | Missing Not At Random — missingness depends on the unobserved value itself |
| MCAR | Missing Completely At Random |
| MAR | Missing At Random — missingness depends only on observed covariates |
| SAGE | Sensitivity-Aware Guarded Estimation — the candidate research algorithm |
| PICP | Prediction Interval Coverage Probability |
| MPIW | Mean Prediction Interval Width |
| CQR | Conformalized Quantile Regression (Romano et al. 2019) |

---

## 2. System Requirements

### 2.1 Software environment
- Python 3.11+
- PostgreSQL 15+ (or SQLite for testing)
- Node.js 20+
- Docker and Docker Compose (for containerised deployment)

### 2.2 Python dependencies (core)
FastAPI, Pydantic v2, SQLAlchemy, Alembic, pandas, numpy, scipy, scikit-learn, LightGBM, joblib, MLflow, pytest, ruff

### 2.3 Frontend dependencies
Next.js 14+, TypeScript, Tailwind CSS

---

## 3. Functional Requirements

### 3.1 Data Ingestion (F01, F02, F03)

**SR-01** The system shall store all raw API responses in `raw_ingest_batches` with a SHA-256 hash of the payload.

**SR-02** The system shall distinguish `observed_at`, `fetched_at`, and `received_at` and reject observations where `observed_at > fetched_at`.

**SR-03** The system shall support append-only versioning for revised measurements.

**SR-04** Sensor status shall be classified as one of: `HEALTHY`, `NO_REPORT`, `LATE`, `INVALID`, `SOURCE_UNAVAILABLE`, `OFFLINE_CONFIRMED`. Hardware outage shall never be inferred solely from API silence.

**SR-05** API keys shall be stored server-side only and never logged or transmitted to the frontend.

### 3.2 Feature Engineering (F05)

**SR-06** Features at forecast issue time `t` for target `t+h` shall use only data with `observed_at ≤ t` and `fetched_at ≤ t`.

**SR-07** The system shall explicitly track missingness features: `hours_since_last_obs`, `freshness_flag`, `is_reporting`.

**SR-08** The system shall explicitly track spatial features from neighbouring stations using values available at issue time.

### 3.3 Forecasting (F08)

**SR-09** The system shall implement the following baseline methods:
- Persistence (last observation carried forward)
- Seasonal naive (t-24h)
- Spatial neighbour median
- Ridge regression with split conformal
- LightGBM with split conformal
- LightGBM with CQR (Romano et al. 2019)
- LightGBM with rolling conformal (168h trailing)
- LightGBM with ACI (Gibbs & Candès 2021)

**SR-10** The system shall implement SAGE as specified in `docs/RESEARCH_REPORT.md` §5.

**SR-11** The API shall refuse to serve predictions from an unverified model artefact (fail-closed on SHA-256 mismatch).

### 3.4 Abstention (F12)

**SR-12** SAGE shall abstain when the scenario-disagreement signal `D_sensitivity > τ` (default τ = 8 µg/m³).

**SR-13** API responses shall include `decision` (`publish` or `abstain`) and `decision_reason` for every forecast.

### 3.5 Evaluation (F09)

**SR-14** Ground-truth values for masked outage simulations shall be stored in evaluator-only storage and never accessible to the model at inference time.

**SR-15** Coverage shall be computed using the same finite-value mask in all evaluation paths (overall coverage = published coverage when no abstentions).

**SR-16** The evaluation shall report: PICP, MPIW, Winkler score, MAE, RMSE, abstention rate, and worst-stratum coverage gap, stratified by: pollution level, freshness, and diurnal period.

### 3.6 API (REST)

**SR-17** The API shall implement the following endpoints:
- `GET /health`
- `GET /api/v1/stations`
- `GET /api/v1/observations/latest`
- `GET /api/v1/forecast/{station_id}`
- `GET /api/v1/model/status`
- `GET /api/v1/research/experiments`
- `GET /api/v1/research/experiments/{id}`
- `POST /api/v1/ingest` (admin only)

**SR-18** The API shall validate model artefact SHA-256 on startup and return HTTP 503 if validation fails.

**SR-19** The API shall include CORS headers for allowed origins configured via `CORS_ORIGINS` environment variable.

### 3.7 Frontend (F10)

**SR-20** The frontend shall display a map of station locations with health indicators.

**SR-21** The frontend shall display observed PM₂.₅ time series with gap markers (not filled interpolation).

**SR-22** The Simulator Lab shall allow scenario selection and display live benchmark/ablation results from the API.

**SR-23** The frontend shall display model status with SHA-256 verification badge.

**SR-24** The frontend shall not display fabricated data as live API data; demo mode shall be clearly labelled.

---

## 4. Non-Functional Requirements

**SR-25** No secret, API key, `.env` file, or private data shall be committed to version control.

**SR-26** The experiment runner shall be deterministic: given the same seeds, splits, and dataset manifest, it shall produce identical outputs.

**SR-27** All evaluation numbers reported in documentation shall match the committed artefacts in `docs/audit/`.

**SR-28** The system shall not perform destructive database operations, paid cloud deployments, or external communications without explicit human approval.

---

## 5. Data Model (key tables)

| Table | Key Columns |
|---|---|
| `stations` | id, provider, provider_location_id, name, lat, lon |
| `sensors` | id, station_id, parameter, canonical_unit |
| `raw_ingest_batches` | id, provider, fetched_at, response_hash |
| `observations` | id, sensor_id, observed_at, received_at, value, quality_flag, version |
| `forecasts` | id, station_id, issued_at, target_at, point_estimate, lower, upper, decision |
| `experiments` | id, dataset_manifest_hash, config_json, started_at, artifacts_uri |

---

## 6. Security Requirements

- All secrets in server-side environment variables only
- HTTPS required for any public deployment
- Admin endpoints protected by `ADMIN_API_KEY` header
- Rate limiting on all public endpoints
- No PII collected; no user personal information stored

---

*This SRS reflects the M6-complete state of SensorGuard AI. For deployment approval, complete the checklist in `docs/DEPLOYMENT_READINESS_CHECKLIST.md`.*
