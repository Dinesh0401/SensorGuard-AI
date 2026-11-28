# SensorGuard AI — Milestone 7.7: Scientific Consistency & Causal Boundary Audit

**Audit Date:** 2026-11-25  
**Repository / Branch:** `Dinesh0401/SensorGuard-AI` · `feat/milestone-3-forecasting-uncertainty`  
**Verified Starting Commit:** [`ba28d43900288ecb63c73ab8741ffd9294bd4d04`](https://github.com/Dinesh0401/SensorGuard-AI/commit/ba28d43900288ecb63c73ab8741ffd9294bd4d04)  
**Reconciled Adaptive Study ID:** `SG-M77-ADAPTIVE-20261125-163505`  
**Dataset Hash:** `33805c42097fc86e` (Archived UK AURN Q1 2024 Network: `2024-01-01T01:00:00Z` – `2024-03-31T23:00:00Z`, 8 stations, `14,556` raw valid obs)  
**Public Production Status:** **NO-GO** *(Private staging, reproducible CI, and bounded opt-in pilot validation only)*

---

## Executive Audit Summary

Milestone 7.7 audited and resolved four remaining scientific, causal-splitting, and fixture-governance gaps identified after Milestone 7.6:

1. **Task A — Scientific Consistency & Outdated Metric Reconciliation:** Superseded stale study identifiers (`SG-M74-ADAPTIVE-20261125-120901` and mixed regime citations) across [`SAGE_ADAPTIVE_RESEARCH_PROTOCOL.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/SAGE_ADAPTIVE_RESEARCH_PROTOCOL.md) and [`MILESTONE_7_FIELD_VALIDATION.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/MILESTONE_7_FIELD_VALIDATION.md), locking every reported table strictly to [`docs/audit/adaptive_sage_results.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/adaptive_sage_results.json). Explicitly disclosed that natural-regime Spearman discrimination (`+0.1749`) does **not** meet the prespecified `> 0.20` criterion without shifting thresholds post-hoc, and retracted prior overstated `8/8` LOSO Winkler claims once strict timestamp causality was enforced.
2. **Task B — Complete-Timestamp Splitting, Embargo Enforcement & Perturbation Invariance:** Eliminated row-position boundary leakage in [`AdaptiveSAGEForecaster.compute_causal_oof_residuals`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage_adaptive.py) so multi-station observations at identical UTC timestamps never split across training and validation, enforced an explicit `horizon_hours + embargo_hours = 2.0h` label cutoff buffer (`embargo_gap_hours = 2.0`), added strict causality guards rejecting untrustworthy or future-leaking `timestamp`/`issued_at`/`feature_timestamp`/`target_available_at` metadata, and proved exact bit-for-bit OOF invariance under future fold and embargo-window perturbations in [`tests/test_sage_adaptive.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_sage_adaptive.py).
3. **Task C — Paired Causal Isolation & Extended Blackout Evaluation:** Evaluated `Adaptive SAGE`, `Locally-Weighted MAR (gamma=0)`, `LightGBM CQR`, `LightGBM ACI`, `Persistence`, and `Static SAGE` across all 5 regimes (`natural`, `mcar_20`, `mnar_gamma_1.5`, `burst_24h`, and a new `extended_72h` 2-station 72h blackout) plus all 8 LOSO stations under exact shared evaluation masks and matched abstention budgets with station-day block bootstrap 95% confidence intervals.
4. **Task D — Reproducible Fixture Provenance & Non-Destructive Governance:** Audited [`data/fixtures/sensorguard_pilot_fixture.sql.gz`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/data/fixtures/sensorguard_pilot_fixture.sql.gz) (`842,743` bytes, SHA-256 `11e9d49a67cdf94cf8b322f4133165f3d63582cde8b6aafd8e0be1af1418180c`), recorded UK OGL & ODC-BY attribution in [`data/fixtures/PROVENANCE_AND_LICENSE.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/data/fixtures/PROVENANCE_AND_LICENSE.json), scanned all 17 raw payloads (0 secrets/HTTP headers found), and hardened [`scripts/seed_pilot_db.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/seed_pilot_db.py) with content checksum verification and non-destructive overwrite protection.

---

## Task A — Regime Separation & Outdated Metric Reconciliation

Previously reported numbers mixed M7.6 row-split regimes (`6.3206` width / `+0.1756` $\rho$ in natural vs `6.3913` / `+0.2727` in MNAR) and retained an older M7.4 study header (`SG-M74-ADAPTIVE-20261125-120901`).

Under the audited M7.7 causal run (`SG-M77-ADAPTIVE-20261125-163505`), exact regime metrics from [`adaptive_sage_results.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/adaptive_sage_results.json) are strictly separated:

| Metric (`sage_adaptive_cal95`) | Pre-Audit M7.6 `natural` (`ba28d43`) | **M7.7 Causal `natural`** | Pre-Audit M7.6 `mnar_gamma_1.5` (`ba28d43`) | **M7.7 Causal `mnar_gamma_1.5` (3-seed)** | **M7.7 Causal `extended_72h`** |
|---|---:|---:|---:|---:|---:|
| **Overall PICP** | `99.23%` | **`99.85%`** `[.9962, .9994]` | `98.70%` | **`99.41%`** | **`99.85%`** |
| **Overall MPIW (µg/m³)** | `6.3206` | **`9.0717`** (`8.9205` pub) | `6.3913` | **`9.2149`** (`8.9191` pub) | **`9.0670`** (`8.9177` pub) |
| **$D_{\text{sens}}$ Mean ± Std** | `0.7318 ± 0.1890` | **`0.2851 ± 0.0664`** | `0.7450 ± 0.1430` | **`0.2981 ± 0.1098`** | **`0.2943 ± 0.0799`** |
| **Spearman $\rho(D_{\text{sens}}, |e|)$** | `+0.1756` | **`+0.1749`** *(fails `>0.20`)* | `+0.2727` | **`+0.2627`** *(passes `>0.20`)* | **`+0.2545`** *(passes `>0.20`)* |
| **Winkler Score** | `6.5090` | **`9.1948`** | `6.9970` | **`9.4282`** | **`9.1901`** |
| **Abstention Rate** | `0.51%` | **`1.47%`** (`1.47%` cal95 / `0.44%` tau8) | `1.09%` | **`2.81%`** (`1.09%` tau8) | **`4.91%`** (`3.89%` tau8) |

- **Prespecified Criterion C1 Accounting:** The prespecified threshold $\rho > 0.20$ was **not** satisfied under natural conditions (`+0.1749`) or 24-hour single-station burst outage (`+0.1782`). It **was** satisfied under `mcar_20` (`+0.2419`), `mnar_gamma_1.5` (`+0.2627`), `extended_72h` (`+0.2545`), and across all `8 / 8` held-out LOSO stations (`+0.2534` to `+0.4539`).

---

## Task B — Strict Temporal Causality Invariant Verification

### Causal Boundary Specification (`AdaptiveSAGEConfig`: `horizon_hours=1.0`, `embargo_hours=1.0`)
1. **Complete Unique Timestamp Splitting:** Multi-station rows sharing any timestamp $t$ belong atomically to the same chronological block $T_k$ (`same_hour_disjoint = True`).
2. **Causal Inequality Enforced:**
   $$\max_{i \in \text{train}_k} \text{label\_available\_at}_i \;\le\; \min(T_k) - (\text{horizon\_hours} + \text{embargo\_hours}) \;=\; t_{\text{val\_decision\_start}} - 1.0\text{h}$$
   - **Audited Fold 1:** `2,899` training rows (`max_avail = 2024-01-19T06:00Z`) $\to$ `2,786` val rows (`min_ts = 2024-01-19T08:00Z`, decision start `07:00Z`, `embargo_gap_hours = 2.0`).
   - **Audited Fold 2:** `5,686` training rows (`max_avail = 2024-02-06T10:00Z`) $\to$ `3,191` val rows (`min_ts = 2024-02-06T12:00Z`, decision start `11:00Z`, `embargo_gap_hours = 2.0`).
3. **Perturbation Regression & Metadata Rejection Evidence:**
   - `test_adaptive_sage_strict_causal_forward_splits_no_future_leakage`: Verified bit-for-bit identical Fold 1 OOF predictions & residuals (`rtol=0, atol=0`) when Fold 2 targets/features AND adjacent embargo hour targets (`t_val_start - 1h`) are corrupted (`+99,999` / `-50,000`), even on randomly shuffled multi-station DataFrames.
   - `test_adaptive_sage_rejects_untrustworthy_or_noncausal_timestamps`: Verified `ValueError` rejection for missing timestamps, `NaT`, `issued_at > timestamp - horizon`, `feature_timestamp > decision_time`, and `target_available_at < timestamp`.

---

## Task C — Paired Component Attribution & Preserved Negative Results

Using **station-day clustered block bootstrap 95% confidence intervals** ($800$ resamples across $135$–$137$ blocks in `seed=42`) on **exact identical masks**:

1. **Benefit of Local Scale Estimator $\hat{s}(x)$ Alone (`locally_weighted_mar` vs `lightgbm_split` on `all_valid` `N=2,727`):**
   - `natural`: $\Delta\text{MPIW} = \mathbf{-4.6398}$ `[-4.91, -4.37]`, $\Delta\text{Winkler} = \mathbf{-4.6250}$ `[-4.90, -4.35]`, $\Delta\text{PICP} = 0.0000$.
   - `mnar_gamma_1.5`: $\Delta\text{MPIW} = \mathbf{-4.4723}$ `[-4.73, -4.22]`, $\Delta\text{Winkler} = \mathbf{-4.4337}$ `[-4.73, -4.09]`, $\Delta\text{PICP} = -0.0040$ `[-0.010, 0.000]`.
   - `extended_72h`: $\Delta\text{MPIW} = \mathbf{-4.6030}$ `[-4.88, -4.32]`, $\Delta\text{Winkler} = \mathbf{-4.5882}$ `[-4.87, -4.31]`, $\Delta\text{PICP} = 0.0000$.
2. **Incremental Contribution of MNAR $\gamma$-Grid (`sage_adaptive_tau8` vs `locally_weighted_mar` on identical published mask):**
   - `natural` (`N=2,715`): $\Delta\text{PICP} = 0.0000$, $\Delta\text{MPIW} = \mathbf{+0.4835}$ `[+0.46, +0.51]`, $\Delta\text{Winkler} = \mathbf{+0.4708}$ `[+0.45, +0.49]`.
   - `mnar_gamma_1.5` (`N=2,701`): $\Delta\text{PICP} = 0.0000$, $\Delta\text{MPIW} = \mathbf{+0.4964}$ `[+0.47, +0.52]`, $\Delta\text{Winkler} = \mathbf{+0.4673}$ `[+0.42, +0.50]`.
   - `extended_72h` (`N=2,621`): $\Delta\text{PICP} = 0.0000$, $\Delta\text{MPIW} = \mathbf{+0.4848}$ `[+0.46, +0.51]`, $\Delta\text{Winkler} = \mathbf{+0.4716}$ `[+0.45, +0.49]`.
   - **Matched Abstention Budget (`cal95` vs `MAR`, `N=2,683` common natural):** `cal95` loses by `+0.4636` Winkler `[+0.44, +0.49]`.
3. **Matched Abstention Budget (`cal95` vs `LightGBM CQR`, `Persistence`, `ACI`):**
   - vs `lightgbm_cqr` (`natural`, `N=2,687`): `cal95` improves Winkler by **`-2.3390`** `[-2.41, -2.27]` (`-2.2961` on MNAR, `-2.3629` on `extended_72h`) at equivalent coverage (`99.85%` vs `99.89%`).
   - vs `persistence` (`natural`, `N=2,687`) and `lightgbm_aci` (`natural`, `N=2,659`): **`persistence` wins Winkler by `6.0912`** `[5.42, 6.63]` and **`ACI` wins Winkler by `5.8613`** `[5.17, 6.34]` (though `cal95` carries higher coverage `+0.0238` vs persistence and `+0.0839` vs ACI).
4. **8-Fold LOSO Spatial Reassessment (Retracting Prior M7.4 `8/8` Claim):**
   - Once row-split same-hour leakage was fixed and all baselines were paired cleanly on LOSO, **`locally_weighted_mar` ($\gamma=0$) achieves the lowest LightGBM-family LOSO Winkler (`4.6330` mean across 8 stations, vs `5.2551` CQR, `6.5871` Adaptive SAGE `cal95`, `9.2190` Static SAGE)**, while `persistence` (`3.6798`) and `lightgbm_aci` (`3.9059`) remain narrowest overall.
   - `sage_adaptive` underperforms `locally_weighted_mar` on paired Winkler across all `8 / 8` stations (`mean_paired_tau8_minus_mar_winkler = +1.5672`) and underperforms `CQR` on `6 / 8` stations (`mean_paired_cal95_minus_cqr_winkler = +1.0504`).
   - Its concrete empirical contribution in LOSO is **distributional robustness under severe spatial shift on `station_4` (`Bexley - Slade Green`)**: where `CQR` drops to `81.72%` and `MAR` drops to `85.36%`, `sage_adaptive_cal95` preserves **`92.07%`** coverage (`Winkler = 11.1414` vs `11.4609` CQR).

---

## Task D — Reproducible Fixture Governance & Security Audit

- **Fixture Inventory & Checksums:**
  - Compressed SQL artifact: [`data/fixtures/sensorguard_pilot_fixture.sql.gz`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/data/fixtures/sensorguard_pilot_fixture.sql.gz) (`842,743` bytes).
  - Compressed SHA-256: `11e9d49a67cdf94cf8b322f4133165f3d63582cde8b6aafd8e0be1af1418180c`.
  - Decompressed SQL SHA-256: `c56a14d865933a9264091f9e050e1246a90aebf60001cad89099e038835aceac`.
  - Audited attribution & checksum manifest: [`data/fixtures/PROVENANCE_AND_LICENSE.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/data/fixtures/PROVENANCE_AND_LICENSE.json).
- **Attribution & License Audit (All 8 Stations Inspected):**
  - 6 EEA UK Defra AURN stations (`provider_location_id` `141, 153, 698, 804, 1795, 2312`): **ODC-BY** (Open Data Commons Attribution License; redistribution allowed with attribution).
  - 2 LAQN UK Defra stations (`154, 157`): **UK Open Government Licence v3.0 (UK OGL)** (redistribution allowed with attribution).
- **Raw Payload & Secret Scan Audit:**
  - All `17` `raw_ingest_batches` contain pure OpenAQ `/v3/sensors/{id}/hours` JSON response bodies (`meta` + `results`), matching recorded `response_hash` SHA-256 digests cleanly (`17 / 17`).
  - Regex scan across the full decompressed SQL dump (`api_key`, `x-api-key`, `authorization`, `bearer`, `password`, `secret`, `token=`, `sk_`, `pk_`): **`0` hits** (`0` HTTP headers/credentials stored).
- **Safe Non-Destructive Seeding Controls ([`scripts/seed_pilot_db.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/seed_pilot_db.py)):**
  - Validates fixture compressed and decompressed SHA-256 BEFORE SQLite execution.
  - Restores atomically into a sibling temporary file, verifies **station IDs, PM2.5 value range (`0.6`–`119.0`, mean `8.086823`), zero `observed_at > fetched_at` causality leaks, zero non-VALID quality flags, and all 17 batch SHA-256 hashes** via `validate_restored_db_contents()` before swapping files.
  - Refuses silent overwrite (`FileExistsError`) if any existing database differs from the audited fixture; explicit destructive replacement requires BOTH `--force` AND `--confirm-overwrite` and writes an automatic `.db.bak` backup. Verified with unit tests in [`tests/test_seed_pilot_db.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_seed_pilot_db.py).

---

## Task E — End-to-End Verification Checklist & Artifacts

| Check Suite | Environment | Outcome |
|---|---|---|
| `ruff format --check` & `ruff check` | Python 3.14 | Clean (0 formatting / lint errors) |
| Full `pytest -v` (incl. `test_postgres_integration.py`) | Local PostgreSQL 18.0 (`14,556` migrated rows) | **100 passed, 0 skipped, 0 failed** (`58.56s`) |
| `npm run typecheck && npm run lint && npm run build` | Node 20 / Next.js 15.5.29 | Clean (`0` vulnerabilities, `0` warnings) |
| `python scripts/run_browser_e2e.py` | Live FastAPI + static export + Chrome | Checked post-final-edit |
| GitHub Actions (`Python research CI` & `PostgreSQL & Browser E2E`) | Remote Linux runners | Verified green on final pushed SHA |
