# SensorGuard AI — Prospective Shadow Timestamp Invariants & Evaluation Protocol (Phase 8.6)

**Implementation:** [`src/sensorguard/shadow_eval.py`](../src/sensorguard/shadow_eval.py) · [`src/sensorguard/db/schema.py`](../src/sensorguard/db/schema.py) · [`alembic/versions/007_m86_safety_fencing.py`](../alembic/versions/007_m86_safety_fencing.py)  
**Test Suite:** [`tests/test_shadow_eval.py`](../tests/test_shadow_eval.py)

---

## 1. Mandatory Timestamp Separation

Every shadow forecast persisted into PostgreSQL or SQLite carries five strictly distinct temporal fields (none derived from or conflated with `decision_cutoff`):

| Field | Semantics | Non-Backdating Invariant |
|---|---|---|
| `decision_cutoff` | End of input telemetry window visible via `PointInTimeReplayEngine`. | `decision_cutoff <= target_time - 1h` |
| `target_time` | Forecast verification timestamp (`decision_cutoff + 1h`). | Never in `observations` at forecast persistence time for `prospective_shadow` |
| `generated_at` | Actual UTC wall-clock timestamp when estimator execution occurred. | Recorded live via `datetime.now(UTC)`; **for `prospective_shadow`, `generated_at < target_time`** |
| `persisted_at` | Actual UTC wall-clock commit timestamp immediately prior to `session.commit()`. | **`persisted_at < target_time`** AND **`obs.persisted_at > forecast.persisted_at`** |
| `created_at` | Record row persistence timestamp. | Equal to `persisted_at`; **never backdated to `decision_cutoff`** |

In addition, every `ForecastRun` records exact model provenance (`model_created_at`, `artifact_immutable_ref`) and a unique SHA-256 `idempotency_key`, while `forecasts` enforces database-level uniqueness via `uq_forecasts_run_station_target(run_id, station_id, target_time)`.

---

## 2. Hard Mode Separation (`prospective_shadow` vs `retrospective_replay`)

1. **`generate_prospective_shadow_forecasts(...)` (`evaluation_mode = "prospective_shadow"`):**
   - Inspects actual `datetime.now(UTC)` BEFORE generation AND immediately prior to `session.commit()`.
   - Queries `observations` for any existing measurement matching `target_time`.
   - Raises `ValueError("Backdated prospective violation: ...")` and rolls back whenever `actual_generated_at >= target_time`, `actual_persisted_at >= target_time`, OR target truth already exists in DB.
2. **`generate_retrospective_replay_forecasts(...)` (`evaluation_mode = "retrospective_replay"`):**
   - Supports historical Q1 2024 replay auditing strictly using `PointInTimeReplayEngine(mode="prospective")` input filtering as-of `decision_cutoff`, while still recording real wall-clock `generated_at` and `persisted_at`.
   - Isolated under `"retrospective_summary"` and `retrospective_run_count`; **never mixed** into prospective evaluation metrics or promotion gates.
3. **Evaluation-Time Tamper Quarantine:** [`reconcile_shadow_evaluation`](../src/sensorguard/shadow_eval.py) checks every `prospective_shadow` row for `(generated_at < target_time) & (persisted_at < target_time) & (matched_obs.persisted_at > rec.persisted_at)`. Any violating row increments `backdated_violation_count`, is excluded from prospective metrics, and forces `calibration_claim_eligible = False`.

---

## 3. Required Prospective Evidence & Promotion Gate (`reconcile_shadow_evaluation`)

Prospective eligibility (`calibration_claim_eligible = True`) requires ALL of:
- Zero backdated violations (`backdated_violation_count == 0`).
- Minimum matched outcomes: `matched_count >= 100` (`min_matched_outcomes=100`).
- Minimum independent horizon span: `unique_target_hours >= 24` (`min_unique_hours=24`, recommended `>= 168` hours / 7 days prior to live promotion sign-off).

Full output reported per model (`persisted_picp`, 95% Wilson CI, 400-bootstrap station-day block CI `published_picp_block_ci95`, `published_mpiw`, `published_winkler`, `mae`, `d_sens_mean`, `publication_availability`, `freshness` mean/max source age, `delayed_arrival_count`, `missing_label_rate`, `data_quality_incident_count`, `provider_incident_count`, `high_pollution` subgroup `>= max(15.0, p75)`, `long_outage` subgroup `age_hours >= 6.0`, and paired matched-availability deltas vs `LOCALLY-WEIGHTED-MAR`, `PERSISTENCE-SPLIT`, and `LIGHTGBM-CQR`).

**Anti-Promotion Rule:** `adaptive_sage_promotion_eligible` remains **`False`** unless `calibration_claim_eligible` holds AND `ADAPTIVE-SAGE-LIGHTGBM` strictly beats `LOCALLY-WEIGHTED-MAR` on paired common-published Winkler without degrading coverage.
