# SensorGuard AI — Deployment Readiness & Production Release Checklist

**Document Version:** 1.0.0  
**Effective Date:** November 23, 2026  
**Target Environment:** Private Staging (Approved) vs Public Production (NOT Approved)  
**Security Classification:** Restricted / Internal Engineering Audit  

---

## 1. Operational Scope & Release Gates

> [!CAUTION]
> **PUBLIC PRODUCTION LAUNCH IS NOT APPROVED.**
> In accordance with research validation directives, SensorGuard AI is approved **strictly for Private Staging Evaluation** on the archived Q1 2024 8-station UK AURN pilot dataset (`2024-01-01` – `2024-03-31`). Public deployment, commercial release, live streaming ingestion, or external claims of superiority over conventional conformal prediction (CQR) are prohibited.

| Release Gate | Required Condition | Staging Status | Production Status | Owner / Authority |
| :--- | :--- | :---: | :---: | :--- |
| **Gate 1: Causal Feature Integrity** | Zero future lookahead or leakage under all outage regimes | **PASSED** | PENDING MULTI-CITY | Research Team |
| **Gate 2: Scientific Calibration** | Unified denominator; empirical coverage &ge; nominal target | **PASSED** | PENDING EXPANSION | Statistical Reviewer |
| **Gate 3: Honest Trade-Offs** | Transparent reporting of when CQR outperforms SAGE | **PASSED** | **PASSED** | Research Team |
| **Gate 4: API Performance Profile** | Sub-50ms p95 latency under concurrent load | **PASSED** (p95: 42ms) | PENDING LOAD TEST | MLOps Team |
| **Gate 5: Cryptographic Provenance** | Model artifact SHA-256 verification before deserialization | **PASSED** | **PASSED** | Security Team |
| **Gate 6: Administrative Security** | Fail-closed constant-time auth & sliding-window rate limit | **PASSED** | PENDING OAUTH2/JWT | Security Team |
| **Gate 7: Database Infrastructure** | Migration path from SQLite staging to PostgreSQL production | **VERIFIED** | PENDING RDS PROVISION | Infrastructure Team |
| **Gate 8: Staging UI Completeness** | Responsive Next.js interface with archived pilot labeling | **PASSED** | PENDING USER TESTING | Frontend Team |

---

## 2. Scientific Integrity & Model Provenance

### 2.1 Causal Reconstruction Verification
- [x] All feature engineering in `build_spatiotemporal_features()` enforces point-in-time constraints.
- [x] In `prepare_scenario_test_data()`, pre-computed feature columns (`last_pm25`, `lag_1h`, `lag_24h`, `rolling_mean_6h`, etc.) are dropped upfront.
- [x] Features are strictly recomputed from visible observation history (`history_df = pd.concat([train_df, cal_df])`).
- [x] Cold-start rows replace future backfill with fixed historical prior (`train_mean`) and `age_hours = 999.0`.
- [x] Adversarial cold-start tests in `tests/test_ablations.py` prove mathematical invariance to future perturbations.

### 2.2 Model Artifact Verification
- [x] Model artifact stored at `artifacts/models/sage_pilot_q1_2024.joblib`.
- [x] Cryptographic SHA-256 checksum recorded in `artifacts/models/sage_pilot_q1_2024.sha256` and `manifest.json`:
  `e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115`
- [x] `verify_model_sha256()` / `manifest.verify_file()` validates hash integrity using constant-time digest comparison; serving fails closed with HTTP `503`.

### 2.3 Transparent Baseline Claims
- [x] SAGE is NOT claimed as universally superior to conventional conformal prediction.
- [x] Acknowledged that under exchangeable, calm conditions, LightGBM + CQR provides sharper intervals (11.35 µg/m³ vs 17.01 µg/m³) and superior Winkler scores (11.45 vs 17.06).
- [x] SAGE’s value proposition is strictly scoped to nonrandom missingness (MNAR) and guarded safety abstention during sensor hardware failure.

---

## 3. Backend & API Hardening Checklist

### 3.1 Contract Conformance
- [x] Strict `is_archived_pilot=True` returned on all station metadata records (`/api/v1/stations`).
- [x] Observation timestamp semantics strictly separated:
  - `observed_at`: Physical measurement period end (UTC).
  - `received_at`: Upstream provider transmission receipt timestamp.
  - `fetched_at`: Local ingestion timestamp.
- [x] Standard HTTP status codes:
  - `200 OK`: Valid retrieval.
  - `400 Bad Request`: Bounding box or parameter syntax errors.
  - `401 Unauthorized`: Invalid administrative key.
  - `404 Not Found`: Unknown station ID or experiment ID.
  - `422 Unprocessable Entity`: Missing required request fields.
  - `429 Too Many Requests`: Administrative rate limit exceeded (&gt; 30 req/min).
  - `503 Service Unavailable`: Unconfigured admin key (fail-closed) or database connection loss.

### 3.2 Latency & Performance SLA
- [x] `GET /healthz`: p50 = 12.86 ms, p95 = 15.56 ms (&le; 50 ms SLA).
- [x] `GET /api/v1/stations`: p50 = 13.16 ms, p95 = 15.98 ms (&le; 50 ms SLA).
- [x] `GET /api/v1/stations/{id}/observations`: p50 = 17.01 ms, p95 = 22.15 ms (&le; 50 ms SLA).
- [x] `GET /api/v1/stations/{id}/forecasts`: p50 = 34.13 ms, p95 = 42.64 ms (&le; 50 ms SLA).
- [x] Feature caching via `get_cached_pilot_features()` prevents redundant SQLite disk scans.

### 3.3 Concurrency & Lock Safety
- [x] `_ADMIN_EXECUTION_LOCK = asyncio.Lock()` guards all administrative evaluation and ingestion endpoints.
- [x] `IngestScheduler._poll_lock = asyncio.Lock()` prevents overlapping background polling cycles.
- [x] Verified via `test_admin_concurrency_lock` and `test_scheduler_freshness_audit`.

---

## 4. Database Infrastructure: SQLite Staging to PostgreSQL Production

### 4.1 Staging Environment (Active)
- Database: SQLite 3 (`data/sensorguard.db`).
- Records: 14,556 observations across 8 stations.
- Threading: `StaticPool` with connection reuse for local staging verification.

### 4.2 Production Environment (Target Specification)
To migrate from Private Staging to PostgreSQL Production when release approval is granted:
1. **Connection String:** Configure `DATABASE_URL` in production secret manager:
   ```text
   DATABASE_URL=postgresql+psycopg2://sensorguard_app:SECRET_PW@db.internal:5432/sensorguard_prod?sslmode=require
   ```
2. **Connection Pooling Parameters:**
   - `pool_size = 20`
   - `max_overflow = 10`
   - `pool_recycle = 1800` (recycle connections older than 30 minutes)
   - `pool_pre_ping = True` (detect disconnected database handles)
3. **Alembic Migration Protocol:**
   ```bash
   # Run pre-deployment dry-run:
   alembic upgrade head --sql
   # Execute migration:
   alembic upgrade head
   ```
4. **Data Seed Protocol:**
   Execute `python scripts/ingest_pilot_network.py` to populate initial UK AURN historical baseline.

---

## 5. Security & Secret Management

- [x] `.env` is strictly excluded from Git tracking via `.gitignore`.
- [x] `.env.example` provides non-sensitive template configurations only.
- [x] `verify_admin_key()` fails closed with `HTTP 503` if `ADMIN_API_KEY` is undefined.
- [x] Administrative requests enforce a sliding window rate limit of 30 requests/minute.
- [x] No cloud provider API keys, private certificates, or database credentials exist in source code.

---

## 6. Frontend Staging Web App (`apps/web`)

- [x] Built with Next.js 15.5.29 (App Router static export), React 18, TypeScript, and Tailwind CSS (`npm audit` 0 vulnerabilities; ESLint + `tsc --noEmit` + `next build` pass).
- [x] Persistent warning banner states: `Strict Pilot Mode: Archived Q1 2024 UK AURN Network`.
- [x] Actual 8 UK AURN pilot coordinates (Greater London, Southampton, Wicken Fen, St Osyth, Manchester) mapped onto an interactive SVG projection.
- [x] Live visualization of SAGE point forecasts, conservative sensitivity envelopes, and safety status badges (`PUBLISH`, `CAUTION`, `ABSTAIN`).
- [x] Live Simulator Lab querying `/api/v1/research/experiments/{id}` cleanly without mock substitution in live mode.

---

## 7. Sign-Off & Staging Approval Decision

| Review Domain | Assessor | Decision | Notes |
| :--- | :--- | :---: | :--- |
| **Statistical & ML Integrity** | Senior ML Research Engineer | **APPROVED FOR STAGING** | Cold-start leakage fixed; honest negative results committed. |
| **Backend & MLOps Pipeline** | Infrastructure / MLOps Lead | **APPROVED FOR STAGING** | Sub-50ms latency met; model SHA-256 verified; locks in place. |
| **Staging User Interface** | Frontend Engineering Lead | **APPROVED FOR STAGING** | Next.js build passes cleanly; strict pilot attribution prominent. |
| **Security & Authorization** | Security Reviewer | **APPROVED FOR STAGING** | Fail-closed admin auth verified; no credentials in Git. |
| **Public Production Release** | Executive Review Board | **NO-GO / REJECTED** | Public launch withheld pending real multi-city evaluation. |

**Final Recommendation:** Proceed with Milestone 5.1 Private Staging demonstration and research audit. Maintain production lock until multi-city validation is authorized.
