# SensorGuard AI — Novelty Audit

**Version:** 1.0  
**Date:** 2026-11-25  
**Status:** Research prototype — originality NOT independently peer-reviewed

---

## Purpose

This document records a structured audit of related prior work to assess the originality of the SAGE (Sensitivity-Aware Guarded Estimation) approach and to identify where SAGE's contribution is genuinely novel versus where it recombines existing ideas.

> [!WARNING]
> This is a self-audit. It does not constitute peer review. Claims should be independently verified before any research submission.

---

## 1. Prior Work Survey

### 1.1 Conformal prediction foundations

| Reference | Key Contribution | Relation to SAGE |
|---|---|---|
| Venn & Shafer (2005), *A Tutorial on Conformal Prediction* | Finite-sample marginal coverage guarantee | SAGE builds on split conformal calibration |
| Romano, Patterson & Candès (2019), *Conformalized Quantile Regression* | CQR extends conformal to quantile regression models | SAGE implements CQR as the `no_sensitivity_grid` baseline |
| Gibbs & Candès (2021), *Adaptive Conformal Inference Under Distribution Shift* | ACI adapts coverage online | SAGE implements ACI as a baseline; ACI does not address MNAR |
| Zaffran et al. (2022/2023), *Conformal Prediction with Missing Values* (ICML 2023) | Conformal prediction when features are missing | Addresses missing **features** (imputation uncertainty); SAGE addresses missing **labels** (MNAR calibration bias) |

### 1.2 Sensitivity analysis and partial identification

| Reference | Key Contribution | Relation to SAGE |
|---|---|---|
| Rosenbaum (2002), *Observational Studies* | Gamma-sensitivity model for odds-ratio bounds | SAGE's MNAR model directly uses this parameterisation |
| Manski (2003), *Partial Identification of Probability Distributions* | Bounds without full identification | SAGE takes a similar bounding approach via the sensitivity envelope |
| Cornfield et al. (1959) | Original sensitivity analysis for confounding | Historical basis for Gamma model |

### 1.3 Air-quality and sensor-network forecasting

| Reference | Relation to SAGE |
|---|---|
| *Citywide Air Quality Forecasting over Sparse Sensor Networks* (MDPI 2026) | Spatial imputation; does not address conformal calibration or MNAR |
| *OmniST: Topology-Invariant Spatiotemporal Forecasting* (arXiv 2026) | Deep learning for sparse sensors; no conformal or MNAR handling |
| *SA-IDS: Informative Sensor Silence* (SSRN 2026) | Addresses informative silence in industrial sensors; different domain and method |
| *Adaptive Conformal Prediction for Time Series with Missing Values* (TMLR 2026) | Closest related work — must compare directly before any publication |
| *Evolving trends in low-cost air quality sensor networks* (Nature 2025) | Survey; motivates the problem setting |

---

## 2. What SAGE Does (Claimed Contribution)

SAGE integrates:
1. **A Gamma-sensitivity grid** over the MNAR calibration step (not just over inference)
2. **A scenario-disagreement signal** as the spread of calibrated interval widths
3. **A guarded abstention policy** triggered by disagreement (not by interval width alone)

This combination — MNAR-sensitivity analysis embedded in conformal calibration with abstention — has not been found in a directly equivalent published form in the literature surveyed above.

---

## 3. What SAGE Does NOT Claim

| Non-claim | Reason |
|---|---|
| Distribution-free MNAR coverage | MNAR is non-identifiable without assumptions; we work within a specified Gamma model |
| Superiority over CQR on sharpness | CQR achieves 99.9% coverage with MPIW = 11.4 µg/m³ vs. SAGE's 17.0 µg/m³ |
| Generalisation beyond UK AURN Q1 2024 | Not tested on held-out stations or time periods |
| Superiority over ACP-TSM baseline | This baseline (Adaptive Conformal with Missing Values, TMLR 2026) has not been fully implemented and compared |
| Peer-reviewed novelty | This is a research prototype, not a published result |

---

## 4. Gap Identification

The most important unresolved comparison:

> **Zaffran et al. ICML 2023** addresses conformal prediction with missing **features** (covariates). SAGE addresses missing **labels** (ground truth at calibration time). These are different problems and the methods are not directly comparable, but this distinction must be clearly stated in any submission.

> **TMLR 2026 (Adaptive Conformal for Time Series with Missing Values)**: this paper may address a very similar problem to SAGE. Full bibliographic details and a direct empirical comparison are needed before any publication-level novelty claim.

---

## 5. Recommendation

SAGE is a credible research prototype with a coherent engineering design and an honest evaluation. It should **not** be described as a peer-reviewed publication or as proven to be novel without:
1. A complete literature search with keyword coverage
2. A direct empirical comparison to Zaffran et al. and the TMLR 2026 paper
3. External validation on at least one additional dataset/region

For portfolio and educational purposes, SAGE is well-evidenced. For research submission, the novelty claim requires further validation.
