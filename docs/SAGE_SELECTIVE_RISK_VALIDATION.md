# SAGE: Selective-Prediction Risk Validation & Algorithmic Audit
**Milestone 5.2 Algorithmic Correction and Empirical Evaluation**

- **Author:** Senior ML Research Engineer, Statistical Evaluation Reviewer
- **Target Algorithms:** SAGE (Sensitivity-Aware Guarded Ensemble), CQR (Conformalized Quantile Regression), Split Conformal Baselines
- **Dataset:** UK AURN London Urban Air Quality Network (Q1 2024 Pilot, 8 Stations, $N_{\text{test}} = 3,496$, $N_{\text{valid}} = 2,727$)
- **Audit Date:** November 25, 2026

---

## 1. Algorithmic Correction: Scenario Disagreement vs. Envelope Width

In Milestone 5.1, an evaluation defect caused SAGE to report 100% abstention across natural and missingness benchmark scenarios. This was caused by conflating the **total prediction interval envelope width** with **sensitivity scenario disagreement**.

### Mathematical Formulation
Let $\gamma \in \Gamma$ index the missingness sensitivity scenarios evaluated by SAGE (e.g., $\gamma \in \{0.0, 0.5, 1.0, 1.5, 2.0\}$ representing odds ratios of missingness-not-at-random mechanisms), with corresponding upper and lower conformal bounds $[L_\gamma, U_\gamma]$.

1. **Total Conservative Envelope Width ($W_{\text{envelope}}$):**
   $$W_{\text{envelope}} = U_{\max} - L_{\min} = \max_{\gamma \in \Gamma} U_\gamma - \min_{\gamma \in \Gamma} L_\gamma$$
   This represents the absolute span between the most conservative upper bound and the most conservative lower bound. Because the point forecast error residuals on PM2.5 in London have a substantial dynamic range, $W_{\text{envelope}}$ averages **$17.01 \ \mu\text{g/m}^3$**.

2. **Sensitivity Scenario Disagreement ($D_{\text{sensitivity}}$):**
   $$D_{\text{sensitivity}} = \max_{\gamma \in \Gamma} U_\gamma - \min_{\gamma \in \Gamma} U_\gamma = q_{\max} - q_{\min}$$
   This isolates the parameter sensitivity: how much the upper bound shifts across differing assumptions about the unobserved missingness mechanism $\gamma$. In natural air quality conditions, $D_{\text{sensitivity}}$ averages **$3.32 \ \mu\text{g/m}^3$**.

### The Defect and the Correction
In previous code, the decision rule checked:
$$\text{If } W_{\text{envelope}} > \tau_{\text{disagree}} \implies \text{Abstain}$$
With $\tau_{\text{disagree}} = 8.0 \ \mu\text{g/m}^3$ and $W_{\text{envelope}} \approx 17.01 \ \mu\text{g/m}^3$, the rule evaluated to `True` on **100% of samples**, causing catastrophic total abstention regardless of whether the sensor was healthy or failing.

The corrected guarded decision rule implemented in [sage.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/models/sage.py) evaluates:
$$\text{Abstain} \iff \begin{cases}
D_{\text{sensitivity}} > \tau_{\text{disagree}} & (\tau_{\text{disagree}} = 8.0 \ \mu\text{g/m}^3) \\
W_{\text{envelope}} > \tau_{\text{envelope}} & (\tau_{\text{envelope}} = 50.0 \ \mu\text{g/m}^3) \\
\text{Observation Age} > 12.0\text{ hours} & (\text{Stale history failure}) \\
\text{Missing Feature Imputation Failure} & (\text{Cold-start failure})
\end{cases}$$

---

## 2. Empirical Benchmark Results (Q1 2024 London AURN)

With the corrected formulation and harmonized thresholds ($\tau_{\text{disagree}} = 8.0$, $\tau_{\text{envelope}} = 50.0$), the benchmark was rerun across all four test regimes from [benchmark_results.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/benchmark_results.json):

| Scenario | Model Configuration | Total Samples | Published | Abstained | Abstention Rate | Overall PICP | Published PICP | Mean Width (MPIW) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Natural** | SAGE (Guarded) | 2,727 | 2,715 | 12 | 0.44% | 99.93% | **99.93%** | 17.01 µg/m³ |
| | SAGE (No Abstain) | 2,727 | 2,727 | 0 | 0.00% | 99.93% | 99.93% | 17.01 µg/m³ |
| | LightGBM + CQR | 2,727 | 2,715 | 12 | 0.44% | 99.89% | 99.89% | **11.35 µg/m³** |
| | Persistence + Conformal | 2,727 | 2,715 | 12 | 0.44% | 96.88% | 97.02% | **2.00 µg/m³** |
| **MCAR 20%** | SAGE (Guarded) | 2,727 | 2,711 | 16 | 0.59% | 99.93% | **99.93%** | 17.01 µg/m³ |
| | SAGE (No Abstain) | 2,727 | 2,727 | 0 | 0.00% | 99.93% | 99.93% | 17.01 µg/m³ |
| | LightGBM + CQR | 2,727 | 2,711 | 16 | 0.59% | 99.89% | 99.89% | 11.41 µg/m³ |
| **MNAR ($\gamma=1.5$)** | SAGE (Guarded) | 2,727 | 2,701 | 26 | 0.95% | 99.85% | **99.96%** | 16.98 µg/m³ |
| | SAGE (No Abstain) | 2,727 | 2,727 | 0 | 0.00% | 99.85% | 99.85% | 16.98 µg/m³ |
| | LightGBM + CQR | 2,727 | 2,701 | 26 | 0.95% | 99.78% | 99.93% | 11.42 µg/m³ |
| **Burst (24h Outage)** | SAGE (Guarded) | 2,727 | 2,702 | 25 | 0.92% | 99.93% | **99.93%** | 17.01 µg/m³ |
| | SAGE (No Abstain) | 2,727 | 2,727 | 0 | 0.00% | 99.93% | 99.93% | 17.01 µg/m³ |
| | LightGBM + CQR | 2,727 | 2,702 | 25 | 0.92% | 99.89% | 99.89% | 11.36 µg/m³ |

---

## 3. Analysis of Selective Abstention & Risk Curves

### How Guarded Abstention Operates Under MNAR
In the MNAR ($\gamma = 1.5$) scenario, sensors drop readings systematically when PM2.5 concentrations are elevated:
- Without abstention (`sage_no_abstain`), the model produces 4 coverage violations out of 2,727 predictions ($\text{PICP} = 99.85\%$).
- With guarded abstention (`sage_full`), SAGE detects heightened sensitivity and stale covariates, selectively abstaining on 26 forecasts.
- **Coverage on Published Forecasts rises to 99.96%** (only 1 violation out of 2,701 published predictions).
- This validates the selective prediction property: when the model abstains, it disproportionately removes high-risk forecasts that would have failed coverage.

### Honest Baseline Comparison and Negative Results
While SAGE achieves exceptional coverage ($99.93\% - 99.96\%$), scientific integrity requires transparent comparison against simpler baselines:
1. **Interval Width Efficiency:**
   - Under natural test conditions, LightGBM + CQR achieves $99.89\%$ coverage with a mean interval width of **$11.35 \ \mu\text{g/m}^3$**.
   - SAGE requires **$17.01 \ \mu\text{g/m}^3$** ($+49.9\%$ wider) to achieve $99.93\%$ coverage.
   - Persistence + split conformal achieves $97.02\%$ coverage with a remarkably narrow width of **$2.00 \ \mu\text{g/m}^3$**.
2. **Scientific Conclusion:**
   - SAGE cannot be claimed as an all-around replacement for standard conformal prediction in benign environments.
   - Its primary value is an **adversarial/stress safety mechanism**: when sensors undergo unmodeled missingness or prolonged outages, SAGE bounds the worst-case sensitivity envelope and triggers abstention, preventing confident, misleading forecasts from propagating to downstream public health systems.
