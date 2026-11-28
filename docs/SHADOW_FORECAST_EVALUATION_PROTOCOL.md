# SensorGuard AI — Prospective Shadow Forecast Evaluation Protocol (Phase 8.2)

**Module:** [`src/sensorguard/shadow_eval.py`](../src/sensorguard/shadow_eval.py)  
**Database Persistence:** [`forecast_runs`](../src/sensorguard/db/schema.py) (`execution_mode = "shadow"`) · `forecasts` · `forecast_feature_snapshots`  
**Verification Suite:** [`tests/test_shadow_eval.py`](../tests/test_shadow_eval.py)

---

## 1. Objective

Evaluate candidate forecasting and uncertainty quantification models prospectively against real arriving observations **without** affecting the primary serving API (`/api/v1/stations/{id}/forecasts`), and **without** drawing calibration conclusions until sufficient post-cutoff outcomes arrive.

---

## 2. Models Evaluated in Shadow Mode

Implemented in `SHADOW_MODEL_SPECS` (`src/sensorguard/shadow_eval.py`):

| Shadow Key | Model ID | Version | Algorithm Label | Estimator Architecture |
|---|---|---|---|---|
| `persistence` | `persistence_split_shadow` | `1.0.0-shadow` | `PERSISTENCE-SPLIT` | `PersistenceForecaster` + `SplitConformalCalibrator` |
| `locally_weighted_mar` | `locally_weighted_mar_shadow` | `1.0.0-shadow` | `LOCALLY-WEIGHTED-MAR` | `AdaptiveSAGEForecaster(gamma_grid=[0.0])` ($\gamma=0$ MAR control) |
| `lightgbm_cqr` | `lightgbm_cqr_shadow` | `1.0.0-shadow` | `LIGHTGBM-CQR` | `LightGBMQuantileForecaster` + `ConformalizedQuantileRegression` |
| `sage_static` | `sage_pilot_q1_2024` | `1.0.0` | `SAGE-STATIC-LIGHTGBM` | `SAGEForecaster` (mirrors primary serving baseline) |
| `sage_adaptive` | `sage_adaptive_q1_2024` | `2.0.0-shadow-m77` | `ADAPTIVE-SAGE-LIGHTGBM` | `AdaptiveSAGEForecaster(gamma_grid=[0.0, 0.5, 1.0, 1.5, 2.0])` |

---

## 3. Strictly Causal Prospective Execution Flow

[`generate_prospective_shadow_forecasts`](../src/sensorguard/shadow_eval.py) enforces strict pre-outcome forecasting:

1. **Point-in-Time Replay Filter:** Queries observations at `decision_cutoff` using `PointInTimeReplayEngine(session).query_as_of(..., decision_cutoff=cutoff, mode="prospective")`, enforcing:
   $$\text{observed\_at} \le t_{\text{cutoff}} \;\land\; \text{fetched\_at} \le t_{\text{cutoff}} \;\land\; \text{persisted\_at} \le t_{\text{cutoff}}$$
2. **Pre-Target Feature Extraction:** Builds spatiotemporal lags (`last_pm25`, `lag_1h`, `lag_2h`, `lag_24h`, rolling stats, neighbor IDW, `age_hours`, calendar harmonics) strictly from rows visible at `decision_cutoff` targeting `target_time = decision_cutoff + 1h` with `pm25 = NaN` and `reported = False` on the target slot.
3. **Immutable Feature Snapshot Storage:** Persists `ForecastFeatureSnapshot` (`features_json`, `feature_hash = SHA-256`, `created_at = decision_cutoff`) **before** any future target timestamp arrives.
4. **Shadow Record Persistence:** Creates `ForecastRun(execution_mode="shadow", data_mode=..., issued_at <= decision_cutoff)` and one `ForecastRecord` per model/station/cutoff recording `point_forecast`, `lower_bound`, `upper_bound`, `status` (`PUBLISH` / `ABSTAIN`), `abstain_reason`, `d_sensitivity`, `newest_source_observed_at`, and JSON `scenario_bounds`.

---

## 4. Delayed Label Reconciliation & Anti-Hype Sample Gate

[`reconcile_shadow_evaluation`](../src/sensorguard/shadow_eval.py) joins shadow `ForecastRecord` rows against arrived `VALID` observations (selecting latest `version` per `(sensor_id, observed_at)`):

1. **Minimum Sample Size Guard (`min_matched_outcomes = 100`):**
   - If any shadow model has `matched_count < min_matched_outcomes`, `status` is fixed to **`"INSUFFICIENT_OBSERVED_OUTCOMES"`** and `calibration_claim_eligible` is set to **`False`**.
   - Exposed publicly on `GET /api/v1/pilot/readiness` (`shadow_evaluation_summary`) and rendered in the staging dashboard so early partial samples never trigger premature claims.
2. **Arrived vs Missing Outcome Separation:**
   - Computes `matched_count`, `unmatched_missing_truth_count`, `missing_label_rate`, and `delayed_arrival_count` (`fetched_at - target_time > 1.5h`).
   - Missing targets remain explicitly unlabelled (`unmatched_missing_truth_count`) and are never imputed as covered or zero-error.
3. **Quality Incident Accounting:** Counts non-`VALID` observations (`data_quality_incident_count`) across monitored sensors in the evaluation period.
4. **Metrics Reported per Model (Across Matched Labels Only):**
   - `publication_availability` (`published_matched_count / matched_count`)
   - `published_picp` and 95% Wilson CI (`published_picp_ci95`)
   - `published_mpiw` (µg/m³) and `published_winkler` (interval score with penalty $2/\alpha$)
   - `mae` (across all matched forecasts)
5. **MNAR Boundary Policy:** Preserves an explicit governance warning reminding operators that unarrived observations (`missing_label_rate`) cannot be confirmed as ignorable under unrestricted MNAR censoring, and simulated outage exercises (`simulated_outage`) are never treated as genuine live-sensor MNAR validation.
