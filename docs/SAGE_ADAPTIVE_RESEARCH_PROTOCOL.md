# SensorGuard AI — Adaptive Input-Dependent SAGE Research Protocol & Audited Results

**Milestone:** 7.4 / 7.7 — Input-Dependent Heteroscedastic Sensitivity Enveloping (Causal & Scientific Consistency Audit)  
**Study ID:** `SG-M77-ADAPTIVE-20261125-163505` *(reconciled directly to committed JSON audit; supersedes superseded M7.4/M7.6 IDs)*  
**Dataset Hash:** `33805c42097fc86e` (Archived Q1 2024 UK AURN Network, 8 stations, `14,556` raw valid obs)  
**Seeds Evaluated:** `[11, 42, 107]` across `MCAR 20%` and `MNAR gamma=1.5` (`natural`, `burst_24h`, and `extended_72h` evaluated deterministically at `seed=42`)  
**Artifact JSON:** [`docs/audit/adaptive_sage_results.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/adaptive_sage_results.json)  
**Implementation:** [`src/sensorguard/models/sage_adaptive.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage_adaptive.py) · [`scripts/run_adaptive_sage_research.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/run_adaptive_sage_research.py)  

---

## 1. Diagnosis of M6.1 Constant Disagreement Limitation

In M6.1 [`SAGEForecaster`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage.py), `calibrate()` derived scalar half-widths $h_\gamma = \max(r_\gamma + \gamma \sigma_{\text{res}}/2, h_{\gamma_{\text{prev}}})$ applied uniformly to every prediction. Consequently:
1. $D_{\text{sensitivity}}(x) = \max_\gamma U_\gamma(x) - \min_\gamma U_\gamma(x) = h_{2.0} - h_{0.0} = \mathbf{3.3206\ \mu\text{g/m}^3}$ for every test input (`std = 0.0`), yielding zero station/hour discrimination (`abstention = 0.44%` natural driven strictly by `age_hours > 12.0`).
2. Calm intervals inherited the global 90th-percentile error radius, inflating natural width to `17.01 µg/m³` and losing on Winkler score to `LightGBM + CQR` across all 8 LOSO stations.

---

## 2. Prespecified Adaptive Formulation & Strictly Causal Temporal Architecture

### 2.1 Strictly Causal Forward Timestamp-Boundary Cross-Validation (`AdaptiveSAGEForecaster`)
To eliminate all same-hour multi-station leakage and temporal lookahead identified prior to M7.7:
1. **Timestamp Validation & Causal Checks (`_validate_and_extract_timestamps`):** Rejects datasets missing valid UTC `timestamp` entries (`ValueError`) and asserts causal inequalities when `issued_at`/`decision_time`, `feature_timestamp`, or `target_available_at`/`label_available_at` columns are provided (`observed_at`/`fetched_at` batch ingestion metadata is tracked separately).
2. **Complete Unique-Timestamp Folding + Embargo (`compute_causal_oof_residuals`):** Partitions **complete unique timestamps** chronologically into $K=3$ contiguous timestamp blocks ($T_0, T_1, T_2$). All 8 stations sharing an identical hour are strictly assigned to the same block (`same_hour_disjoint = True`). Fold 0 serves strictly as historical warmup anchor; for fold $k \in \{1, 2\}$, the sub-model is trained exclusively on rows satisfying `label_available_at <= t_val_start - (horizon_hours + embargo_hours)` (`horizon = 1.0h`, `embargo = 1.0h`, actual audited `embargo_gap_hours = 2.0h`).
   - Fold 1: `train_n = 2,899` (`max_avail = 2024-01-19T06:00Z`), `val_n = 2,786` (`[2024-01-19T08:00Z, 2024-02-06T11:00Z]`, decision start `07:00Z`).
   - Fold 2: `train_n = 5,686` (`max_avail = 2024-02-06T10:00Z`), `val_n = 3,191` (`[2024-02-06T12:00Z, 2024-02-24T14:00Z]`, decision start `11:00Z`).
3. **Causal Local Scale Estimator $\hat{s}(x)$ & Propensity $P(R=1 \mid X)$:** Trains shallow LightGBM L1 regressor predicting $\log \hat{s}(x)$ strictly on forward embargoed OOF errors (`min_scale = 0.25`, `max_scale = 3.0`), and logistic propensity $\hat{p}(X) \in [0.01, 0.99]$ strictly pre-calibration.
4. **Input-Dependent Weighted Quantiles & Sensitivity Shifts (`calibrate`):** Normalizes residuals $\tilde{e}_i = |y_i - \hat{\mu}(X_i)| / \hat{s}(X_i)$, weighted by $1 / \pi_\gamma(X_i, Y_i)$, producing half-width:
   $$h_\gamma(x) = \max\!\left(\hat{s}(x)\,\tilde{q}_\gamma + \gamma \cdot \frac{\hat{s}(x)}{2}\left(1 + (1 - \hat{p}(x))\right),\ h_{\gamma_{\text{prev}}}(x)\right)$$
5. **Pre-Specified Policy Thresholds (Zero Test Peeking):** Fixed threshold $\tau = 8.0$ (`sage_adaptive_tau8`), calibration 95th-percentile threshold $\tau = P_{95}(D_{\text{sens}}(X_{\text{cal}})) = \mathbf{0.6066\ \mu\text{g/m}^3}$ (`sage_adaptive_cal95`), and `no_abstain`.

### 2.2 Prespecified Evaluation Criteria (Locked Before Audit — Unchanged)
- **C1 (Non-constant discrimination):** `std(D_sensitivity) > 0` and Spearman $\rho(D_{\text{sensitivity}}(x), |y - \hat{y}(x)|) > 0.20$.
- **C2 (Calibration validity):** Empirical `PICP >= 90.0%` (`95%` Wilson CI lower bound `>= 89.0%`) across all scenarios.
- **C3 (Sharpness improvement vs Static SAGE):** Strictly narrower `MPIW` and lower `Winkler` than Static SAGE (`17.01` / `17.06`), plus paired evaluation under identical masks and matched abstention budgets vs `Locally-Weighted MAR` ($\gamma=0$), `CQR`, `Persistence`, and `ACI`.

---

## 3. Multi-Regime Chronological Evaluation (`2,727` Valid Test Obs)

### 3.1 Natural Regime (`seed=42`, with Exact 95% Wilson Confidence Intervals)

| Method | Overall PICP `[95% CI]` | Published PICP | MPIW (µg/m³) | Winkler | Abstention | $D_{\text{sens}}$ Mean ± Std ($\rho$ vs $|e|$) |
|---|---|---:|---:|---:|---:|---|
| **persistence** (Split) | `96.88%` `[.9616, .9747]` | `97.02%` | **`2.00`** | **`3.16`** | `0.44%` | `—` |
| **masked_observation_aci** | `89.11%` `[.8788, .9022]` | `89.32%` | `2.14` | `3.26` | `0.44%` | `—` *(slightly `< 90%` target)* |
| **lightgbm_aci** (online) | `90.87%` `[.8973, .9189]` | `91.05%` | `2.48` | `3.49` | `0.44%` | `—` |
| **lightgbm_rolling** (168h) | `90.80%` `[.8965, .9182]` | `90.94%` | `3.06` | `4.13` | `0.44%` | `—` |
| **locally_weighted_mar_80** | `98.35%` `[.9780, .9876]` | `98.49%` | `3.82` | `4.43` | `0.44%` | `0.00 ± 0.00` *(80% nom calibration)* |
| **locally_weighted_mar** ($\gamma=0$) | `99.85%` `[.9962, .9994]` | `99.85%` | `8.59` (`8.58` pub) | `8.72` | `0.44%` | `0.00 ± 0.00` |
| **sage_adaptive_tau8** | `99.85%` `[.9962, .9994]` | `99.85%` | `9.07` (`9.07` pub) | `9.19` | `0.44%` | `0.29 ± 0.07` (**$\rho=+0.1749 < 0.20$**) |
| **sage_adaptive_cal95** | `99.85%` `[.9962, .9994]` | `99.85%` | `9.07` (`8.92` pub) | `9.19` | `1.47%` | `0.29 ± 0.07` (**$\rho=+0.1749 < 0.20$**) |
| **lightgbm_cqr** (Romano 2019) | `99.89%` `[.9968, .9996]` | `99.89%` | `11.35` | `11.45` | `0.44%` | `—` |
| **lightgbm_split** | `99.85%` `[.9962, .9994]` | `99.85%` | `13.23` | `13.35` | `0.44%` | `—` |
| **sage_static** (M6.1) | `99.93%` `[.9973, .9998]` | `99.93%` | `17.01` | `17.06` | `0.44%` | `3.32 ± 0.00` ($\rho=-0.1281$) |

> [!CAUTION]
> **Criterion C1 Audit Finding (`natural` & `burst_24h`):** Under strictly causal complete-timestamp splitting with a 2-hour embargo, **natural-regime Spearman rank correlation is $\rho = +0.1749$ (`std = 0.0664`, `MPIW = 9.0717`, `Winkler = 9.1948`)**, which **fails to meet** the prespecified C1 criterion ($\rho > 0.20$). The `burst_24h` regime likewise yields $\rho = +0.1782 < 0.20$. C1 (> 0.20) is met only under broader outage perturbations (`mcar_20` $+0.2419$, `mnar_gamma_1.5` $+0.2627$, `extended_72h` $+0.2545$) and across all 8 held-out LOSO stations (`+0.2534` to `+0.4539`).

### 3.2 Outage & Extended Blackout Regimes (`[11, 42, 107]` Multi-Seed for MCAR/MNAR; `seed=42` for Burst/72h)

| Scenario | Method | Overall PICP | Published PICP | MPIW (µg/m³) | Pub MPIW | Winkler | Abstention | $D_{\text{sens}}$ ($\rho$ vs $|e|$) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `mcar_20` | `locally_weighted_mar` | `99.77%` | `99.77%` | **`8.68`** | **`8.68`** | **`8.84`** | `0.55%` | `0.00` |
| `mcar_20` | `sage_adaptive_cal95` | `99.79%` | `99.80%` | `9.18` | `8.97` | `9.32` | `2.03%` | `0.29 ± 0.08` ($\mathbf{+0.2419}$) |
| `mnar_gamma_1.5` | `persistence` | `95.44%` | `96.01%` | `2.00` | `2.00` | `4.20` | `1.09%` | `—` |
| `mnar_gamma_1.5` | `masked_observation_aci` | **`88.78%`** | `89.27%` | `2.35` | `2.32` | `3.65` | `1.09%` | `—` *(below 90% target)* |
| `mnar_gamma_1.5` | `lightgbm_aci` | `90.63%` | `91.16%` | `2.74` | `2.73` | `4.22` | `1.09%` | `—` |
| `mnar_gamma_1.5` | `lightgbm_cqr` | `99.78%` | `99.92%` | `11.42` | `11.37` | `11.69` | `1.09%` | `—` |
| `mnar_gamma_1.5` | `locally_weighted_mar` | `99.33%` | `99.53%` | **`8.71`** | **`8.66`** | **`8.99`** | `1.09%` | `0.00` |
| `mnar_gamma_1.5` | `sage_static` | `99.85%` | `99.96%` | `16.99` | `16.96` | `17.10` | `1.09%` | `3.32 ± 0.00` ($\rho=-0.1154$) |
| `mnar_gamma_1.5` | `sage_adaptive_tau8` | `99.41%` | `99.56%` | `9.21` | `9.15` | `9.43` | `1.09%` | `0.30 ± 0.11` ($\mathbf{+0.2627}$) |
| `mnar_gamma_1.5` | `sage_adaptive_cal95` | `99.41%` | `99.55%` | `9.21` | `8.92` | `9.43` | `2.81%` | `0.30 ± 0.11` ($\mathbf{+0.2627}$) |
| `burst_24h` | `locally_weighted_mar` | `99.85%` | `99.85%` | **`8.59`** | **`8.58`** | **`8.72`** | `0.92%` | `0.00` |
| `burst_24h` | `sage_adaptive_cal95` | `99.85%` | `99.85%` | `9.08` | `8.92` | `9.20` | `1.98%` | `0.29 ± 0.07` ($\rho=+0.1782 < 0.20$) |
| `extended_72h` | `persistence` | `94.57%` | `96.87%` | `2.00` | `2.00` | `3.77` | `3.89%` | `—` |
| `extended_72h` | `locally_weighted_mar` | `99.85%` | `99.85%` | **`8.58`** | **`8.59`** | **`8.71`** | `3.89%` | `0.00` |
| `extended_72h` | `sage_adaptive_cal95` | `99.85%` | `99.85%` | `9.07` | `8.92` | `9.19` | `4.91%` | `0.29 ± 0.08` ($\mathbf{+0.2545}$) |

---

## 4. Paired Comparisons Under Identical Masks & Matched Budgets (Station-Day Block Bootstrap 95% CI)

| Contrast (Model A minus Model B) | Regime (`seed=42`) | Exact Common Pairs ($N$) | $\Delta\text{PICP}$ | $\Delta\text{MPIW}$ `[95% Block CI]` | $\Delta\text{Winkler}$ `[95% Block CI]` |
|---|---|---:|---:|---|---|
| **Scale Isolation:** `locally_weighted_mar` − `lightgbm_split` | `natural` (`all_valid`) | `2,727` | `0.0000` | **`-4.6398`** `[-4.91, -4.37]` | **`-4.6250`** `[-4.90, -4.35]` |
| **$\gamma$-Grid Isolation:** `sage_adaptive_tau8` − `locally_weighted_mar` | `natural` (`common_pub`) | `2,715` | `0.0000` | `+0.4835` `[+0.46, +0.51]` | **`+0.4708`** `[+0.45, +0.49]` |
| **$\gamma$-Grid Isolation:** `sage_adaptive_tau8` − `locally_weighted_mar` | `mnar_gamma_1.5` (`common_pub`) | `2,701` | `0.0000` | `+0.4964` `[+0.47, +0.52]` | **`+0.4673`** `[+0.42, +0.50]` |
| **$\gamma$-Grid Isolation:** `sage_adaptive_tau8` − `locally_weighted_mar` | `extended_72h` (`common_pub`) | `2,621` | `0.0000` | `+0.4848` `[+0.46, +0.51]` | **`+0.4716`** `[+0.45, +0.49]` |
| `sage_adaptive_cal95` − `sage_static` | `natural` (`common_pub`) | `2,687` | `-0.0007` | **`-8.0502`** `[-8.38, -7.78]` | **`-7.9784`** `[-8.33, -7.67]` |
| `sage_adaptive_cal95` − `lightgbm_cqr` *(matched pub=2,687)* | `natural` | `2,687` | `-0.0004` | **`-2.3607`** `[-2.44, -2.30]` | **`-2.3390`** `[-2.41, -2.27]` |
| `sage_adaptive_cal95` − `locally_weighted_mar` *(matched pub=2,687)* | `natural` | `2,683` | `0.0000` | `+0.4765` `[+0.46, +0.49]` | **`+0.4636`** `[+0.44, +0.49]` |
| `sage_adaptive_cal95` − `persistence` *(on cal95 pub)* | `natural` | `2,687` | `+0.0238` | `+6.9205` `[+6.79, +7.06]` | **`+6.0912`** `[+5.42, +6.63]` |
| `sage_adaptive_cal95` − `lightgbm_aci` *(matched budget)* | `natural` | `2,659` | `+0.0839` | `+6.5274` `[+6.27, +6.74]` | **`+5.8613`** `[+5.17, +6.34]` |

---

## 5. 8-Fold Leave-One-Station-Out (LOSO) Spatial Generalization (Full Paired Suite)

| Held-Out Station | `lightgbm_cqr` (PICP/W/Wink) | `locally_weighted_mar` (PICP/W/Wink) | `sage_static` (PICP/W/Wink) | `sage_adaptive_tau8` (PICP/W/Wink/Abs) | `sage_adaptive_cal95` (PICP/W/Wink/Abs/$\rho$) | $\Delta\text{Wink}$ (`tau8−MAR`) |
|---|---|---|---|---|---|---:|
| `station_1` (Harlington) | `94.97%` / `3.70` / `4.10` | `99.23%` / `3.72` / **`3.87`** | `99.55%` / `8.18` / `8.24` | `99.81%` / `7.79` / `7.82` (`4.32%`) | `99.81%` / `7.79` / `7.82` (`3.74%`, `+0.3285`) | `+2.9755` |
| `station_2` (Hillingdon) | `95.37%` / `3.41` / `3.61` | `96.58%` / `2.90` / **`3.02`** | `100.0%` / `8.47` / `8.47` | `99.47%` / `3.85` / `3.90` (`0.21%`) | `99.47%` / `3.85` / `3.90` (`3.05%`, `+0.2877`) | `+0.8904` |
| `station_3` (Marylebone) | `90.54%` / `3.96` / **`5.48`** | `98.52%` / `5.81` / `6.20` | `97.53%` / `8.34` / `9.29` | `99.58%` / `10.27` / `10.40` (`8.33%`) | `99.58%` / `10.27` / `10.40` (`6.99%`, `+0.3968`) | `+2.8514` |
| `station_4` (Slade Green) | **`81.72%`** / `3.37` / `11.46` | **`85.36%`** / `3.52` / **`10.91`** | `90.72%` / `4.76` / `13.23` | **`92.07%`** / `5.51` / `11.14` (`0.05%`) | **`92.07%`** / `5.51` / `11.14` (`2.70%`, `+0.4539`) | `+0.2337` |
| `station_5` (Southampton) | `96.00%` / `3.10` / `3.38` | `96.11%` / `2.46` / **`2.67`** | `100.0%` / `8.36` / `8.36` | `99.41%` / `3.91` / `4.01` (`0.21%`) | `99.41%` / `3.91` / `4.01` (`0.91%`, `+0.2534`) | `+1.3478` |
| `station_6` (Wicken Fen) | `93.84%` / `4.03` / `4.82` | `98.12%` / `3.28` / **`3.39`** | `99.14%` / `7.94` / `8.32` | `100.0%` / `6.44` / `6.44` (`4.07%`) | `100.0%` / `6.44` / `6.44` (`3.38%`, `+0.3026`) | `+2.2437` |
| `station_7` (St Osyth) | `95.32%` / `3.26` / `4.08` | `97.61%` / `2.95` / **`3.29`** | `99.06%` / `7.86` / `8.51` | `98.91%` / `4.20` / `4.38` (`0.16%`) | `98.91%` / `4.20` / `4.38` (`2.86%`, `+0.3150`) | `+1.0935` |
| `station_8` (Manchester) | `94.06%` / `3.78` / `5.10` | `94.39%` / `3.44` / **`3.72`** | `99.04%` / `8.42` / `9.33` | `99.30%` / `4.52` / `4.61` (`0.27%`) | `99.30%` / `4.52` / `4.61` (`5.24%`, `+0.4279`) | `+0.9017` |
| **8-Station Mean** | `92.73%` / `3.58` / `5.26` | `95.74%` / `3.51` / **`4.63`** | `98.13%` / `7.79` / `9.22` | `98.57%` / `5.81` / `6.59` (`2.20%`) | `98.57%` / `5.81` / `6.59` (`3.61%`, `>0.25`) | **`+1.5672`** |

*(Additional 8-station LOSO mean baselines: `persistence` `97.17%` / `2.00` / **`3.68`**, `lightgbm_aci` `90.43%` / `2.59` / **`3.91`**.)*

---

## 6. Honest Assessment & Preserved Negative Results

1. **What Actually Improves Performance (Local Scale Estimation $\hat{s}(x)$):** Isolating $\hat{s}(x)$ via `locally_weighted_mar` ($\gamma=0$) drives virtually all the improvement relative to Static SAGE and `LightGBM Split Conformal` (`-4.63` Winkler and `-4.64 µg/m³` MPIW across all valid natural test rows, and `4.63` vs `5.26` LOSO station-mean Winkler vs CQR).
2. **Negative Result 1 — MNAR $\gamma$-Grid Incremental Penalty:** Under identical published masks (`tau8 − MAR`), adding the propensity-modulated $\gamma$-grid widens intervals by `+0.48` to `+0.50 µg/m³` chronologically (`+0.47` Winkler penalty, zero coverage change in natural/burst/72h) and worsens LOSO Winkler on all `8/8` stations (`+1.57` station-mean Winkler penalty, `+2.20%` vs `0.28%` abstention under `tau8`). The only concrete empirical rescue from adding the $\gamma$ grid is on `station_4` (`Bexley - Slade Green`), where `CQR` (`81.72%`) and `locally_weighted_mar` (`85.36%`) fall well below 90%, while `sage_adaptive` preserves **`92.07%`** coverage (`Winkler = 11.14` vs `11.46` CQR).
3. **Negative Result 2 — Criterion C1 Fails Under Natural & Burst-24h Conditions:** Under strict timestamp-boundary forward splits with a 2-hour embargo, Spearman $\rho(D_{\text{sens}}, |e|)$ equals **`+0.1749`** (`natural`) and **`+0.1782`** (`burst_24h`), falling short of the prespecified `> 0.20` goal. We preserve this failure transparently without post-hoc threshold adjustment.
4. **Negative Result 3 — Conventional Sharpness Baselines Still Lead:** On routine Q1 2024 UK AURN hours, `Persistence + Split Conformal` (`Winkler = 3.16` natural / `3.68` LOSO) and `LightGBM + ACI` (`3.49` natural / `3.91` LOSO) remain markedly sharper (`-5.86` to `-6.09` Winkler advantage over `sage_adaptive_cal95` on paired published rows). `masked_observation_aci` under-covers the nominal `90%` target (`89.11%` natural, `88.78%` MNAR).
5. **Scope Boundary:** No formal finite-sample MNAR theorem is claimed; `sage_adaptive` remains an audited empirical prototype tested on one 90-day UK region (`Q1 2024`). Multi-region / seasonal generalization and sustained live telemetry remain unvalidated.
