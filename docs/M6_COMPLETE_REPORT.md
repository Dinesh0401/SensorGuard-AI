# SensorGuard AI — Project Complete: Milestone 6 & 6.1 Summary Report

**Date:** 2026-11-25  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Verified Tests:** 86 passed (`pytest tests/ -q`), Ruff clean, Frontend ESLint / `tsc --noEmit` / `next build` / `npm audit` (0 vulnerabilities) clean  
**Release Decision:** **GO for Private Staging & Portfolio Demo · NO-GO for Public Production Launch**

---

## 1. Milestone Completion Matrix

| Milestone | Scope | Verification Evidence | Status |
|---|---|---|---|
| M0 | Literature/novelty audit, PRD, SRS, data dictionary | [`docs/PRD.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/PRD.md), [`docs/SRS.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/SRS.md), [`docs/NOVELTY_AUDIT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/NOVELTY_AUDIT.md) | ✅ Complete |
| M1 | OpenAQ v3 ingestion, append-only versions, replay | `tests/test_db_and_ingest.py`, `tests/test_replay.py`, `alembic/` | ✅ Complete |
| M2 | 6 baseline forecasters, chronological 60/20/20 splits | [`docs/BASELINE_COMPARISON.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/BASELINE_COMPARISON.md) | ✅ Complete |
| M3 | MCAR/MAR/MNAR/burst simulator, isolated evaluator truth | `tests/test_simulation.py`, `tests/test_ablations.py` | ✅ Complete |
| M4 | SAGE algorithm, 3-seed × 5-condition ablation study | [`docs/audit/sage_ablation_results.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/sage_ablation_results.json) | ✅ Complete |
| M4.1 | Evaluation-mask finite denominator fix, cold-start leak fix | `tests/test_ablations.py` (`test_adversarial_cold_start_no_future_leakage`) | ✅ Complete |
| M5 | FastAPI backend, MLflow logging, background scheduler | [`src/sensorguard/api.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/api.py), [`src/sensorguard/scheduler.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/scheduler.py) | ✅ Complete |
| M5.1–M5.3 | Disagreement separation, fail-closed SHA-256 serving, CORS, frontend Simulator Lab | `tests/test_api.py`, [`apps/web/app/page.tsx`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/apps/web/app/page.tsx) | ✅ Complete |
| M6 & M6.1 | Research report, portfolio demo, ESLint CI & npm security patches (`next@15.5.29`), doc & config hardening | [`docs/RESEARCH_REPORT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/RESEARCH_REPORT.md), [`docs/MILESTONE_6_1_RELEASE_AUDIT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/MILESTONE_6_1_RELEASE_AUDIT.md) | ✅ Complete |

---

## 2. Dataset Provenance & Verified Benchmark Numbers

- **Window & Network:** Archived Q1 2024 (`2024-01-01T01:00:00Z` – `2024-03-31T23:00:00Z`) across 8 UK AURN stations spanning Greater London (`Harlington`, `Hillingdon`, `Marylebone Road`, `Bexley Slade Green`) and regional England (`Southampton Centre`, `Wicken Fen`, `St Osyth`, `Manchester Piccadilly`).
- **Exact Row Reconciliation:** `14,556` raw valid DB rows = `8,884` (`train`, reported) + `2,937` (`calibrate`, reported) + `2,727` (`test` reported) + `8` (initial per-station warmup points lacking prior `last_pm25`). The `test` partition includes `769` unreported station-hours (`3,496` grid rows total), and evaluation filters strictly to the `2,727` finite targets. Content hash: `33805c42097fc86e`.
- **Natural Benchmark (`SG-BENCH-20261125-040900`):**
  - Persistence + Split Conformal: `96.88%` overall / `97.02%` pub PICP, `MPIW = 2.00 µg/m³`, `Winkler = 3.16`, `0.44%` abstention.
  - LightGBM + CQR: `99.89%` PICP, `MPIW = 11.35 µg/m³`, `Winkler = 11.45`, `0.44%` abstention.
  - SAGE Full: `99.93%` overall / `99.93%` pub PICP, `MPIW = 17.01 µg/m³` (`17.00` pub), `Winkler = 17.06`, `0.44%` abstention.
- **MNAR ($\gamma=1.5$):** Single-seed (`seed=42`): `99.85%` PICP / `99.96%` pub PICP, `0.95%` abstention (`26/2,727`). 3-seed ablation mean (`[11, 42, 107]`): `1.09% ± 0.17%` abstention. All active abstentions in Q1 2024 stem from `age_hours > 12.0` (`stale_sensor_history`), because calibrated scalar $D_{\text{sensitivity}} = 3.32 < 8.0$.
- **8-Fold LOSO CV:** CQR achieves better Winkler scores on all 8 stations; `station_4` (`Bexley - Slade Green`) drops CQR to `81.72%` while SAGE keeps `90.72%` (`MPIW = 4.76`, `Winkler = 13.23`).
- **Stale-History Subgroup (`age_hours > 12.0`):** `n = 12` valid observations in `natural` (`n = 26` in MNAR), with **0 published / 12 abstained** on SAGE (matching the shared baseline freshness rule); therefore counterfactual containment on withheld intervals (`100%` natural, `88.5%` MNAR) is **not** claimed as delivered forecast superiority.

---

## 3. Production Launch Gate: **NO-GO** (Remaining Open Items)

1. Comparison to `TMLR 2026` (*Adaptive Conformal Prediction for Time Series with Missing Values*) and input-dependent local disagreement scaling remain open before any publication-level novelty claim.
2. Live OpenAQ ingestion and real PostgreSQL multi-worker soak tests have not run against an external cloud staging cluster.
3. No reverse proxy (TLS / public GET rate limiting) or external observability alerting is provisioned yet.
