# SensorGuard AI — Milestone 5.2 Integration Report
**Real Product Integration, Selective-Prediction Reliability, and Security Hardening**

- **Date:** November 25, 2026
- **Status:** Staging Integration Complete & Verified | Public Production Launch NOT Approved (Internal Staging Only)
- **Active Branch:** `feat/milestone-3-forecasting-uncertainty`
- **Dataset Lineage:** Archived Q1 2024 UK AURN Pilot Dataset (`2024-01-01` – `2024-03-31`, 8 stations across Greater London & regional England; `14,556` raw valid DB observations; split into `8,884` train / `2,937` calibration / `3,496` test grid rows containing `2,727` valid reported targets, content hash: `33805c42097fc86e`). Zero live streaming.
- **Model Binary Digest (SHA-256):** `e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115` (updated from earlier M5.2 SHA-256 `2a03cd292ffd1ea352934d2926e83e3f331652e5e68190c21265b9c250018add` after harmonizing `max_disagreement_threshold=8.0` in M5.3).

---

## 1. Executive Summary

Milestone 5.2 resolves all five critical blockers identified during the Milestone 5.1 review, transforming SensorGuard AI from a prototype into an architecturally robust, hardened staging system.

Key achievements:
1. **Resolution of 100% SAGE Abstention Defect:** Disentangled the sensitivity scenario disagreement metric ($D_{\text{sensitivity}} = \max_\gamma U_\gamma - \min_\gamma U_\gamma$) from the prediction interval envelope width ($W_{\text{envelope}} = U_{\max} - L_{\min}$). Restored the natural-regime publication rate from $0.0\%$ to **$99.56\%$** ($2,715 / 2,727$ published) while achieving **$99.93\%$** empirical coverage.
2. **Threshold Harmonization:** Standardized `max_disagreement_threshold = 8.0` across evaluation pipelines ([ablation.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/ablation.py), [evaluation.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/evaluation.py)), algorithmic core ([sage.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage.py)), and model serving API ([api.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/api.py)).
3. **Hardened Model Serving & Cryptographic Provenance:** Replaced request-path dynamic retraining in [api.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/api.py) with fail-closed HTTP 503 behavior. Implemented cryptographic verification via [manifest.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/manifest.py) and [manifest.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/artifacts/models/manifest.json). Added system endpoints `/health/ready` and `/api/v1/model/status`, atomic multi-process file locks (`ingest.lock`), and in-memory feature cache invalidation.
4. **Real Frontend Integration:** Upgraded the Next.js frontend in [apps/web](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/apps/web) (now on Next.js 15.5.29). Replaced static mock data with a typed REST client ([api.ts](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/apps/web/lib/api.ts)) querying live backend endpoints, UK AURN coordinate projection (8 stations spanning Greater London and regional England), interactive SAGE uncertainty visualization canvas, and an explicit offline demonstration fallback toggle.
5. **Network-Level Concurrency Benchmarks:** Executed live socket load tests on Uvicorn via [benchmark_network_concurrency.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/benchmark_network_concurrency.py) across concurrency tiers $c \in \{1, 5, 10, 20\}$ (1,600 requests). Confirmed **$0.0\%$ error rate** and sub-50ms p95 latency at single concurrency.

---

## 2. Detailed Resolution of Milestone 5.1 Review Blockers

| Priority | Finding / Defect | Root Cause | Resolution in Milestone 5.2 |
| :--- | :--- | :--- | :--- |
| **P0** | **Frontend used mock data** | Dashboard prototype imported static fixtures from `mockData.ts`. | Created typed API client in [api.ts](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/apps/web/lib/api.ts); wired [page.tsx](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/apps/web/app/page.tsx) to fetch real stations, observations, forecasts, and reliability from `/api/v1/`. Clear banner and toggle for offline demo mode. |
| **P0** | **100% SAGE Abstention** | Code defined scenario disagreement as $U_{\max} - L_{\min}$ ($\approx 17.0 \ \mu\text{g/m}^3$). Exceeded threshold of $8.0$ on every row. | Mathematically separated $D_{\text{sensitivity}} = \max_\gamma U_\gamma - \min_\gamma U_\gamma = q_{\max} - q_{\min} \approx 3.32 \ \mu\text{g/m}^3$ from envelope width $W_{\text{envelope}}$. Natural publication rate restored to $99.56\%$ with $99.93\%$ coverage. |
| **P0** | **Threshold Mismatch** | `evaluation.py` configured `8.0`, while `ablation.py` and `api.py` used `20.0`. | Harmonized `max_disagreement_threshold = 8.0` uniformly across all modules. Added secondary `max_envelope_width_threshold = 50.0`. |
| **P0** | **Request-Path Dynamic Model Retraining** | Missing model artifact triggered fitting in HTTP request thread. | Decoupled training to offline script [train_pilot_model.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/train_pilot_model.py). Serving API `get_serving_model()` now strictly fails closed (HTTP 503) if artifact or cryptographic manifest is missing or invalid. |
| **P1** | **TestClient Sequential Latency** | Prior latency was measured sequentially inside in-memory TestClient. | Implemented [benchmark_network_concurrency.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/benchmark_network_concurrency.py) testing live socket HTTP connections over Uvicorn with $c \in \{1, 5, 10, 20\}$ concurrent async clients. |

---

## 3. Architecture & Operational Verification

### Cryptographic Model Provenance
The model artifact is trained offline and signed with a SHA-256 digest recorded in [manifest.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/artifacts/models/manifest.json):
```json
{
  "model_id": "sage_pilot_q1_2024",
  "version": "SAGE-LIGHTGBM-v1",
  "sha256": "2a03cd292ffd1ea352934d2926e83e3f331652e5e68190c21265b9c250018add",
  "dataset_hash": "33805c42097fc86e",
  "nominal_coverage": 0.9,
  "algorithm": "SAGE (Sensitivity-Aware Guarded Ensemble)"
}
```
Upon startup or request serving, [api.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/api.py) verifies the disk binary against this hash. If altered or unmanifested, serving fails closed with HTTP 503.

### Cache Invalidation & Multi-Process Concurrency
- `invalidate_pilot_feature_cache()`: Triggered whenever new observations are ingested via `POST /api/v1/admin/ingest` or background polling.
- `InterProcessFileLock` in [scheduler.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/scheduler.py): Coordinates background ingestion jobs across multi-process workers via atomic `ingest.lock`, preventing database locks and race conditions.

### Socket-Level Concurrency Results Summary
From [network_benchmark_results.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/network_benchmark_results.json) (100 requests per tier, 1,600 total requests):
- `/health`: p50 = 11.57ms, p95 = 14.51ms, throughput = 83.9 req/s ($c=1$); scales to 135.8 req/s ($c=5$).
- `/api/v1/stations`: p50 = 11.51ms, p95 = 14.88ms, throughput = 82.8 req/s ($c=1$).
- `/api/v1/stations/1/observations`: p50 = 24.60ms, p95 = 29.10ms, throughput = 39.9 req/s ($c=1$).
- `/api/v1/stations/1/forecasts` (Full SAGE ML pipeline): p50 = 30.19ms, p95 = 34.67ms, throughput = 32.6 req/s ($c=1$); scales to 37.0 req/s ($c=5$) with 0.0% errors.

---

## 4. Test Suite Summary
- **Pytest:** 85 passed, 0 failed in 46.6s ([test_api.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_api.py), [test_sage.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_sage.py), [test_ablations.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_ablations.py), [test_evaluation.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_evaluation.py), [test_baselines.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_baselines.py)).
- **Linter / Formatter:** Ruff 100% clean across all 68 files.
- **Database Migrations:** Clean SQLite & PostgreSQL transactional DDL generation via `alembic upgrade head --sql`.
- **Frontend Build:** Next.js 15.3.9 static optimization and typecheck passed with 0 errors.

---

## 5. Deployment Policy Notice
> [!IMPORTANT]
> SensorGuard AI is configured and certified for **private staging evaluation only**. Public production launch, live streaming sensor ingest, and marketing claims of SAGE superiority over conformal prediction are strictly withheld until multi-region longitudinal validation is completed.
