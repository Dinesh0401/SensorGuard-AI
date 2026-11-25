# SensorGuard AI — Product Requirements Document (PRD)

**Version:** 1.0  
**Status:** Research prototype — not for public deployment  
**Date:** 2026-11-25  
**Owner:** SensorGuard AI project team

---

## 1. Problem Statement

Public air-quality sensor networks experience systematic, non-random failures: stations drop offline more frequently during high-pollution events, during adverse weather, and during peak transmission loads. This introduces **informative missingness** (MNAR) that biases standard conformal forecasting methods calibrated on the available (selected) subset of observations.

A system that simply uses whatever data arrives will systematically undercover the most health-relevant, high-concentration events — the precise conditions when reliable predictions matter most.

---

## 2. Product Vision

> "Show what the station measured, what the model estimates next, how uncertain it is, and when the available evidence is too weak to make a trusted forecast."

---

## 3. Target Users

| User | Primary Need |
|---|---|
| Community observer | Station status, nearby measurement history, point forecasts, uncertainty, and freshness |
| Researcher | Historical replay, outage scenarios, baseline comparisons, calibration subgroup analysis |
| Network operator | Data quality/coverage dashboard, source delay alerts, validated failure explanations, exports |

---

## 4. Product Scope

### In Scope (MVP)
- One pilot city/region: 8 UK AURN PM₂.₅ stations
- Hourly PM₂.₅ forecasts at +1-hour horizon
- 90% prediction intervals with explicit uncertainty
- Sensor health / freshness status display
- Explicit abstention signal when prediction is unreliable
- Researcher lab: scenario selector (natural / MCAR / MNAR / burst)
- Research experiment registry with committed artefact provenance

### Out of Scope
- Clinical or medical advice
- Official AQI certification or regulatory guidance
- Global sensor coverage guarantees
- Automated publication of pollution crisis alerts
- Hardware manufacturing or sensor deployment
- Distribution-free coverage guarantees under unrestricted MNAR

---

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| F01 | Station discovery and availability audit | P0 |
| F02 | Hourly ingestion with retries, backoff, pagination | P0 |
| F03 | Time-aware data normalisation and quality flags | P0 |
| F04 | Point-in-time replay of historical windows | P0 |
| F05 | Feature engineering with missingness and age | P0 |
| F06 | Reproducible experiment runner with manifest checksums | P0 |
| F07 | Outage simulator: MCAR / MAR / MNAR / burst | P0 |
| F08 | Point and interval forecasts (six baselines + SAGE) | P0 |
| F09 | Interval validation and ablation reports | P0 |
| F10 | Public dashboard: map, sensor state, trend, forecast, uncertainty | P1 |
| F11 | Administrative model/version/incident detail | P1 |
| F12 | Explicit abstention and stale-data behaviour with reason + timestamp | P0 |

---

## 6. Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| NF01 | Forecast update latency | Hourly when upstream data is available |
| NF02 | p95 API response time | ≤ 500 ms (pilot deployment, excluding external providers) |
| NF03 | Experiment reproducibility | Deterministic with recorded seeds, splits, manifests |
| NF04 | Test coverage | ≥ 80% for deterministic core modules |
| NF05 | Accessibility | Responsive UI, keyboard navigation, readable charts |
| NF06 | Security | Secrets server-side only; HTTPS; basic rate limiting |
| NF07 | Provider failure handling | Stale-data state with preserved last-known forecast + timestamp |

---

## 7. Acceptance Criteria (M6 — All Milestones Complete)

| Criterion | Status |
|---|---|
| 8 UK AURN stations in database (`Q1 2024`) | ✅ 8 stations (`14,556` valid readings) |
| All 6 baselines + SAGE + 8-fold LOSO CV benchmarked | ✅ Committed in `docs/audit/benchmark_results.json` |
| MCAR / MAR / MNAR / burst outage scenarios | ✅ `src/sensorguard/ablation.py` |
| 3 ablation seeds, negative results documented | ✅ 3 seeds, honest negatives in `RESEARCH_REPORT.md` |
| Automated unit, contract, migration & no-leakage tests | ✅ 86 tests pass (no-leakage, API, DB, SAGE) |
| Python + Next.js CI pipeline green | ✅ Ruff, Alembic, pytest, npm audit (0 vulns), ESLint, tsc, build |
| Containerized staging config (`docker-compose.yml`) | ✅ PostgreSQL internal network, loopback ports, no default passwords |
| Research report & portfolio demo | ✅ `docs/RESEARCH_REPORT.md`, `docs/PORTFOLIO_DEMO.md` |
| Honest evidence & SHA-256 fail-closed API | ✅ Audited against JSON artifacts |

---

## 8. Known Limitations and Open Items

1. 8-fold Leave-One-Station-Out CV is evaluated only on the UK AURN Q1 2024 network (natural regime); out-of-region multi-city and multi-season generalisability remain unvalidated
2. SAGE interval widths are substantially wider (`17.01 µg/m³`) than Persistence (`2.00 µg/m³`) and LightGBM + CQR (`11.35 µg/m³`), and CQR achieves lower Winkler scores on all 8 LOSO stations
3. Current global calibration sensitivity spread `D_sensitivity = 3.32 µg/m³ < 8.0` is invariant across test inputs, so observed abstentions (`12` natural, `26` MNAR seed 42) trigger via sensor staleness (`age_hours > 12h`) rather than per-sample local disagreement
4. Comparison to `TMLR 2026` (*Adaptive Conformal Prediction for Time Series with Missing Values*) is incomplete
5. Public live deployment is **not approved** (requires PostgreSQL load verification, reverse-proxy rate limiting, and sustained live ingestion checks)
