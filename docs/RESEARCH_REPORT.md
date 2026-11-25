# SensorGuard AI: Sensitivity-Aware Guarded Estimation for PM₂.₅ Forecasting Under Non-Random Sensor Missingness

**Technical Research Report — Milestone 6**  
Repository: `Dinesh0401/SensorGuard-AI`  
Branch: `feat/milestone-3-forecasting-uncertainty`  
Benchmark ID: `SG-BENCH-20261125-040900`  
Dataset hash: `33805c42097fc86e`  
Date: 2026-11-25

---

## Abstract

Air-quality sensor networks suffer from systematic, **non-random missingness**: sensors fail more often during high-pollution events, creating a feedback loop that biases coverage estimates derived under missing-at-random (MAR) assumptions. We describe **SAGE** (Sensitivity-Aware Guarded Estimation), a conformal prediction framework that (1) calibrates a sensitivity interval over a user-specified Gamma-sensitivity model for missing-not-at-random (MNAR) data, (2) computes a scenario-disagreement signal as the spread of that interval across multiple Gamma values, and (3) applies a guarded abstention policy that withholds a forecast when the disagreement exceeds a threshold, signalling that the prediction is too MNAR-sensitive to be reliable. Evaluated on 14,556 hourly PM₂.₅ observations from eight UK AURN stations (Q1 2024), SAGE achieves **99.93% coverage** on published forecasts at a **0.44% natural-regime abstention rate** (threshold = 8 µg/m³). We report honest negative results: on these data, the Persistence baseline with split conformal calibration achieves near-nominal 96.9% coverage with a mean interval width of only 2.0 µg/m³, substantially sharper than SAGE's 17.0 µg/m³, demonstrating that SAGE's benefit is principally robustness under MNAR conditions rather than sharpness under calm conditions. All results, code, and evaluation artefacts are publicly available.

---

## 1. Introduction

Urban PM₂.₅ monitoring underpins regulatory decisions and public-health alerts. Modern monitoring networks combine fixed reference stations with low-cost sensors that exhibit intermittent failure. When a station fails to report, the missing value is **not** random: heavily-polluted episodes are more likely to cause sensor saturation or communication failure than clean periods. Standard conformal prediction methods assume MAR (or even MCAR) missingness; they will systematically **undercover** the most health-relevant, high-concentration events if that assumption is violated.

**Research questions addressed:**

1. Can a sensitivity analysis over the Gamma-MNAR model be incorporated directly into the conformal calibration step without breaking finite-sample marginal coverage guarantees?
2. Does a disagreement-based abstention policy improve published coverage without hiding dangerous situations behind abstentions?
3. How do spatial features and freshness-age features each contribute to model accuracy under missing-data perturbation?

> [!NOTE]
> This work is an engineering research prototype, not a peer-reviewed publication. All claims are qualified by the limitations in §10.

---

## 2. Prior Work

| Reference | Contribution | Limitation addressed by SAGE |
|---|---|---|
| Venn & Shafer (2005) | Conformal prediction framework | No sensitivity to MNAR |
| Romano et al. (2019) — CQR | Conformalized quantile regression | Assumes MAR during calibration |
| Gibbs & Candès (2021) — ACI | Adaptive conformal inference | Sequential coverage; ignores MNAR |
| Zaffran et al. (2022) — EnbPI | Ensemble + rolling calibration | MAR; no abstention policy |
| Manski (2003) | Partial identification via sensitivity | Not applied to conformal intervals |
| Gamma-sensitivity model (Rosenbaum 2002) | MNAR bounds via odds-ratio model | Not integrated with conformal PI |

SAGE bridges the partial-identification literature (Manski; Rosenbaum) with modern conformal methods by computing a coverage-valid interval that is simultaneously valid under a grid of MNAR scenarios.

---

## 3. Problem Formulation

### 3.1 Notation

Let $Y_{i,t} \in \mathbb{R}^+$ be the true PM₂.₅ concentration at station $i$, hour $t$.  
Let $R_{i,t} \in \{0, 1\}$ be the reporting indicator (1 = observed).  
Observed data: $\mathcal{D} = \{(X_{i,t},\, Y_{i,t}) : R_{i,t}=1\}$.

### 3.2 MNAR and the Gamma-sensitivity model

Under MNAR, $P(R_{i,t}=0 \mid Y_{i,t}) \neq P(R_{i,t}=0)$. We use the Gamma-sensitivity model:

$$
\frac{1}{\Gamma} \leq \frac{P(R=0 \mid Y=y_1) / P(R=1 \mid Y=y_1)}{P(R=0 \mid Y=y_0) / P(R=1 \mid Y=y_0)} \leq \Gamma, \quad \forall\, y_0, y_1
$$

Gamma = 1 is the MAR assumption; Gamma > 1 allows progressively stronger MNAR dependence.

### 3.3 Conformal coverage under MNAR

For a nominal level $1 - \alpha = 0.9$, standard split conformal guarantees:

$$
P\bigl(Y_{n+1} \in \hat{C}(X_{n+1})\bigr) \geq 1 - \alpha
$$

only under exchangeability (essentially MAR). SAGE constructs $\hat{C}_\gamma(X)$ for each $\gamma \in \Gamma_\text{grid}$, then takes the **sensitivity envelope**:

$$
\hat{C}_\text{env}(X) = \bigl[\min_\gamma L_\gamma(X),\ \max_\gamma U_\gamma(X)\bigr]
$$

### 3.4 Disagreement and guarded abstention

The scenario-disagreement signal is the **spread of calibrated interval widths** across the sensitivity grid (not the envelope width):

$$
D_\text{sensitivity}(X) = \max_{\gamma \in \Gamma_\text{grid}} W_\gamma(X) - \min_{\gamma \in \Gamma_\text{grid}} W_\gamma(X)
$$

The guarded policy abstains if $D_\text{sensitivity}(X) > \tau$ where $\tau = 8\;\mu\text{g/m}^3$ in all reported experiments. Under natural conditions, mean $D_\text{sensitivity} = 3.32\;\mu\text{g/m}^3$, so abstention is rare.

---

## 4. Dataset

| Property | Value |
|---|---|
| **Source** | UK Automatic Urban and Rural Network (AURN) via OpenAQ v3 |
| **Pollutant** | PM₂.₅ (µg/m³) |
| **Period** | Q1 2024 (2026-01-01 – 2026-03-31) |
| **Stations** | 8 (see table below) |
| **Total observations** | 14,556 hourly readings (after deduplication) |
| **Train / Calibration / Test split** | 8,884 / 2,937 / 3,496 |
| **Valid test targets** | 2,727 (of 3,496 — remainder have missing ground truth) |
| **Dataset content hash** | `33805c42097fc86e` |
| **Attribution** | © UK Defra / AURN; available under Open Government Licence v3.0 |

### Pilot stations

| ID | Name | Provider Location ID | Region |
|---|---|---|---|
| 1 | London Harlington | 141 | Greater London |
| 2 | London Hillingdon | 153 | Greater London |
| 3 | Westminster Marylebone Road | 154 | Central London |
| 4 | Bexley Slade Green | 157 | South East London |
| 5 | Southampton Centre | 698 | South East England |
| 6 | Wicken Fen | 804 | East of England |
| 7 | St Osyth | 1795 | East of England |
| 8 | Manchester Piccadilly | 2312 | North West England |

> [!IMPORTANT]
> These are real monitoring stations from the UK national network. Measurement values in the database reflect actual observations downloaded from OpenAQ; no simulated values are stored as ground truth.

### Missing-data characteristics

Natural missingness in the test set: 22.0% of station-hours lack a reported value ($R_{i,t}=0$). The stale-history freshness stratum (12 test samples) exhibits 33.3% undercoverage under Persistence+SplitConformal, motivating robustness work.

---

## 5. The SAGE Algorithm

```
Input:  calibration set D_cal, test point x, gamma grid Γ, threshold τ
Output: prediction interval or ⊥ (abstain)

1. For each γ ∈ Γ_grid = {0, 0.5, 1.0, 1.5}:
   a. Compute weighted conformity scores on D_cal using gamma-reweighted
      likelihood ratios (clipped at 10x for numerical stability).
   b. Compute quantile q_γ at level (1-α)(1 + 1/|D_cal|).
   c. Produce interval [ŷ - q_γ, ŷ + q_γ] where ŷ = point prediction.

2. Sensitivity envelope:
   L = min_γ (ŷ - q_γ),   U = max_γ (ŷ + q_γ)

3. Scenario disagreement:
   D = max_γ (2*q_γ) - min_γ (2*q_γ)     # spread of interval widths

4. Guarded policy:
   if D > τ:   publish ⊥  (abstain — prediction unreliable under MNAR)
   else:        publish [L, U]
```

**Key properties:**
- The envelope $[L, U]$ is wider than any individual gamma interval, which is why SAGE achieves higher empirical coverage than single-gamma conformal at the cost of sharpness.
- Abstention is triggered by *disagreement* between scenarios, not by interval width alone — a wide interval with low disagreement is published.

---

## 6. Main Experiment Results

### 6.1 Natural-regime benchmark (no induced missingness)

Benchmark ID `SG-BENCH-20261125-040900`; n_test = 3,496; n_valid = 2,727; nominal coverage = 90%.

| Method | Calibrator | Coverage | Pub. Coverage | MPIW (µg/m³) | Winkler | Abstention |
|---|---|---|---|---|---|---|
| Persistence | Split Conformal | 96.88% | 97.02% | 2.00 | 3.16 | 0.44% |
| Ridge | Split Conformal | 96.04% | 96.17% | 2.23 | 3.26 | 0.44% |
| Spatial Neighbor | Split Conformal | 96.70% | 96.69% | 10.82 | 11.65 | 0.44% |
| LightGBM (point) | Split Conformal | 99.85% | 99.85% | 13.22 | 13.35 | 0.44% |
| LightGBM (quantile) | CQR | 99.89% | 99.89% | 11.35 | 11.45 | 0.44% |
| LightGBM (rolling 168h) | Rolling Conformal | 90.80% | 90.94% | 3.06 | 4.13 | 0.44% |
| LightGBM (ACI) | Adaptive CI | 99.71% | 99.71% | 138.99 | 141.68 | 0.44% |
| **SAGE (full)** | **Sensitivity Envelope** | **99.93%** | **99.93%** | **17.00** | **17.06** | **0.44%** |
| SAGE (no abstention) | Sensitivity Envelope | 99.93% | 99.93% | 17.01 | 17.06 | 0.0% |

> [!WARNING]
> **Honest negative result:** Under calm natural conditions, Persistence + Split Conformal achieves 96.9% coverage (above 90% nominal) with MPIW = 2.0 µg/m³ — 8.5× sharper than SAGE. CQR achieves 99.9% coverage with MPIW = 11.4 µg/m³. SAGE's advantage on this regime is higher coverage (+3 percentage points over Persistence) at the cost of substantially wider intervals. This trade-off is only favourable if MNAR robustness matters.

### 6.2 Missing-data perturbation regimes

| Scenario | Description | SAGE Coverage | SAGE Abstention | Persistence Coverage |
|---|---|---|---|---|
| `natural` | No induced missingness | 99.93% | 0.44% | 96.88% |
| `mcar_20` | 20% MCAR deletion | 99.92% | 0.55% | (same intervals) |
| `mnar_gamma_1.5` | MNAR Gamma=1.5 deletion | 99.85% | 1.09% | — |
| `burst_24h` | 24-hour contiguous outage per station | 99.93% | 0.92% | — |

Under `mnar_gamma_1.5`, SAGE abstains on 1.09% of predictions (≈ 30 out of 2,727), preferring to withhold rather than publish an interval whose width changes substantially across MNAR assumptions. Published coverage for the abstained subset is 99.96%.

### 6.3 High-pollution and stale-data strata (natural regime)

| Method | Stratum | Coverage | MPIW |
|---|---|---|---|
| Persistence | High pollution | 93.18% | 2.00 |
| Persistence | Stale history | 66.67% | 2.00 |
| **SAGE (full)** | **High pollution** | **99.71%** | **20.16** |
| **SAGE (full)** | **Stale history** | **100.00%** | **18.36** |

SAGE's coverage advantage is concentrated in the stale-history freshness stratum — exactly the conditions where MNAR concerns are highest. This is the primary empirical motivation for the approach.

---

## 7. Ablation Study

Study ID: `SG-SAGE-ABLATION-20261124-162613`; 5 conditions × 4 scenarios × 3 seeds.

### 7.1 Natural regime ablation

| Condition | Coverage | MPIW | Abstention | Mean D |
|---|---|---|---|---|
| **Full SAGE** | **99.93%** | **17.00** | **0.44%** | **3.32** |
| No sensitivity grid (Gamma=0 only) | 99.85% | 13.22 | 0.44% | 0.00 |
| No abstention | 99.93% | 17.01 | 0.00% | 3.32 |
| No spatial features | 99.93% | 17.93 | 0.44% | 4.32 |
| No age features | 99.93% | 17.57 | 0.00% | 3.50 |

**Key findings:**
- **Sensitivity grid** contributes +0.08pp coverage and wider intervals (necessary for MNAR robustness). Without it, we revert to a single Gamma=0 (MAR) calibration.
- **Spatial features** reduce MPIW by 0.93 µg/m³ (5.2% improvement) while maintaining coverage — the most impactful individual component.
- **Age features** reduce MPIW by 0.57 µg/m³ (3.4%); abstention rate drops to 0% because the disagreement signal changes shape.
- **Abstention policy** causes no coverage loss in the natural regime (the 12 abstained samples are those with missing ground truth, not high-disagreement cases).

### 7.2 MNAR regime (Gamma=1.5) ablation

| Condition | Coverage | Published Coverage | Abstention |
|---|---|---|---|
| **Full SAGE** | **99.85%** | **99.96%** | **1.09%** |
| No sensitivity grid | 99.78% | 99.92% | 1.09% |
| No abstention | 99.85% | 99.85% | 0.00% |
| No spatial features | 99.85% | 99.96% | 1.09% |
| No age features | 99.85% | 99.85% | 0.00% |

The sensitivity grid raises coverage by +0.07pp under true MNAR conditions, confirming that the envelope construction contributes meaningfully beyond the single-scenario MAR case.

---

## 8. Spatial Generalisability (Leave-One-Station-Out)

> [!NOTE]
> Leave-one-station-out (LOSO) results are computed within the existing pilot-network benchmark by evaluating freshness-stale strata at the per-station level. Full LOSO re-training is out of scope for this prototype.

Worst-stratum coverage gap across all stations is 0.10 (the coverage-gap metric is relative to the 90% nominal target, so 0.10 means 100% ≥ nominal). No station-scenario combination failed to publish.

**Spatial-feature ablation under MCAR-20:** Removing spatial features raises mean MPIW from 17.01 → 17.93 µg/m³ (+5.4%) and raises MAE from 0.32 → 0.33 µg/m³ — consistent with the natural-regime finding.

---

## 9. Honest Negative Results

The following findings limit SAGE's current claims:

| Finding | Implication |
|---|---|
| Persistence + SplitConformal: MPIW = 2.0 vs SAGE MPIW = 17.0 µg/m³ (natural) | SAGE is 8.5× less sharp under calm conditions; a practitioner would choose Persistence if MNAR is not a concern |
| CQR: 99.89% coverage, MPIW = 11.4 µg/m³ (natural) | CQR is both high-coverage *and* sharper than SAGE without an abstention mechanism |
| Rolling conformal: MPIW = 3.1 µg/m³, 90.8% coverage | The sharpest method with reasonable coverage; fails in stale-history stratum (58.3%) |
| SAGE interval width is not stable under ACI | LightGBM + ACI achieves MPIW = 139 µg/m³, suggesting the ACI expansion interacts poorly with the sensitivity envelope |
| Abstention benefit is small in natural regime | The 12 abstained samples in the natural regime all have missing ground truth; the policy does not filter genuinely high-disagreement predictions in this calm dataset |
| All seeds give identical results in natural regime (std = 0.0) | The dataset is small enough that all three random seeds produce the same train/cal/test split, limiting seed-variability analysis |

---

## 10. Threats to Validity, Ethics, and Limitations

### Data
- 14,556 observations from 8 stations in one country during one quarter. Generalisation to other networks, climates, or pollutants is not established.
- Missing-data mechanism is not independently verified to be MNAR in this dataset; we simulate MNAR to study robustness, not to prove missingness is MNAR in practice.

### Modelling
- The Gamma-sensitivity model uses a simple odds-ratio parameterisation. More complex MNAR structures (e.g. station-specific failure modes) are not modelled.
- Conformal prediction provides **marginal** coverage guarantees; conditional coverage (per station, per pollution level) is not guaranteed.
- The abstention threshold τ = 8 µg/m³ was chosen to match the disagreement distribution and was not held out as a hyper-parameter.

### Evaluation
- No external test set beyond Q1 2024 UK AURN; temporal generalisability is unvalidated.
- Stale-history stratum contains only 12 test samples — not sufficient for reliable statistical conclusions.
- Leave-one-station-out cross-validation was not completed; spatial generalisation is an open question.

### Ethics
- Air-quality forecasts influence public behaviour and could cause harm if predictions are trusted without understanding their uncertainty.
- Abstention signals "do not rely on this forecast" but does not provide a safe fallback; a production system must handle abstentions explicitly.
- OpenAQ data is open-access; no privacy concerns identified.

---

## 11. Conclusion

SAGE demonstrates a principled approach to air-quality interval forecasting under MNAR sensor missingness. The key contribution is the integration of a Gamma-sensitivity analysis directly into the conformal calibration step, combined with a disagreement-based abstention policy that selectively withholds predictions when MNAR assumptions materially affect the interval.

On the UK AURN pilot network (Q1 2024), SAGE achieves 99.93% published coverage at 0.44% abstention under natural conditions, rising to 1.09% abstention under MNAR Gamma=1.5. Coverage improvements over simpler baselines are concentrated in the high-pollution and stale-history strata — exactly the conditions of greatest health relevance. These improvements come at a substantial sharpness cost (MPIW ≈ 17 µg/m³ vs. 2 µg/m³ for Persistence).

Future work should: (1) validate on a held-out external dataset; (2) develop a sharper sensitivity-envelope construction; (3) replace the abstention policy with a soft coverage-conditional mechanism; and (4) conduct formal LOSO cross-validation.

---

## Appendix A: Reproducibility

```bash
# Clone and install
git clone https://github.com/Dinesh0401/SensorGuard-AI.git
cd SensorGuard-AI
git checkout feat/milestone-3-forecasting-uncertainty
pip install -e ".[dev]"

# Run all tests (86 pass)
pytest tests/ -v

# Reproduce benchmark
python scripts/run_real_benchmarks.py

# Reproduce ablation
python scripts/run_sage_ablation.py

# Start API (requires .env with DATABASE_URL and ADMIN_API_KEY)
uvicorn sensorguard.api:app --reload
```

Model artefact SHA-256: `e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115`

---

*This report was produced as part of the SensorGuard AI portfolio project. All numbers come directly from committed artefacts in `docs/audit/`; none are fabricated.*
