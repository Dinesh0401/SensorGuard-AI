# SensorGuard AI: Sensitivity-Aware Guarded Estimation for PM₂.₅ Forecasting Under Non-Random Sensor Missingness

**Technical Research Report — Milestone 6.1 (Verified Audit)**  
Repository: `Dinesh0401/SensorGuard-AI`  
Branch: `feat/milestone-3-forecasting-uncertainty`  
Benchmark ID: `SG-BENCH-20261125-040900`  
Ablation ID: `SG-SAGE-ABLATION-20261124-162613`  
Dataset content hash: `33805c42097fc86e`  
Date: 2026-11-25

---

## Abstract

Public air-quality sensor networks suffer from non-random reporting gaps when missingness correlates with environmental conditions or elevated particulate concentrations. We evaluate **SAGE** (Sensitivity-Aware Guarded Estimation), an engineering and conformal-envelope prototype that (1) fits an observed reporting propensity model $\hat{p}(X) = P(R=1 \mid X)$, (2) calibrates weighted conformity quantiles across a parametric logit sensitivity grid $\gamma \in \{0.0, 0.5, 1.0, 1.5, 2.0\}$ with heuristic residual-std bound shifts $\Delta_\gamma = \gamma \cdot \sigma_{\text{res}} / 2$, and (3) applies a guarded decision rule over sensor staleness, effective sample size, envelope width, and inter-scenario upper-bound disagreement. On archived Q1 2024 hourly PM₂.₅ observations (`2024-01-01` to `2024-03-31`, `14,556` raw valid readings) across eight UK AURN stations spanning Greater London and regional England (`Southampton`, `Wicken Fen`, `St Osyth`, `Manchester`), SAGE achieves `99.93%` coverage over both all valid targets (`2,727`) and published targets (`2,715`) under natural reporting with `0.44%` abstention (`12 / 2,727`), driven identically by the `age_hours > 12.0` freshness guard. **Honest negative findings:** under natural and simulated outage regimes, `Persistence + Split Conformal` (`MPIW = 2.00 µg/m³`, `Winkler = 3.16`, `PICP = 96.88%`) and `LightGBM + CQR` (`MPIW = 11.35 µg/m³`, `Winkler = 11.45`, `PICP = 99.89%`) produce substantially sharper intervals and lower Winkler scores than SAGE (`MPIW = 17.01 µg/m³`, `Winkler = 17.06`). On 8-fold Leave-One-Station-Out CV, `LightGBM + CQR` outperforms SAGE on Winkler score across all eight stations, though `station_4` (`Bexley - Slade Green`) drops CQR below nominal (`81.72%`) whereas SAGE preserves `90.72%` (`MPIW = 4.76 µg/m³`). We claim **no formal finite-sample MNAR coverage theorem** and **no general superiority at matched width**.

---

## 1. Introduction & Claim Boundaries

Urban PM₂.₅ monitoring informs environmental situational awareness and operational reliability analysis. When stations stop reporting or lag upstream, evaluation restricted only to observed periods can overstate calibration reliability. However, missing-not-at-random (MNAR) mechanisms are fundamentally unidentifiable from observed data alone without auxiliary measurements or structural assumptions.

**Research questions evaluated:**
1. Does combining a parametric logit sensitivity grid with conformal calibration preserve ≥ 90% empirical containment under simulated MCAR, MNAR, and 24-hour blackout masking?
2. How does a conservative union envelope compare against Persistence, Ridge, Spatial IDW, LightGBM Split Conformal, CQR (Romano et al., 2019), Rolling Conformal, and Adaptive Conformal Inference (Gibbs & Candès, 2021) at matched splits?
3. What is the empirical contribution of the sensitivity grid, abstention policy, spatial neighbor features, and freshness-age features?

> [!NOTE]
> SensorGuard AI is an internal research and portfolio prototype on archived historical data (`2024-01-01` to `2024-03-31`). It is **not** a live telemetry service, medical advisory tool, or peer-reviewed publication.

---

## 2. Prior Work

| Reference | Focus & Mechanism | Scope Difference vs. SAGE |
|---|---|---|
| Vovk, Gammerman & Shafer (2005) | Finite-sample conformal prediction | Assumes exchangeability |
| Romano, Patterson & Candès (2019) — **CQR** | Conformalized quantile regression | Adaptive width under exchangeability; no explicit staleness/sensitivity envelope |
| Gibbs & Candès (2021) — **ACI** | Online $\alpha_t$ adaptation under shift | Sequential adjustment; no static MNAR sensitivity grid |
| Zaffran et al. (2023, ICML) | Conformal prediction with missing **covariates** | Missing input features $X$, whereas SensorGuard targets missing **labels** $Y$ at calibration/evaluation |
| TMLR 2026 (*Adaptive Conformal for Time Series with Missing Values*) | Closest time-series missingness conformal work | Not yet implemented for direct empirical comparison (open blocker) |
| Rosenbaum (2002) | Observational odds-ratio sensitivity parameter $\Gamma \ge 1$ | Formal non-parametric odds-ratio bound (distinct from SAGE's linear-logit parameterization) |

---

## 3. Mathematical Formulation (Matching Codebase)

Let $i$ index monitoring station, $t$ index UTC hour, $Y_{i,t} \ge 0$ denote PM₂.₅ ($\mu\text{g/m}^3$), and $R_{i,t} \in \{0, 1\}$ denote whether a valid measurement is reported ($R_{i,t} = 1$). All features $X_{i,t}$ are strictly causal from observations $\le t - 1\text{h}$ (using `ffill().shift(1)` on reported history).

### 3.1 Observed Propensity & Parametric Logit Sensitivity Grid

Unlike Rosenbaum's odds-ratio model $\frac{1}{\Gamma} \le \frac{\text{odds}(Y_1)}{\text{odds}(Y_0)} \le \Gamma$, the implemented sensitivity model in [sage.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage.py) specifies a parametric linear-logit shift parameterized by $\gamma \in \Gamma_{\text{grid}} = \{0.0, 0.5, 1.0, 1.5, 2.0\}$:

1. Fit observed reporting propensity $\hat{p}(X) = \text{clip}\!\left(\hat{P}(R=1 \mid X),\ 0.01,\ 0.99\right)$ via regularized logistic regression, yielding log-odds $g(X) = \log\frac{\hat{p}(X)}{1 - \hat{p}(X)}$.
2. Standardize calibration targets $z_i = \frac{y_i - \mu_y}{\sigma_y}$ (with $\sigma_y = \max(\text{std}(y_{\text{cal}}), 1.0)$).
3. For each sensitivity coefficient $\gamma \in \Gamma_{\text{grid}}$:
   $$\text{logit}\,\pi_\gamma(X_i, Y_i) = \text{clip}\!\left(g(X_i) + \gamma \cdot z_i,\ -20.0,\ 20.0\right), \quad \pi_\gamma = \sigma(\text{logit}\,\pi_\gamma)$$
   Here $\gamma = 0$ assumes MAR conditional on $X$, and $\gamma > 0$ tests sensitivity to positive selection odds-log-slopes in $z_i$.

### 3.2 Importance Reweighting, Heuristic Bound Shifts & Envelope

For each $\gamma$ on the calibration set ($n_{\text{cal}} = 2,937$):
1. Normalize inverse propensity weights $w_i(\gamma) = 1 / \max(\pi_\gamma(X_i, Y_i), 10^{-4})$, $\tilde{w}_i = w_i / \sum_j w_j$. Track Kish Effective Sample Size $\text{ESS}_\gamma = 1 / \sum_i \tilde{w}_i^2$.
2. Compute weighted conformal residual quantile $r_\gamma$ over $|y_i - \hat{y}_i|$ at finite-sample level $\min\!\left(1.0,\ \frac{\lceil (n_{\text{cal}}+1)(1-\alpha)\rceil}{n_{\text{cal}}}\right)$ ($\alpha = 0.10$).
3. Add a heuristic sensitivity margin proportional to calibration residual standard deviation $\sigma_{\text{res}} = \max(\text{std}(|y - \hat{y}|), 0.5)$ and enforce monotonicity across sorted $\gamma$:
   $$\Delta_\gamma = \gamma \cdot \frac{\sigma_{\text{res}}}{2}, \qquad h_\gamma = \max\!\left(r_\gamma + \Delta_\gamma,\ h_{\gamma_{\text{prev}}}\right)$$
4. Form per-scenario bounds and conservative outer envelope:
   $$L_\gamma(x) = \max(0,\, \hat{y}(x) - h_\gamma), \quad U_\gamma(x) = \hat{y}(x) + h_\gamma$$
   $$L_{\text{env}}(x) = \min_\gamma L_\gamma(x), \quad U_{\text{env}}(x) = \max_\gamma U_\gamma(x)$$

> [!IMPORTANT]
> **Theoretical Scope Boundary:** No finite-sample theorem guarantees coverage under unrestricted or misspecified MNAR. The $\Delta_\gamma$ margin and weighted quantile are **empirical heuristic sensitivity controls**, not a distribution-free guarantee. On the fitted Q1 2024 model (`mu_y=11.84`, `sigma_y=9.09`, `sigma_res=4.23`), $h_{0.0}=7.55$, $h_{0.5}=8.19$, $h_{1.0}=9.25$, $h_{1.5}=9.82$, and $h_{2.0}=10.87\ \mu\text{g/m}^3$ (`ESS = 2,935.36`).

### 3.3 Scenario Disagreement & Guarded Policy

Scenario disagreement is isolated from total envelope width ($W_{\text{env}} = U_{\text{env}} - L_{\text{env}}$):
$$D_{\text{sensitivity}}(x) = \max_\gamma U_\gamma(x) - \min_\gamma U_\gamma(x)$$

Because $h_\gamma$ is calibrated globally on `X_cal` and does not currently vary with test input $x$, $D_{\text{sensitivity}}(x) = h_{2.0} - h_{0.0} = 10.8705 - 7.5499 = 3.3206\ \mu\text{g/m}^3$ across all test observations in the pilot run.

The guarded rule in `guarded_decision_rule()` abstains (`status = "abstain"`) if:
- `age_hours > 12.0` (`stale_sensor_history`), OR
- `effective_sample_size < 15`, OR
- `D_sensitivity > 8.0` (`\tau_disagree`), OR
- `W_envelope > 50.0` (`\tau_envelope`).

Otherwise it assigns `caution` if `age_hours > 3.0` or `D_sensitivity > 0.70 * 8.0`, else `publish`. Because `3.3206 < 8.0` and `W_envelope <= 21.74 < 50.0`, **all abstentions observed in Q1 2024 (`12` natural, `16` MCAR, `26` MNAR, `25` burst) were triggered solely by `age_hours > 12.0`**, identically matching the baseline `policy(age_hours > 12.0)` rule.

---

## 4. Dataset Provenance & Count Reconciliation

| Property | Exact Audited Value |
|---|---|
| **Source & License** | UK Defra Automatic Urban and Rural Network (AURN) via OpenAQ v3 (Open Government Licence v3.0) |
| **Pollutant** | Hourly PM₂.₅ ($\mu\text{g/m}^3$) |
| **Observed Data Window** | Q1 2024: `2024-01-01T01:00:00Z` to `2024-03-31T23:00:00Z` (requested audit window `2024-01-01T00:00:00Z` to `2024-03-31T23:59:59Z`) |
| **Pilot Network Stations (8)** | UK AURN Network spanning Greater London (`London Harlington #141`, `London Hillingdon #153`, `Westminster - Marylebone Road #154`, `Bexley - Slade Green #157`) and regional England (`Southampton Centre #698`, `Wicken Fen #804`, `St Osyth #1795`, `Manchester Piccadilly #2312`) |
| **Raw Valid Observations in DB** | **14,556** valid hourly readings (`1552 + 1902 + 1417 + 2146 + 1876 + 1867 + 1925 + 1871`) |
| **Reindexed Grid Reconciliation** | `2,183` hours × `8` stations = `17,464` station-hours. Dropping `69` pre-first-report warmup rows leaves `17,395` grid rows (`14,548` with `reported=True`, excluding `8` initial measurements that have no prior `last_pm25`). |
| **Chronological Splits (60 / 20 / 20)** | **Train (`< 2024-02-24 15:00Z`, `reported=True` only):** `8,884` rows<br>**Calibrate (`[2024-02-24 15:00Z, 2024-03-13 19:00Z)`, `reported=True` only):** `2,937` rows<br>**Test (`>= 2024-03-13 19:00Z`, unfiltered hourly grid):** `3,496` station-hours (`2,727` valid reported targets + `769` missing-target hours = `22.0%` missing) |
| **Exact Arithmetic Check** | $8{,}884\text{ (train)} + 2{,}937\text{ (cal)} + 2{,}727\text{ (test reported)} + 8\text{ (initial warmup)} = 14{,}556$ raw DB records |
| **Dataset Content Hash** | `33805c42097fc86e` |

### Why Raw DB Count (`14,556`) Differs From Split Sum (`15,317`)
`train` (`8,884`) and `calibrate` (`2,937`) retain only reported observations, whereas `test` (`3,496`) keeps the full continuous hourly station grid (including `769` unreported station-hours where `target = NaN`) so outage simulators can reconstruct causal histories hour by hour without dropping gap slots. Under the unified denominator mask ($\text{isfinite}(y) \land \text{isfinite}(\hat{y}) \land \text{isfinite}(L) \land \text{isfinite}(U)$), all benchmark and ablation metrics evaluate on the exact **$2,727$ valid test observations**.

---

## 5. Benchmark Results (`SG-BENCH-20261125-040900`)

All models share identical splits, causal feature matrix, and finite evaluation denominator ($N_{\text{valid}} = 2,727$, nominal coverage $90\%$).

### 5.1 Natural Data Regime

| Method | Calibrator | MAE | RMSE | Overall PICP | Pub. PICP | Overall MPIW | Pub. MPIW | Winkler | Abstention |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Persistence (`B0a`) | Split Conformal | **0.24** | 0.70 | 96.88% | 97.02% | **2.00** | **2.00** | **3.16** | 0.44% |
| Ridge (`B2a`) | Split Conformal | 0.30 | **0.69** | 96.04% | 96.17% | 2.23 | 2.23 | 3.26 | 0.44% |
| Seasonal Naive (`B0b`) | Split Conformal | 2.15 | 3.20 | 99.63% | 99.63% | 21.43 | 21.43 | 21.71 | 0.44% |
| Spatial Neighbor (`B1`) | Split Conformal | 1.81 | 2.45 | 96.70% | 96.69% | 10.82 | 10.82 | 11.65 | 0.44% |
| LightGBM Point | Split Conformal | 0.28 | 0.80 | 99.85% | 99.85% | 13.23 | 13.22 | 13.35 | 0.44% |
| LightGBM Quantile | **CQR** (Romano 2019) | 0.29 | 0.83 | 99.89% | 99.89% | 11.35 | 11.34 | 11.45 | 0.44% |
| LightGBM Rolling | Rolling (168h) | 0.28 | 0.80 | 90.80% | 90.94% | 3.06 | 3.06 | 4.13 | 0.44% |
| LightGBM ACI | ACI (Gibbs & Candès) | 0.28 | 0.80 | 99.71% | 99.71% | 141.46 | 138.99 | 141.68 | 0.44% |
| SAGE ($\gamma=0$ MAR) | Point Conformal | 0.28 | 0.80 | 99.85% | 99.85% | 13.23 | 13.22 | 13.35 | 0.44% |
| **SAGE Full** | **Sensitivity Envelope** | 0.28 | 0.80 | **99.93%** | **99.93%** | 17.01 | 17.00 | 17.06 | 0.44% |
| SAGE No Abstain | Sensitivity Envelope | 0.28 | 0.80 | 99.93% | 99.93% | 17.01 | 17.01 | 17.06 | 0.00% |

*Unified-denominator invariant check:* `sage_no_abstain` satisfies `overall_picp == published_picp == 99.93%` and `mpiw == published_mpiw == 17.0093` identically.

### 5.2 Controlled Missing-Data Stress Regimes (Seed 42 in `benchmark_results.json`)

Evaluated under leak-free causal feature reconstruction (precomputed features dropped upfront; ground truth retained separately for evaluator scoring):

| Scenario | Persistence PICP / Width / Winkler | LightGBM+CQR PICP / Pub / Width / Winkler | SAGE Full PICP / Pub / Width / Winkler | Abstention Rate (Published / Valid) |
|---|---|---|---|---|
| `natural` | 96.88% / 2.00 / 3.16 | 99.89% / 99.89% / 11.35 / 11.45 | 99.93% / 99.93% / 17.01 / 17.06 | 0.44% (2,715 / 2,727) |
| `mcar_20` | 96.59% / 2.00 / 3.21 | 99.89% / 99.89% / 11.41 / 11.51 | 99.93% / 99.93% / 17.01 / 17.06 | 0.59% (2,711 / 2,727) |
| `mnar_gamma_1.5` | 95.60% / 2.00 / 4.14 | 99.78% / 99.93% / 11.42 / 11.70 | 99.85% / 99.96% / 16.98 / 17.10 | 0.95% (2,701 / 2,727) |
| `burst_24h` | 96.85% / 2.00 / 3.17 | 99.89% / 99.89% / 11.36 / 11.46 | 99.93% / 99.93% / 17.01 / 17.06 | 0.92% (2,702 / 2,727) |

### 5.3 Critical Clarification on the Stale-History Subgroup (`age_hours > 12.0`)

Earlier summaries cited `100.0%` stale-history coverage for SAGE vs. `66.7%` for Persistence as evidence of superiority. Inspection of the underlying code and sample-level decisions reveals two essential caveats:
1. **Small Sample Size ($n = 12$ in natural):** Only `12` valid observations fall into `stale_history` (`age_hours > 12.0`) in natural testing (`16` in `mcar_20`, `26` in `mnar_gamma_1.5`, `25` in `burst_24h`).
2. **All Stale-History Rows Were Withheld, Not Published:** On `sage_full`, **0 of 12 predictions in `natural` (`0/16` MCAR, `0/26` MNAR, `0/25` burst) were published**—all `100%` of stale-history observations triggered `abstain` (`stale_sensor_history`), identically to the shared freshness `policy(age_hours > 12.0)` applied to baselines. The `strata` metric in `compute_interval_metrics()` measures counterfactual envelope containment on withheld intervals (`12/12 = 100.0%` natural, `23/26 = 88.46%` under MNAR $\gamma=1.5$), **not delivered forecast reliability**.

In the `moderate_delay` subgroup (`3.0 < age_hours <= 12.0`), where predictions *were* published by every method:
- **Natural ($n = 10$ published):** Persistence `80.0%` (`8/10`), Ridge `70.0%`, LightGBM Rolling `70.0%`, Spatial Neighbor `40.0%`, LightGBM Point/CQR `100.0%` (`MPIW = 12.26`), SAGE Full `100.0%` (`MPIW = 19.03`).
- **MNAR $\gamma = 1.5$ ($n = 133$ published):** Persistence `78.2%`, LightGBM Rolling `59.4%`, LightGBM Point `100.0%` (`MPIW = 14.93`), LightGBM CQR `100.0%` (`MPIW = 13.78`), SAGE Full `100.0%` (`MPIW = 20.27`).

---

## 6. 3-Seed Ablation Study (`SG-SAGE-ABLATION-20261124-162613`)

Executed over seeds `[11, 42, 107]` across 5 conditions (`mean ± std`). *Note:* `sage_ablation_results.json` shows `abstention_rate = 1.09% ± 0.17%` (`29.67 ± 4.73` of `2,727`) under MNAR across seeds `[11, 42, 107]`, whereas `benchmark_results.json` (`seed=42` only) gives `0.95%` (`26 / 2,727`).

### 6.1 Natural Regime & MNAR ($\gamma = 1.5$) Summary

| Scenario | Condition | Overall PICP | Published PICP | MPIW (µg/m³) | Winkler | Abstention | Mean $D_{\text{sens}}$ |
|---|---|---:|---:|---:|---:|---:|---:|
| `natural` | `full_sage` | 99.93% ± 0.00% | 99.93% ± 0.00% | 17.01 ± 0.00 | 17.06 ± 0.00 | 0.44% ± 0.00% | 3.32 |
| `natural` | `no_sensitivity_grid` ($\gamma=0$) | 99.85% ± 0.00% | 99.85% ± 0.00% | 13.23 ± 0.00 | 13.35 ± 0.00 | 0.44% ± 0.00% | 0.00 |
| `natural` | `no_abstention` | 99.93% ± 0.00% | 99.93% ± 0.00% | 17.01 ± 0.00 | 17.06 ± 0.00 | 0.00% ± 0.00% | 3.32 |
| `natural` | `no_spatial_features` | 99.93% ± 0.00% | 99.93% ± 0.00% | 17.93 ± 0.00 | 17.98 ± 0.00 | 0.44% ± 0.00% | 4.32 |
| `natural` | `no_age_features` | 99.93% ± 0.00% | 99.93% ± 0.00% | 17.57 ± 0.00 | 17.63 ± 0.00 | 0.00% ± 0.00% | 3.50 |
| `mnar_gamma_1.5` | `full_sage` | 99.85% ± 0.00% | 99.96% ± 0.00% | 16.99 ± 0.01 | 17.10 ± 0.01 | 1.09% ± 0.17% | 3.32 |
| `mnar_gamma_1.5` | `no_sensitivity_grid` | 99.78% ± 0.00% | 99.92% ± 0.02% | 13.22 ± 0.00 | 13.45 ± 0.00 | 1.09% ± 0.17% | 0.00 |
| `mnar_gamma_1.5` | `no_abstention` | 99.85% ± 0.00% | 99.85% ± 0.00% | 16.99 ± 0.01 | 17.10 ± 0.01 | 0.00% ± 0.00% | 3.32 |
| `mnar_gamma_1.5` | `no_spatial_features` | 99.85% ± 0.00% | 99.96% ± 0.00% | 17.91 ± 0.01 | 17.99 ± 0.01 | 1.09% ± 0.17% | 4.32 |
| `mnar_gamma_1.5` | `no_age_features` | 99.85% ± 0.00% | 99.85% ± 0.00% | 17.55 ± 0.01 | 17.65 ± 0.01 | 0.00% ± 0.00% | 3.50 |

**Interpretation:**
- Removing **spatial IDW features** increases MPIW by `0.92 µg/m³` (`+5.4%`) and raises disagreement from `3.32` to `4.32 µg/m³`.
- Removing **age features** disables freshness gating (`stale_age_hours=1e9`, `0.00%` abstention) and expands MPIW by `0.56 µg/m³`.
- Disabling the **sensitivity grid** narrows intervals (`13.22` vs `16.99 µg/m³`) and reduces Winkler score (`13.45` vs `17.10`) with only `-0.04pp` published coverage difference (`99.92%` vs `99.96%`) under MNAR $\gamma=1.5$.

---

## 7. Leave-One-Station-Out (LOSO) CV & Time-Block Stability

Both longitudinal time-block stability (3 sequential sub-windows) and 8-fold LOSO cross-validation are implemented and logged in [benchmark_results.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/benchmark_results.json).

### 7.1 8-Fold Leave-One-Station-Out Spatial Generalizability

Trained and calibrated on 7 stations (75%/25% split), evaluated naturally on the remaining held-out station:

| Held-Out Station | Test Obs (`N`) | Persist MAE | LGB Split (PICP / Width / Winkler) | LGB CQR (PICP / Width / Winkler) | SAGE Full (PICP / Pub / Width / Winkler / Abs) |
|---|---:|---:|---|---|---|
| `station_1` (Harlington) | 2,121 | 0.24 | 97.29% / 3.10 / **3.84** | 94.97% / 3.70 / 4.10 | 99.55% / 99.61% / 8.18 / 8.24 / 0.26% |
| `station_2` (Hillingdon) | 2,182 | 0.21 | 98.16% / 3.27 / **3.54** | 95.37% / 3.41 / 3.61 | 100.00% / 100.00% / 8.47 / 8.47 / 0.21% |
| `station_3` (Marylebone) | 2,182 | 0.31 | 93.08% / 2.86 / 5.91 | 90.54% / 3.96 / **5.48** | 97.53% / 97.72% / 8.34 / 9.29 / 0.85% |
| `station_4` (Slade Green) | 2,182 | 0.85 | 82.00% / 2.07 / 13.96 | 81.72% / 3.37 / **11.46** | **90.72%** / **90.72%** / 4.76 / 13.23 / 0.05% |
| `station_5` (Southampton) | 2,182 | 0.24 | 98.56% / 3.15 / **3.33** | 96.00% / 3.10 / 3.38 | 100.00% / 100.00% / 8.36 / 8.36 / 0.21% |
| `station_6` (Wicken Fen) | 2,182 | 0.19 | 96.78% / 2.95 / **4.06** | 93.84% / 4.03 / 4.82 | 99.14% / 99.14% / 7.94 / 8.32 / 0.21% |
| `station_7` (St Osyth) | 2,182 | 0.22 | 96.21% / 2.77 / 4.42 | 95.32% / 3.26 / **4.08** | 99.06% / 99.06% / 7.86 / 8.51 / 0.16% |
| `station_8` (Manchester) | 2,182 | 0.27 | 96.36% / 3.29 / **5.06** | 94.06% / 3.78 / 5.10 | 99.04% / 99.09% / 8.42 / 9.33 / 0.27% |

**Honest LOSO Finding:** `LightGBM + CQR` achieves lower (better) Winkler scores than SAGE on **all 8 stations**, and stays above the 90% nominal target on 7/8 stations. Only on `station_4` (higher residual variance: Persistence `MAE = 0.85`, LightGBM `MAE = 1.00`) do Split (`82.00%`) and CQR (`81.72%`) drop below nominal while SAGE preserves `90.72%` (`MPIW = 4.76`), yet SAGE's Winkler score (`13.23`) remains higher than CQR (`11.46`). Furthermore, LOSO CV currently runs only on naturally observed data and has not yet been stress-tested under simulated MNAR outages or across independent airsheds outside England.

### 7.2 Chronological Time-Block Stability (Test Partition)

| Time Block | `N` | LightGBM Split (PICP / Width / Winkler) | LightGBM CQR (PICP / Width / Winkler) | SAGE Full (PICP / Pub / Width / Winkler / Abs) |
|---|---:|---|---|---|
| `block_1_2024-03-13_to_2024-03-19` | 1,165 | 100.0% / 13.06 / 13.06 | 100.0% / 11.16 / 11.16 | 100.0% / 100.0% / 16.78 / 16.78 / 0.5% |
| `block_2_2024-03-19_to_2024-03-25` | 1,165 | 99.9% / 13.57 / 13.73 | 99.9% / 11.49 / 11.69 | 99.9% / 99.9% / 17.39 / 17.45 / 0.9% |
| `block_3_2024-03-25_to_2024-03-31` | 1,166 | 99.7% / 13.14 / 13.36 | 99.8% / 11.44 / 11.58 | 99.9% / 99.9% / 16.97 / 17.07 / 0.0% |

---

## 8. Honest Negative Results & Pre-Registered Stop-Rule Assessment

Against the pre-registered research hurdle (*improve worst-stratum coverage gap by ≥ 5 percentage points under MNAR without disproportionate width inflation relative to strong baselines*):
- **Pre-registered Hurdle Verdict (vs. Strongest ML Baseline):** **NOT MET at matched width.** Under `mnar_gamma_1.5`, `LightGBM + CQR` already achieves a `10.0%` worst-stratum coverage gap (`99.78%` overall PICP, `99.93%` published PICP) with `MPIW = 11.42` and `Winkler = 11.70`, matching SAGE's `10.0%` gap with **33% narrower intervals and lower Winkler loss**. SAGE improves worst-stratum coverage only against un-adapted `Persistence` (`47.7%` gap) and `Rolling Conformal` (`47.7%` gap), or on the natural `station_4` LOSO fold (`90.72%` vs `81.72%`) at higher width/Winkler.
- **Persistence Strength at +1h:** Simple `Persistence + Split Conformal` (`MAE = 0.24`, `MPIW = 2.00`, `Winkler = 3.16`, `PICP = 96.88%`) outperforms ML trees on 1-hour sharpness in calm regimes because hourly PM₂.₅ has strong autocorrelation.
- **ACI Width Inflation:** Fixed-step `AdaptiveConformalInference` (`gamma=0.005`) explodes interval width (`MPIW = 141.46`, `Winkler = 141.68`) when calibration radii interact with zero-miss streaks.
- **Constant Disagreement in Current Implementation:** Because $h_\gamma$ does not depend on local input $X$, $D_{\text{sensitivity}} = 3.32\ \mu\text{g/m}^3$ is globally constant across test samples (`< 8.0`). Future research versions would require local heteroscedastic quantile/residual scaling across $\gamma$ for disagreement abstention to act selectively per observation.

---

## 9. Threats to Validity, Limitations & Ethics

- **Geographic & Temporal Scope:** 1 quarter (`Q1 2024`) across 8 stations in England. Multi-city, seasonal, and external-network validation remain incomplete.
- **MNAR Identification:** Missingness in `natural` readings cannot be proved MNAR from observed records alone; simulated MNAR uses a synthetic logit curve calibrated around training statistics.
- **Subgroup Sample Size:** The natural `stale_history` (`age > 12h`) subgroup contains only `n = 12` valid targets and all 12 are abstained.
- **Ethical Responsibility:** No clinical or public-health regulatory advice is implied. Abstention flags low-evidence situations explicitly and must never be equated with confirmed physical hardware breakage.

---

## 10. Reproducibility

```bash
git clone https://github.com/Dinesh0401/SensorGuard-AI.git
cd SensorGuard-AI
git checkout feat/milestone-3-forecasting-uncertainty
pip install -e ".[dev]"

# Verify tests, lint, migrations
ruff check . && ruff format --check .
alembic upgrade head
pytest tests/ -v

# Reproduce benchmark (SG-BENCH-*) and LOSO CV
python scripts/run_real_benchmarks.py

# Reproduce 3-seed ablation (SG-SAGE-ABLATION-*)
python scripts/run_sage_ablations.py

# Retrain pilot serving model
python scripts/train_pilot_model.py
```

Model artifact SHA-256: `e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115`.
