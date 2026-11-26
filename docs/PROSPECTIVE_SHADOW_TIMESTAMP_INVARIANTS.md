# SensorGuard AI — Prospective Shadow Timestamp Invariants & Evaluation Protocol (Phase 8.7)

**Implementation:** [`src/sensorguard/models/shadow_artifacts.py`](../src/sensorguard/models/shadow_artifacts.py) · [`src/sensorguard/shadow_eval.py`](../src/sensorguard/shadow_eval.py) · [`src/sensorguard/db/schema.py`](../src/sensorguard/db/schema.py) · [`alembic/versions/008_m87_artifact_timing_gate.py`](../alembic/versions/008_m87_artifact_timing_gate.py)  
**Test Suite:** [`tests/test_shadow_eval.py`](../tests/test_shadow_eval.py) · [`tests/test_live_pilot_dry_run.py`](../tests/test_live_pilot_dry_run.py)

---

## 1. Immutable JSON Artifact Integrity (Zero Untrusted Deserialization)

Every prospective shadow model (`persistence`, `locally_weighted_mar`, `lightgbm_cqr`, `sage_static`, `sage_adaptive`) is serialized write-once by [`serialize_shadow_model_artifact`](../src/sensorguard/models/shadow_artifacts.py) into canonical UTF-8 `.json` artifacts (`schema_format = "sensorguard.shadow_artifact.v1"`):
- **Zero `pickle` / `joblib` deserialization** on the shadow prediction path: linear/logistic propensity and Ridge pipelines serialize explicit numeric weights, conformal calibrators serialize scalar radii, and LightGBM models serialize official text trees via `booster_.model_to_string()`.
- **Byte-Level SHA-256 Anchoring:** SHA-256 is computed over exact saved `.json` file bytes and embedded into `artifact_immutable_ref = "{model_id}@{model_version}#{sha256[:12]}"`, `ForecastRun.model_sha256`, and `ForecastRecord.artifact_sha256`.
- **Training Provenance Recorded Per Artifact & ForecastRun:** `model_created_at`, `training_completed_at`, `training_data_cutoff`, `training_data_availability_cutoff`, `calibration_cutoff`, `calibration_availability_cutoff`, `code_version`, `feature_schema_version`, and `feature_schema_hash`.
- **Fail-Closed Verification in `prospective_shadow`:** [`verify_and_load_shadow_artifact`](../src/sensorguard/models/shadow_artifacts.py) rejects missing files, tampered bytes, non-JSON files, constant placeholders (`sha256(b"shadow-suite-m86")`), dataset/fit hashes (`#unpinned`, `#data_`, `#fit_`), incompatible feature schemas, and labels whose availability cutoff post-dates `training_completed_at` or `model_created_at`.

---

## 2. Mandatory Forecast Timing, Lead Time & Cutoff Honesty

Every `ForecastRecord` and `ForecastRun` persists six distinct timing measurements (never collapsing `persisted_at` to `decision_cutoff` or claiming availability before commit):

| Field | Semantics | Prospective Invariant |
|---|---|---|
| `training_completed_at` | Actual UTC time shadow suite fit & calibration finished | `training_data_availability_cutoff <= calibration_availability_cutoff <= training_completed_at <= generated_at` |
| `decision_cutoff` | Declared end of causal telemetry window visible via `PointInTimeReplayEngine` | `decision_cutoff <= target_time - 1h` |
| `target_time` | Verification hour (`decision_cutoff + 1h`) | Absent from DB `observations` before generation AND commit |
| `generated_at` | Actual UTC wall-clock inference start time | `(target_time - generated_at).total_seconds() >= min_lead_seconds` |
| `persisted_at` | Actual UTC wall-clock transaction commit time | `(target_time - persisted_at).total_seconds() >= min_lead_seconds` (`> 0`); `obs.persisted_at > persisted_at` |
| `effective_lead_seconds` | Actual lead remaining at DB commit: `(target_time - persisted_at).total_seconds()` | Strictly positive (`>= min_lead_seconds`, default **`300.0`s / 5 minutes**) |
| `available_at_decision_cutoff` | Boolean truthfulness flag (`persisted_at <= decision_cutoff`) | Strictly `False` whenever generated or persisted after `decision_cutoff` (counted separately under `post_cutoff_pre_target_count`) |

---

## 3. Hard Mode Separation & Evaluation-Time Quarantine

1. **`generate_prospective_shadow_forecasts(..., min_lead_seconds=300.0)`:** Enforces positive minimum lead both before estimation and immediately before `session.commit()`, handles concurrent worker races on `idempotency_key` cleanly, and aborts if a concurrent poll persists target truth mid-inference.
2. **`generate_retrospective_replay_forecasts(...)` (`evaluation_mode = "retrospective_replay"`):** Explicitly isolated under `"retrospective_summary"` and `retrospective_run_count`; never mixed into prospective metrics or calibration gates.
3. **`reconcile_shadow_evaluation()` Quarantine Counters:** Audits every `prospective_shadow` record for pinned byte SHA-256 (`artifact_violation_count`), causality (`backdated_violation_count`), and effective lead >= `min_lead_seconds` (`lead_time_violation_count`). Any violation blocks `calibration_claim_eligible` and sets `artifact_integrity_verified = False`.
4. **Promotion Gate:** `calibration_claim_eligible` requires zero backdated/lead/artifact violations, `matched_count >= 100`, and `unique_target_hours >= 24` (`>= 168` hours / 7 continuous days required prior to live promotion). `adaptive_sage_promotion_eligible` remains **`False`** unless `calibration_claim_eligible` holds AND `ADAPTIVE-SAGE-LIGHTGBM` strictly outperforms `LOCALLY-WEIGHTED-MAR` on paired common-published Winkler.
