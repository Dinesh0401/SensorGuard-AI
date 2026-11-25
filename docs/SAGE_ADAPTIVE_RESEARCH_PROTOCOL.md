# SensorGuard AI — Adaptive Input-Dependent SAGE Research Protocol & Results

**Milestone:** 7.4 — Input-Dependent Heteroscedastic Sensitivity Enveloping  
**Study ID:** `SG-M74-ADAPTIVE-20261125-120901`  
**Dataset Hash:** `33805c42097fc86e` (Archived Q1 2024 UK AURN Network, 8 stations, `14,556` raw valid obs)  
**Seeds Evaluated:** `[11, 42, 107]` across `MCAR 20%`, `MNAR gamma=1.5`, and `Burst 24h` (`natural` evaluated deterministically)  
**Artifact JSON:** [`docs/audit/adaptive_sage_results.json`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/adaptive_sage_results.json)  
**Implementation:** [`src/sensorguard/models/sage_adaptive.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage_adaptive.py) · [`scripts/run_adaptive_sage_research.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/run_adaptive_sage_research.py)  

---

## 1. Diagnosis of M6.1 Constant Disagreement Limitation

In M6.1 [`SAGEForecaster`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage.py), `calibrate()` derived scalar half-widths $h_\gamma = \max(r_\gamma + \gamma \sigma_{\text{res}}/2, h_{\gamma_{\text{prev}}})$ applied uniformly to every prediction. Consequently:
1. $D_{\text{sensitivity}}(x) = \max_\gamma U_\gamma(x) - \min_\gamma U_\gamma(x) = h_{2.0} - h_{0.0} = \mathbf{3.3206\ \mu\text{g/m}^3}$ for every test input (`std = 0.0`), yielding zero station/hour discrimination (`abstention = 0.44%` natural driven strictly by `age_hours > 12.0`).
2. Calm intervals inherited the global 90th-percentile error radius, inflating natural width to `17.01 µg/m³` and losing on Winkler score to `LightGBM + CQR` on `8 / 8` LOSO stations.

---

## 2. Prespecified Adaptive Formulation & Acceptance Criteria

### 2.1 Strictly Causal Adaptive Architecture (`AdaptiveSAGEForecaster`)
1. **Out-of-Fold Local Scale Estimator $\hat{s}(x)$ (strictly causal forward expanding-window splits within `train`):** Partition `train` chronologically into $K=3$ contiguous sequential blocks. Fold 0 serves strictly as historical warmup anchor; for fold $k \in \{1 \dots K-1\}$, base forecasters are trained strictly on prior historical blocks $\bigcup_{j=0}^{k-1} B_j$ (timestamps $t < t_{\text{eval}}$) and evaluated on $B_k$. The local scale estimator $\hat{s}(x)$ (shallow LightGBM L1 regressor predicting $\log \hat{s}(x)$, with `min_scale = 0.25`, `max_scale = 3.0`) is fit strictly on these forward, causal out-of-sample residuals with zero future lookahead.
2. **Causal Propensity Estimation on Unfiltered Pre-Calibration Window:** Fit $P(R=1 \mid X)$ on all pre-calibration rows ($t < t_{\text{cal\_start}}$, containing both `reported=True` and `reported=False` hours) with clipping `[0.01, 0.99]`.
3. **Input-Dependent Weighted Quantiles & Sensitivity Shifts on `calibrate`:** Compute normalized calibration nonconformity scores $\tilde{e}_i = |y_i - \hat{\mu}(X_i)| / \hat{s}(X_i)$, weighted by $1 / \pi_\gamma(X_i, Y_i)$, yielding $\tilde{q}_\gamma$ and input-dependent half-width:
   $$h_\gamma(x) = \max\!\left(\hat{s}(x)\,\tilde{q}_\gamma + \gamma \cdot \frac{\hat{s}(x)}{2}\left(1 + (1 - \hat{p}(x))\right),\ h_{\gamma_{\text{prev}}}(x)\right)$$
4. **Pre-Specified Policy Thresholds (Zero Test Peeking):** Evaluated under fixed harmonized threshold $\tau = 8.0$ (`sage_adaptive_tau8`), calibration 95th-percentile threshold $\tau = P_{95}(D_{\text{sens}}(X_{\text{cal}})) = \mathbf{3.6713\ \mu\text{g/m}^3}$ (`sage_adaptive_cal95`), and `no_abstain`.

### 2.2 Prespecified Evaluation Criteria
- **C1 (Non-constant discrimination):** `std(D_sensitivity) > 0` and Spearman $\rho(D_{\text{sensitivity}}(x), |y - \hat{y}(x)|) > 0.20$.
- **C2 (Calibration validity):** Empirical `PICP >= 90.0%` (`95%` Wilson CI lower bound `>= 89.0%`) across all scenarios.
- **C3 (Sharpness improvement):** Strictly narrower `MPIW` and lower `Winkler` than Static SAGE (`17.01` / `17.06`), plus direct comparison vs `CQR`, `Locally-Weighted MAR`, and `Observation-Gated Masked ACI`.

---

## 3. Multi-Regime Chronological Evaluation (`2,727` Valid Test Obs)

### 3.1 Natural Regime (`seed=42`, with Exact 95% Wilson Confidence Intervals)

| Method | Overall PICP `[95% CI]` | Published PICP | MPIW (µg/m³) | Winkler | Abstention | $D_{\text{sens}}$ Mean ± Std ($\rho$ vs $|e|$) |
|---|---|---:|---:|---:|---:|---|
| **persistence** (Split) | `96.88%` `[.9616, .9747]` | `97.02%` | **`2.00`** | **`3.16`** | `0.44%` | `—` |
| **lightgbm_aci** (online) | `90.87%` `[.8973, .9189]` | `91.05%` | `2.48` | `3.49` | `0.44%` | `—` |
| **masked_observation_aci** | `89.11%` `[.8788, .9022]` | `89.32%` | `2.14` | `3.26` | `0.44%` | `—` *(slightly `< 90%`)* |
| **lightgbm_rolling** (168h) | `90.80%` `[.8965, .9182]` | `90.94%` | `3.06` | `4.13` | `0.44%` | `—` |
| **locally_weighted_mar** ($\gamma=0$) | `98.94%` `[.9848, .9926]` | `99.08%` | `4.39` | `4.62` | `0.44%` | `0.00 ± 0.00` |
| **lightgbm_cqr** (Romano 2019) | `99.89%` `[.9968, .9996]` | `99.89%` | `11.35` | `11.45` | `0.44%` | `—` |
| **lightgbm_split** | `99.85%` `[.9962, .9994]` | `99.85%` | `13.23` | `13.35` | `0.44%` | `—` |
| **sage_static** (M6.1) | `99.93%` `[.9973, .9998]` | `99.93%` | `17.01` | `17.06` | `0.44%` | `3.32 ± 0.00` ($\rho=-0.128$) |
| **sage_adaptive_tau8** | `99.56%` `[.9923, .9975]` | `99.67%` | `5.67` | `5.81` | `0.44%` | `0.67 ± 0.44` ($\mathbf{\rho=+0.223}$) |
| **sage_adaptive_cal95** | `99.56%` `[.9923, .9975]` | `99.67%` | `5.67` (`5.42` pub) | `5.81` | `1.17%` | `0.67 ± 0.44` ($\mathbf{\rho=+0.223}$) |

### 3.2 Outage Stress Regimes (Mean ± Std Across Seeds `[11, 42, 107]`)

| Scenario | Method | Overall PICP | Published PICP | MPIW (µg/m³) | Winkler | Abstention | $D_{\text{sens}}$ ($\rho$ vs $|e|$) |
|---|---|---:|---:|---:|---:|---:|---|
| `mcar_20` | `lightgbm_cqr` | `99.86% ± .02%` | `99.86%` | `11.40` | `11.51` | `0.55%` | `—` |
| `mcar_20` | `locally_weighted_mar` | `99.01% ± .10%` | `99.18%` | `4.44` | `4.71` | `0.55%` | `0.00` |
| `mcar_20` | `sage_static` | `99.92% ± .02%` | `99.92%` | `17.02` | `17.07` | `0.55%` | `3.32 ± 0.00` |
| `mcar_20` | **`sage_adaptive_cal95`** | `99.50% ± .03%` | `99.62%` | **`5.75`** (`5.54` pub) | **`5.92`** | `1.16%` | `0.68 ± 0.43` ($\mathbf{+0.300}$) |
| `mnar_gamma_1.5` | `persistence` | `95.44% ± .31%` | `96.01%` | `2.00` | `4.20` | `1.09%` | `—` |
| `mnar_gamma_1.5` | `masked_observation_aci` | **`88.78% ± .12%`** | `89.27%` | `2.35` | `3.65` | `1.09%` | `—` *(below 90% target)* |
| `mnar_gamma_1.5` | `lightgbm_aci` | `90.63% ± .09%` | `91.16%` | `2.74` | `4.22` | `1.09%` | `—` |
| `mnar_gamma_1.5` | `lightgbm_cqr` | `99.78% ± .00%` | `99.92%` | `11.42` | `11.69` | `1.09%` | `—` |
| `mnar_gamma_1.5` | `locally_weighted_mar` | `97.98% ± .26%` | `98.49%` | **`4.27`** | **`5.28`** | `1.09%` | `0.00` |
| `mnar_gamma_1.5` | `sage_static` | `99.85% ± .00%` | `99.96%` | `16.99` | `17.10` | `1.09%` | `3.32 ± 0.00` ($\rho=-0.115$) |
| `mnar_gamma_1.5` | **`sage_adaptive_cal95`** | `98.60% ± .23%` | `99.02%` | **`5.57`** (`5.55` pub) | **`6.32`** | `1.13%` | `0.66 ± 0.20` ($\mathbf{+0.317}$) |
| `burst_24h` | `lightgbm_cqr` | `99.89% ± .00%` | `99.89%` | `11.36` | `11.46` | `0.92%` | `—` |
| `burst_24h` | `sage_static` | `99.93% ± .00%` | `99.93%` | `17.01` | `17.06` | `0.92%` | `3.32 ± 0.00` |
| `burst_24h` | **`sage_adaptive_cal95`** | `99.56% ± .00%` | `99.66%` | **`5.68`** (`5.42` pub) | **`5.81`** | `1.65%` | `0.67 ± 0.44` ($\mathbf{+0.232}$) |

---

## 4. 8-Fold Leave-One-Station-Out (LOSO) Spatial Generalization

| Held-Out Station | LightGBM CQR (PICP / Width / Winkler) | Static SAGE (PICP / Width / Winkler) | Adaptive SAGE `cal95` (PICP `[95% CI]` / Width / Winkler / Abs) | $D_{\text{sens}}$ ($\rho$ vs $|e|$) |
|---|---|---|---|---|
| `station_1` (Harlington) | `94.97%` / `3.70` / `4.10` | `99.55%` / `8.18` / `8.24` | `99.23%` `[.9865,.9956]` / `3.80` / **`3.92`** (`0.32%`) | `0.39 ± 0.49` (`+0.308`) |
| `station_2` (Hillingdon) | `95.37%` / `3.41` / `3.61` | `100.0%` / `8.47` / `8.47` | `99.32%` `[.9883,.9960]` / `3.47` / **`3.57`** (`2.37%`) | `0.35 ± 0.44` (`+0.341`) |
| `station_3` (Marylebone) | `90.54%` / `3.96` / `5.48` | `97.53%` / `8.34` / `9.29` | `98.16%` `[.9732,.9874]` / `4.37` / **`4.83`** (`4.59%`) | `0.44 ± 0.59` (`+0.432`) |
| `station_4` (Slade Green) | `81.72%` / `3.37` / `11.46` | `90.72%` / `4.76` / `13.23` | **`90.54%`** `[.8922,.9170]` / `4.08` / **`10.74`** (`0.51%`) | `0.42 ± 0.52` (`+0.365`) |
| `station_5` (Southampton) | `96.00%` / `3.10` / `3.38` | `100.0%` / `8.36` / `8.36` | `99.36%` `[.9888,.9963]` / `3.05` / **`3.19`** (`1.60%`) | `0.31 ± 0.26` (`+0.420`) |
| `station_6` (Wicken Fen) | `93.84%` / `4.03` / `4.82` | `99.14%` / `7.94` / `8.32` | `99.52%` `[.9909,.9975]` / `3.71` / **`3.78`** (`1.93%`) | `0.38 ± 0.45` (`+0.380`) |
| `station_7` (St Osyth) | `95.32%` / `3.26` / `4.08` | `99.06%` / `7.86` / `8.51` | `98.54%` `[.9790,.9899]` / `3.49` / **`3.79`** (`0.83%`) | `0.35 ± 0.43` (`+0.167`) |
| `station_8` (Manchester) | `94.06%` / `3.78` / `5.10` | `99.04%` / `8.42` / `9.33` | `98.61%` `[.9797,.9905]` / `3.79` / **`4.16`** (`3.26%`) | `0.38 ± 0.46` (`+0.412`) |

---

## 5. Honest Assessment & Preserved Negative Results

1. **Criteria C1–C3 Met Against Static SAGE & CQR:** Adaptive SAGE eliminates constant $D_{\text{sensitivity}}$ (`std` `0.00 -> 0.20..0.59`, Spearman correlation with true error **`+0.22..+0.43`** across all scenarios and `7/8` LOSO folds $>0.30$), cuts interval width by **`~67%`** vs Static SAGE (`17.01 -> 5.67` natural), and outperforms `LightGBM + CQR` on Winkler score across **all 4 regimes** AND **all 8 LOSO stations** (including rescuing `station_4` coverage from `81.72%` to `90.54%` while beating CQR Winkler `10.74` vs `11.46`).
2. **Negative Result 1 — Heteroscedastic Scaling Drives Most of the Gain (`locally_weighted_mar`):** The $\gamma=0$ adaptive control (`locally_weighted_mar`) already achieves `4.39` natural / `4.27` MNAR MPIW and `4.62` / `5.28` Winkler with `97.98%`–`98.94%` coverage. Adding the heuristic MNAR $\gamma$-grid adds `+1.30 µg/m³` average width for `+0.62pp` MNAR coverage, confirming that local scale normalization $\hat{s}(x)$ — rather than MNAR $\gamma$ sensitivity alone — accounts for most of the sharpness improvement.
3. **Negative Result 2 — Simpler Methods Still Win Raw Sharpness:** On calm Q1 2024 UK AURN hours, `Persistence + Split Conformal` (`2.00`, Winkler `3.16`) and `LightGBM + ACI` (`2.48–2.74`, Winkler `3.49–4.22`) remain sharper and lower-Winkler than Adaptive SAGE.
4. **Negative Result 3 — `masked_observation_aci` Under-Coverage & `station_4` CI Margin:** Our bounded observation-gated ACI variant achieved tight intervals (`2.14–2.35 µg/m³`) **but failed the 90% target** (`89.11%` natural, `88.78%` MNAR). On `station_4`, Adaptive SAGE's 95% Wilson CI lower bound (`89.22%`) satisfies our prespecified `>= 89.0%` floor only narrowly.
5. **Scope Boundary:** No formal finite-sample MNAR theorem is claimed; `sage_adaptive` remains an audited empirical prototype tested on one 90-day UK region (`Q1 2024`). Multi-region / seasonal generalization remains unvalidated.
