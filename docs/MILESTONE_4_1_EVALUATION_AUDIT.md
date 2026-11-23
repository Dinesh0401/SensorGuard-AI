# SensorGuard AI — Milestone 4.1 Evaluation & Research Integrity Audit

**Audit Status:** APPROVED (Passed Evaluation Integrity Gate)  
**Evaluator:** Statistical Evaluation Reviewer & Senior ML Research Engineer  
**Dataset Lineage Fingerprint:** `33805c42097fc86e` (SHA-256 Content Fingerprint)  
**Evaluation Scope:** London Pilot Sensor Network (Q1 2024 UK AURN, 14,556 Valid Observations)  
**Ablation Study Reference:** `SG-SAGE-ABLATION-20261123-135913`  

---

## 1. Executive Summary & Audit Context

During the Milestone 4 review of the Sensitivity-Aware Guarded Estimation (SAGE) framework, two critical research-integrity issues and two engineering risks were identified:
1. **The Evaluation Denominator Mismatch:** An inconsistency between `calibration.py:compute_interval_metrics()` (which filtered out non-finite targets/bounds) and `ablation.py:run_sage_ablation_suite()` (which applied raw Boolean publication masks on unmasked target arrays). This caused a spurious gap between overall coverage (reported as 99.9%) and published coverage under `no_abstention` (reported as 78.0%), falsely inflating the apparent benefit of guarded abstention.
2. **Feature Leakage under Masked Simulation:** In `prepare_scenario_test_data()`, simulated outages modified `last_pm25` and `age_hours`, but left precomputed `lag_1h`, `lag_2h`, `lag_24h`, rolling statistics (`rolling_mean_6h`, `rolling_mean_24h`), and spatial neighbor features (`neighbor_idw_pm25`) intact. A secondary review also revealed fallback expressions (`.fillna(combined["last_pm25"])`) that risked pulling precomputed values if history was insufficient.
3. **Inadequate Dataset Fingerprinting:** Datasets were hashed by row counts rather than content, risking undetected data divergence.
4. **Observation Revision Ambiguity:** Queries could load multiple versions of the same sensor observation if database revisions occurred.

This audit documents the mathematical resolutions, adversarial test verifications, and empirical rectifications implemented in Milestone 4.1.

---

## 2. Mathematical Analysis & Fix of the Denominator Bug

### 2.1 The Root Cause of the Spurious Gap
Let $\mathcal{D}_{\text{test}} = \{(x_i, y_i)\}_{i=1}^N$ denote candidate test observations. In temporal sensor streams, some observations contain missing targets ($y_i = \text{NaN}$) due to physical sensor drops, or undefined predictions ($y_{\text{pred}, i} = \text{NaN}$) during warmup.

In the uncorrected code:
- **`calibration.py:compute_interval_metrics`** constructed the valid evaluation set:
  $$\mathcal{V} = \{i \in \{1, \dots, N\} \mid y_i \in \mathbb{R} \land y_{\text{pred}, i} \in \mathbb{R} \land L_i \in \mathbb{R} \land U_i \in \mathbb{R}\}$$
  and calculated overall interval coverage strictly over $\mathcal{V}$:
  $$\text{PICP}_{\text{all}} = \frac{1}{|\mathcal{V}|} \sum_{i \in \mathcal{V}} \mathbb{I}(L_i \le y_i \le U_i)$$
- **`ablation.py:run_sage_ablation_suite`** calculated published coverage by applying the Boolean publication mask $\mathcal{P} = \{i \mid \text{status}_i \ne \text{"abstain"}\}$ directly on the raw numpy array $y$:
  $$\text{PICP}_{\text{published}}^{\text{flawed}} = \frac{1}{|\mathcal{P}|} \sum_{i \in \mathcal{P}} \mathbb{I}(L_i \le y_i \le U_i)$$
  Because comparisons with `NaN` evaluate to `False` in IEEE-754 floating-point arithmetic ($L_i \le \text{NaN} \le U_i \implies \text{False}$), all non-finite target rows inside $\mathcal{P}$ were counted in the denominator $|\mathcal{P}|$ but failed the numerator indicator!
  Under `no_abstention`, $\mathcal{P} = \{1, \dots, N\}$. Approximately 22% of rows in the raw grid represented unobserved warmup or missing periods. Consequently, $\text{PICP}_{\text{published}}^{\text{flawed}}$ was deflated to $\sim 78.0\%$, whereas $\text{PICP}_{\text{all}}$ evaluated to $99.9\%$.

### 2.2 Corrected Unified Evaluation Mask
We unified the evaluation mask into `sensorguard.calibration:compute_interval_metrics()`. Given publication indicator array $\mathbf{p} \in \{0, 1\}^N$:
1. **Valid Evaluation Subset:**
   $$\mathcal{V} = \{i \mid \text{isfinite}(y_i) \land \text{isfinite}(y_{\text{pred}, i}) \land \text{isfinite}(L_i) \land \text{isfinite}(U_i)\}$$
2. **Valid Published Subset:**
   $$\mathcal{V}_{\text{pub}} = \{i \in \mathcal{V} \mid p_i = 1\}$$
3. **Valid Abstained Subset:**
   $$\mathcal{V}_{\text{abs}} = \{i \in \mathcal{V} \mid p_i = 0\}$$

The metrics are now rigorously defined as:
$$\text{PICP}_{\text{overall}} = \frac{1}{|\mathcal{V}|} \sum_{i \in \mathcal{V}} \mathbb{I}(L_i \le y_i \le U_i)$$
$$\text{PICP}_{\text{published}} = \begin{cases} \frac{1}{|\mathcal{V}_{\text{pub}}|} \sum_{i \in \mathcal{V}_{\text{pub}}} \mathbb{I}(L_i \le y_i \le U_i) & \text{if } |\mathcal{V}_{\text{pub}}| > 0 \\ \text{None} & \text{if } |\mathcal{V}_{\text{pub}}| = 0 \end{cases}$$
$$\text{Abstention Rate} = \frac{|\mathcal{V}_{\text{abs}}|}{|\mathcal{V}|} = 1 - \frac{|\mathcal{V}_{\text{pub}}|}{|\mathcal{V}|}$$

### 2.3 Mathematical Invariant Proof
When an experiment operates under `no_abstention`, $p_i = 1$ for all $i \in \{1, \dots, N\}$. Therefore:
$$\mathcal{V}_{\text{pub}} \equiv \mathcal{V} \implies |\mathcal{V}_{\text{pub}}| = |\mathcal{V}| \land \sum_{i \in \mathcal{V}_{\text{pub}}} \mathbb{I}(L_i \le y_i \le U_i) = \sum_{i \in \mathcal{V}} \mathbb{I}(L_i \le y_i \le U_i)$$
$$\therefore \text{PICP}_{\text{published}} \equiv \text{PICP}_{\text{overall}} \quad (\forall \text{ conditions where } |\mathcal{V}_{\text{abs}}| = 0)$$

### 2.4 Verification Table: Before vs. After Correction
| Scenario | Condition | Flawed PICP (All) | Flawed Published PICP | Corrected PICP (All) | Corrected Published PICP | Invariant Satisfied? |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Natural** | `no_abstention` | 99.9% | 78.0% | **99.9%** | **99.9%** | **YES ($|\Delta| < 10^{-6}$)** |
| **Natural** | `full_sage` | 99.9% | 83.7% | **99.9%** | **100.0%** | **YES** |
| **MCAR 20%** | `no_abstention` | 99.9% | 78.0% | **99.9%** | **99.9%** | **YES ($|\Delta| < 10^{-6}$)** |
| **MCAR 20%** | `full_sage` | 99.9% | 83.6% | **99.9%** | **100.0%** | **YES** |
| **MNAR 1.5** | `no_abstention` | 99.9% | 78.0% | **99.9%** | **99.9%** | **YES ($|\Delta| < 10^{-6}$)** |
| **MNAR 1.5** | `full_sage` | 99.9% | 83.8% | **99.9%** | **100.0%** | **YES** |
| **Burst 24h** | `no_abstention` | 99.9% | 78.0% | **99.9%** | **99.9%** | **YES ($|\Delta| < 10^{-6}$)** |
| **Burst 24h** | `full_sage` | 99.9% | 83.5% | **99.9%** | **100.0%** | **YES** |

---

## 3. Outage Simulation & Zero-Leakage Feature Reconstruction

### 3.1 The Feature Leakage Risk
In an autoregressive spatiotemporal forecaster, predicting $y_t$ relies on:
- Recent lags: $y_{t-1}, y_{t-2}, y_{t-24}$
- Trailing window statistics: $\text{mean}_{6h}(y), \text{mean}_{24h}(y)$
- Spatial neighbors: $\text{IDW}_{j \ne s}(y_{t-1}^{(j)})$
- Freshness latency: $\text{age}(t) = t - t_{\text{last\_reported}}$

If a simulated outage occurs at $t-1$, setting `last_pm25 = lag_1h` (which is $y_{t-2}$) without updating `lag_1h`, `lag_2h`, and `rolling_mean_6h` allows the model to indirectly observe the hidden measurement $y_{t-1}$ through `rolling_mean_6h` or `neighbor_idw_pm25`. Furthermore, fallback expressions like `combined["last_pm25"] = last_val.fillna(combined["last_pm25"])` allow precomputed values to persist whenever warmup history is truncated.

### 3.2 Complete Elimination of Fallback Expressions
We restructured `prepare_scenario_test_data()` with defensive elimination of all precomputed columns:
1. **Upfront Dropping of Reconstructed Columns:**
   ```python
   reconstructed_cols = [
       "last_pm25",
       "lag_1h",
       "lag_2h",
       "lag_24h",
       "rolling_mean_6h",
       "rolling_std_6h",
       "rolling_mean_24h",
       "neighbor_idw_pm25",
       "age_hours",
   ]
   clean_test = scenario_test.drop(columns=reconstructed_cols, errors="ignore").copy()
   clean_hist = history_df.drop(columns=reconstructed_cols, errors="ignore").copy()
   ```
   Because these columns do not exist in the working DataFrame, **no expression can fall back to precomputed values**.
2. **Complete Pre-Test Station History:**
   Instead of `cal_df.tail(48)`, the ablation runner now provides the full continuous timeline:
   $$\text{history\_df} = \text{pd.concat}([\text{train\_df}, \text{cal\_df}], \text{ignore\_index}=\text{True})$$
   This supplies $>11,000$ continuous historical observation rows for every station, ensuring uninterrupted 24-hour lag and rolling window coverage.
3. **Pure Recomputation from Masked Stream:**
   For each station timeline:
   $$\text{visible}_t = \begin{cases} y_t & \text{if } \text{reported}_t \land \neg\text{outage}_t \\ \text{NaN} & \text{otherwise} \end{cases}$$
   $$\text{last\_pm25}_t = \text{ffill}(\text{visible}_{<t})$$
   $$\text{lag\_1h}_t = \text{ffill}(\text{visible}_{<t-1}), \quad \text{lag\_2h}_t = \text{ffill}(\text{visible}_{<t-2}), \quad \text{lag\_24h}_t = \text{ffill}(\text{visible}_{<t-23})$$
   $$\text{rolling\_mean\_6h}_t = \frac{1}{|\mathcal{W}_{6h}|} \sum_{k \in \mathcal{W}_{6h}} \text{last\_pm25}_k$$
   $$\text{age\_hours}_t = \frac{t - t_{\text{last\_visible}}}{3600}$$
4. **Spatial IDW Masking:**
   At each timestamp $t$, neighboring stations are included in the inverse-distance weighted average if and only if:
   $$\text{age\_hours}_t^{(j)} \le 3.0 \text{ hours}$$
   If a neighbor suffers an outage exceeding 3 hours, it is dynamically excluded from spatial interpolation.

---

## 4. Adversarial Regression Test Evidence

We instituted four specialized regression tests in `tests/test_ablations.py`:

### Test 1: `test_adversarial_feature_poisoning_excluded`
- **Methodology:** The input test DataFrame was deliberately corrupted by setting all precomputed columns to impossible sentinel values:
  `last_pm25 = 999999.0`, `lag_1h = -888888.0`, `lag_2h = 777777.0`, `lag_24h = -666666.0`, `rolling_mean_6h = 555555.0`, `rolling_mean_24h = -444444.0`, `neighbor_idw_pm25 = 333333.0`, `age_hours = -111.0`.
- **Assertion:** Rebuilding features under `mcar_20` asserts that `np.isclose(scen_df[col], p_val).any() == False` for every column and every sentinel value.
- **Result:** PASSED (zero sentinel values present).

### Test 2: `test_no_abstention_coverage_invariant`
- **Methodology:** Evaluates `run_sage_ablation_suite` under `no_abstention` across all scenarios.
- **Assertion:** Asserts $|\text{published\_picp} - \text{picp}| < 10^{-6}$ and $\text{abstention\_rate} == 0.0$.
- **Result:** PASSED across all scenarios.

### Test 3: `test_masked_features_recomputed_no_leakage`
- **Methodology:** Simulates a 24-hour contiguous blackout (`burst_24h`) on station `st_1`.
- **Assertion:** Asserts that during the blackout, `age_hours` increases monotonically, `target` is untouched in evaluator-only storage, and visible features contain no blackout readings.
- **Result:** PASSED.

### Test 4: `test_content_hash_deterministic`
- **Methodology:** Computes SHA-256 fingerprint over mock splits; alters a single floating-point target observation by $+50.0$.
- **Assertion:** Asserts that hash is deterministic on identical data and strictly changes upon value mutation.
- **Result:** PASSED (`hash1 != hash2`).

---

## 5. Propensity Indicator ($R$) Definition & Observable Approximations

In SAGE sensitivity analysis, the propensity weight $w(x)$ adjusts for non-random reporting:
$$w(x, y; \gamma) = \frac{\exp(-\gamma \cdot z(y))}{\mathbb{E}[\exp(-\gamma \cdot z(y)) \mid X=x]}$$
Where $R \in \{0, 1\}$ denotes whether an observation was physically recorded and received:
- **Observable Representation:** In the continuous hourly reindexed pilot network, $R_t = 1$ when a physical sensor measurement is recorded and validated ($ quality\_flag == "VALID" $), and $R_t = 0$ when an hourly period has no report or is flagged invalid.
- **Approximation Validity:** When training on unmasked datasets where unobserved rows are excluded from the training split, logistic regression estimates propensity $\hat{e}(x) = P(R=1 \mid X=x)$ using observable environmental and cyclic features (hour-of-day, day-of-week, neighboring station values, and historical freshness).
- **Research Boundary:** In real-world sensor deployments where the true missingness mechanism is missing-not-at-random (MNAR), $R$ depends on the latent, unobserved concentration $y$. SAGE models this sensitivity over a bounded grid $\Gamma = \{0.0, 0.5, 1.0, 1.5, 2.0\}$ rather than claiming exact propensity identification.

---

## 6. Scientific Limitations & Research Boundaries

1. **Conservative Over-Coverage (99.9% vs 90.0%):**
   The empirical coverage of SAGE on Q1 2024 London data is $99.9\%$, significantly above the nominal $90\%$ target. This occurs because SAGE forms its prediction interval as the outer envelope over multiple $\gamma \in \Gamma$ plus worst-case sensitivity shifts $\Delta_\gamma$:
   $$L = \min_{\gamma \in \Gamma} (y_{\text{pred}} - r_\gamma - \Delta_\gamma), \quad U = \max_{\gamma \in \Gamma} (y_{\text{pred}} + r_\gamma + \Delta_\gamma)$$
   The union of multiple valid $90\%$ conformal intervals is conservative. SAGE prioritizes fail-safe containment under unknown adversarial shifts over narrow interval sharpness.
2. **Abstention Trade-off:**
   Guarded abstention withholds forecasts when sensor staleness exceeds 12 hours or scenario disagreement exceeds $20.0 \ \mu\text{g/m}^3$. In the London pilot, this resulted in a $12.3\% - 13.0\%$ abstention rate. While this achieved $100.0\%$ published empirical coverage, downstream consumers must handle explicit abstention notices (`"status": "abstain"`, `"reason": "stale_sensor_history"`).
3. **No Unrestricted Coverage Guarantees:**
   Conformal guarantees strictly require exchangeability. Under arbitrary, unbounded MNAR shifts, no mathematical guarantee holds without parametric bounds on the selection odds ratio $\Gamma$. SensorGuard AI documents its results within the sensitivity grid $\Gamma \le 2.0$.
