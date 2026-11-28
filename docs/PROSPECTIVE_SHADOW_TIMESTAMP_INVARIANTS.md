# SensorGuard AI — Prospective Shadow Timestamp Invariants & Evaluation Protocol

**Implementation:** [`src/sensorguard/models/shadow_artifacts.py`](../src/sensorguard/models/shadow_artifacts.py) · [`src/sensorguard/shadow_eval.py`](../src/sensorguard/shadow_eval.py) · [`src/sensorguard/db/schema.py`](../src/sensorguard/db/schema.py) · [`alembic/versions/009_m87_commit_timing.py`](../alembic/versions/009_m87_commit_timing.py) · [`alembic/versions/010_single_use_stage1_proof.py`](../alembic/versions/010_single_use_stage1_proof.py)  
**Test Suite:** [`tests/test_shadow_eval.py`](../tests/test_shadow_eval.py) · [`tests/test_live_pilot_dry_run.py`](../tests/test_live_pilot_dry_run.py)

---

## 1. Immutable JSON Artifact Integrity (Zero Untrusted Deserialization)

Every prospective shadow model (`persistence`, `locally_weighted_mar`, `lightgbm_cqr`, `sage_static`, `sage_adaptive`) is serialized write-once by [`serialize_shadow_model_artifact`](../src/sensorguard/models/shadow_artifacts.py) into canonical UTF-8 `.json` artifacts (`schema_format = "sensorguard.shadow_artifact.v1"`):
- **Zero `pickle` / `joblib` deserialization** on the shadow prediction path: linear/logistic propensity and Ridge pipelines serialize explicit numeric weights, conformal calibrators serialize scalar radii, and LightGBM models serialize official text trees via `booster_.model_to_string()`.
- **Byte-Level SHA-256 Anchoring:** SHA-256 is computed over exact saved `.json` file bytes and embedded into `artifact_immutable_ref = "{model_id}@{model_version}#{sha256[:12]}"`, `ForecastRun.model_sha256`, and `ForecastRecord.artifact_sha256`.
- **Training Provenance Recorded Per Artifact & ForecastRun:** `model_created_at`, `training_completed_at`, `training_data_cutoff`, `training_data_availability_cutoff`, `calibration_cutoff`, `calibration_availability_cutoff`, `code_version`, `feature_schema_version`, and `feature_schema_hash`.
- **Fail-Closed Verification in `prospective_shadow`:** [`verify_and_load_shadow_artifact`](../src/sensorguard/models/shadow_artifacts.py) rejects missing files, tampered bytes, non-JSON files, constant placeholders (`sha256(b"shadow-suite-m86")`), dataset/fit hashes (`#unpinned`, `#data_`, `#fit_`), incompatible feature schemas, and labels whose availability cutoff post-dates `training_completed_at` or `model_created_at`.

---

## 2. Mandatory Two-Phase Completion Proof & Timestamp Invariants

Every `ForecastRecord` and `ForecastRun` persists explicit two-phase commit completion state (`commit_proof_status`) and seven distinct timing fields (never collapsing `persisted_at` to `decision_cutoff` or claiming prospective validity when `commit_completed_at` is missing):

| Field | Semantics | Prospective Invariant |
|---|---|---|
| `commit_proof_status` | Two-phase state: `PENDING_COMPLETION_PROOF` (Tx1) -> `VERIFIED_PROSPECTIVE` (Tx2) or `QUARANTINED_TIMING_VIOLATION` | Strictly **`VERIFIED_PROSPECTIVE`** on both `ForecastRun` & `ForecastRecord` (zero fallback when incomplete) |
| `training_completed_at` | Actual UTC time shadow suite fit & calibration finished | `training_data_availability_cutoff <= calibration_availability_cutoff <= training_completed_at <= generated_at` |
| `decision_cutoff` | Declared end of causal telemetry window visible via `PointInTimeReplayEngine` | `decision_cutoff <= target_time - 1h` |
| `target_time` | Verification hour (`decision_cutoff + 1h`) | Absent from DB `observations` before generation, pre-commit, AND Tx2 completion |
| `generated_at` | Actual UTC wall-clock inference start time | `(target_time - generated_at).total_seconds() >= min_lead_seconds` |
| `pre_commit_at` | Timestamp captured right before Tx1 `session.commit()` | `generated_at <= pre_commit_at`; `(target_time - pre_commit_at) >= min_lead_seconds + commit_safety_margin_seconds` (`315s`) |
| `commit_completed_at` / `persisted_at` | Conservative completion timestamp written in Tx2 **after** Tx1 `session.commit()` returns | Non-null (`NOT NULL`), `pre_commit_at <= commit_completed_at <= persisted_at < target_time`; `(target_time - persisted_at).total_seconds() >= min_lead_seconds` (`>= 300s`); `obs.persisted_at > commit_completed_at` |
| `effective_lead_seconds` | Actual lead remaining at Tx2 commit completion: `(target_time - persisted_at).total_seconds()` | Strictly positive (`>= min_lead_seconds`, default **`300.0`s / 5 minutes**) |
| `available_at_decision_cutoff` | Boolean truthfulness flag (`persisted_at <= decision_cutoff`) | Strictly `False` whenever generated or persisted after `decision_cutoff` (counted separately under `post_cutoff_pre_target_count`) |

---

## 3. Hard Mode Separation, Crash Quarantine & Evaluation Gate

1. **Mandatory Tx1 -> Tx2 State Machine ([`generate_prospective_shadow_forecasts`](../src/sensorguard/shadow_eval.py)):**
   - **Tx1 (`PENDING_COMPLETION_PROOF`):** Enforces generation lead & pre-commit safety margin (`315s`) and absent target truth before committing initial runs/records with `commit_completed_at = NULL`.
   - **Tx2 (`VERIFIED_PROSPECTIVE` or `QUARANTINED_TIMING_VIOLATION`):** Immediately captures `commit_completed_at` after Tx1 commit and re-verifies lead `>= min_lead_seconds` and zero competing target observations. Crossings/competing inserts transition both run and records to `QUARANTINED_TIMING_VIOLATION` and raise `ValueError`. Failures during Tx2 roll back, leaving `PENDING_COMPLETION_PROOF` (`commit_completed_at = NULL`).
   - **Safe Idempotent Recovery:** Retrying an incomplete `PENDING_COMPLETION_PROOF` batch (`idempotency_behavior="return"`) verifies record & artifact completeness and either finalizes in-place via Tx2 with zero duplicate rows (if still timely with no competing truth) or permanently transitions in-place to `QUARANTINED_TIMING_VIOLATION`. Quarantined batches are never recoverable.
2. **`generate_retrospective_replay_forecasts(...)` (`evaluation_mode = "retrospective_replay"`):** Explicitly stamped `RETROSPECTIVE_REPLAY` and isolated under `"retrospective_summary"` and `retrospective_run_count`; never mixed into prospective evidence.
3. **`reconcile_shadow_evaluation()` Strict Fail-Closed Quarantine:** Requires `commit_proof_status == "VERIFIED_PROSPECTIVE"` and non-null `pre_commit_at` & `commit_completed_at` (no permissive missing-timestamp bypass). Tracks `commit_proof_violation_count`, `artifact_violation_count`, `backdated_violation_count`, and `lead_time_violation_count`. Any incomplete/unverified/violating record sets `truly_prospective_count == 0` for that record and forces `calibration_claim_eligible = False` and `artifact_integrity_verified = False`.
4. **Promotion Gate:** `calibration_claim_eligible` requires zero proof/backdated/lead/artifact violations, `matched_count >= 100`, and `unique_target_hours >= 24` (`>= 168` hours / 7 continuous days required prior to live promotion). `adaptive_sage_promotion_eligible` remains **`False`** unless `calibration_claim_eligible` holds AND `ADAPTIVE-SAGE-LIGHTGBM` strictly outperforms `LOCALLY-WEIGHTED-MAR` on paired common-published Winkler.
