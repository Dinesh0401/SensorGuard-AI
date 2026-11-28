# SensorGuard AI — Model Serving & Versioning Audit (Phase 8.0)

**Audit Date:** 2026-11-25  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Registry Artifact:** [`artifacts/models/model_registry.json`](../artifacts/models/model_registry.json) · [`src/sensorguard/models/registry.py`](../src/sensorguard/models/registry.py)  
**Primary Serving Manifest:** [`artifacts/models/manifest.json`](../artifacts/models/manifest.json)  
**Causal Research Baseline:** [`docs/audit/adaptive_sage_results.json`](audit/adaptive_sage_results.json) (`SG-M77-ADAPTIVE-20261125-163505`)

---

## 1. Executive Summary & Serving Separation Policy

Milestone 8 establishes a strict, machine-verifiable boundary between **what the live FastAPI backend serves** and **what research models run in offline/shadow evaluation**:

1. **Active Primary Serving Model:** `sage_pilot_q1_2024` (`SAGEForecaster`, Static SAGE `v1.0.0`) is the **only** model loaded and served by `/api/v1/stations/{id}/forecasts`, `/health/ready`, and [`scripts/train_pilot_model.py`](../scripts/train_pilot_model.py).
2. **Shadow Research Candidate (NOT Active Serving):** `sage_adaptive_q1_2024` (`AdaptiveSAGEForecaster`, `v2.0.0-shadow-m77`, `promoted_to_primary: false`) is **not** served on the primary API path. Under causal Milestone 7.7 verification (`SG-M77-ADAPTIVE-20261125-163505`), Adaptive SAGE does **not** meet promotion criteria to replace Static SAGE or outscore simpler baselines cleanly enough for primary promotion.
3. **Forecast Record Identification:** Every forecast emitted by [`src/sensorguard/api.py`](../src/sensorguard/api.py) identifies both the artifact ID/version and concrete Python estimator class (`sage_pilot_q1_2024@1.0.0 (SAGEForecaster)`), while PostgreSQL `forecast_runs` separates `execution_mode = "serving"` from `execution_mode = "shadow"`.

---

## 2. Verified Model Registry Matrix

| Model ID | Version | Class | Algorithm Label | Serving Role | Promoted to Primary | Primary Behavior / Evidence Summary |
|---|---|---|---|---|---|---|
| `sage_pilot_q1_2024` | `1.0.0` | `SAGEForecaster` | `SAGE-LIGHTGBM` (API) / `SAGE-STATIC-LIGHTGBM` (Registry) | `primary_serving` | **Yes** | Global split-conformal + $\Gamma$-envelope (`[0.0..2.0]`). $D_{\text{sens}}$ is constant (`3.3206 µg/m³` in natural calibration) across rows; abstains primarily on `age_hours > 12h`. SHA-256 `e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115`. |
| `sage_adaptive_q1_2024` | `2.0.0-shadow-m77` | `AdaptiveSAGEForecaster` | `ADAPTIVE-SAGE-LIGHTGBM` | `shadow_research` | **No** (`false`) | Forward timestamp-embargoed OOF local scale $\hat{s}(x)$ + causal $\Gamma$ grid. Preserves spatial robustness on `station_4` (`92.07%` vs `85.36%` MAR), but natural Spearman $\rho = +0.1749$ ($< 0.20$) and loses `+0.4708` natural Winkler vs `locally_weighted_mar`. |
| `locally_weighted_mar_q1_2024` | `1.0.0-shadow` | `AdaptiveSAGEForecaster(gamma=0.0)` | `LOCALLY-WEIGHTED-MAR` | `shadow_baseline` | **No** (`false`) | Isolates causal forward-OOF $\hat{s}(x)$ without $\Gamma$-inflation. Best LightGBM-family natural Winkler (`8.7217`) and 8-station LOSO Winkler (`4.6330`). |
| `lightgbm_cqr_q1_2024` | `1.0.0-shadow` | `ConformalizedQuantileRegression` | `LIGHTGBM-CQR` | `shadow_baseline` | **No** (`false`) | Split quantile conformal baseline (`11.4472` natural Winkler; `5.2551` LOSO Winkler). |
| `persistence_split_q1_2024` | `1.0.0-shadow` | `PersistenceForecaster+SplitConformal` | `PERSISTENCE-SPLIT` | `shadow_baseline` | **No** (`false`) | Autoregressive baseline (`3.1602` natural Winkler; `3.6798` LOSO Winkler). |

---

## 3. Primary Artifact Cryptographic & Training Provenance

From [`artifacts/models/manifest.json`](../artifacts/models/manifest.json):
- **Model ID / Version:** `sage_pilot_q1_2024` / `1.0.0`
- **Artifact File:** `artifacts/models/sage_pilot_q1_2024.joblib`
- **Verified SHA-256:** `e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115`
- **Dataset Hash:** `33805c42097fc86e` (UK AURN Q1 2024 pilot network, `n_stations = 8`, `n_train = 8,884`, `n_cal = 2,937`, `n_test = 3,496`)
- **Features (13):** `last_pm25`, `lag_1h`, `lag_2h`, `lag_24h`, `rolling_mean_6h`, `rolling_std_6h`, `rolling_mean_24h`, `neighbor_idw_pm25`, `age_hours`, `hour_sin`, `hour_cos`, `dow_sin`, `dow_cos`
- **Calibration Hyperparameters:** `nominal_coverage = 0.90`, `gamma_grid = [0.0, 0.5, 1.0, 1.5, 2.0]`, `max_disagreement_threshold = 8.0`, `max_envelope_width_threshold = 50.0`

---

## 4. Quantitative Reason `AdaptiveSAGEForecaster` Remains Shadow-Only

From [`docs/audit/adaptive_sage_results.json`](audit/adaptive_sage_results.json) (`SG-M77-ADAPTIVE-20261125-163505`, strict complete-timestamp splits + 2.0h horizon+embargo buffer):

| Metric | `persistence` | `locally_weighted_mar` ($\gamma=0$) | `sage_adaptive_tau8` / `cal95` | `sage_static` (`SAGEForecaster`) |
|---|---:|---:|---:|---:|
| **Natural PICP (`published`)** | `97.02%` | `99.85%` | `99.85%` | `99.93%` |
| **Natural MPIW (`published`, µg/m³)** | `2.0000` | `8.5845` | `9.0680` / `8.9205` | `17.0034` |
| **Natural Winkler** | `3.1602` | `8.7217` | `9.1948` | `17.0617` |
| **Natural Spearman $\rho(D_{\text{sens}}, |e|)$** | — | `0.0000` | **`+0.1749`** *(fails `>0.20`)* | `0.0000` (const `3.3206`) |
| **MNAR $\gamma=1.5$ Winkler** | `4.2004` | `8.9907` | `9.4282` | `17.1000` |
| **MNAR $\gamma=1.5$ Spearman $\rho$** | — | `0.0000` | **`+0.2627`** *(passes `>0.20`)* | `0.0000` |
| **LOSO 8-Station Mean Winkler** | `3.6798` | `4.6330` | `6.5871` | `9.2190` |

**Audit Conclusion:** Local residual scaling $\hat{s}(x)$ produces substantial interval-efficiency gains over `Static SAGE` (`17.06 -> 8.72` natural Winkler), but adding the $\Gamma$ sensitivity inflation worsens paired natural Winkler by `+0.4708` (`[+0.4491, +0.4896]` 95% CI) and LOSO paired Winkler by `+1.5672` vs `locally_weighted_mar`. Promoting Adaptive SAGE directly into primary API serving without proving a live operational advantage would misrepresent its current performance.

---

## 5. Prespecified Criteria for Any Future Serving Promotion

Before a shadow candidate (`AdaptiveSAGEForecaster` or `locally_weighted_mar`) can replace `sage_pilot_q1_2024`:
1. **Shadow Sample Threshold:** At least `100` post-cutoff matched ground-truth observations per regime/evaluation window (`calibration_claim_eligible == True` in `src/sensorguard/shadow_eval.py`).
2. **Paired Efficiency & Coverage Gate:** Statistically credible Winkler improvement or parity (`delta_winkler <= 0` upper 95% CI vs `locally_weighted_mar` and Static SAGE) without sacrificing nominal coverage (`>= 0.90` on matched MAR checks) or triggering unacceptable false abstention spikes.
3. **Artifact & Signoff Gate:** Versioned `.joblib` + SHA-256 manifest generated offline, backward-compatible `/api/v1/model/status` verification, updated [`artifacts/models/model_registry.json`](../artifacts/models/model_registry.json), and explicit human operator approval.
