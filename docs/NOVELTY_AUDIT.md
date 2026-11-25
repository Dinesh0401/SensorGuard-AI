# SensorGuard AI — Novelty Audit

**Version:** 1.1  
**Date:** 2026-11-25  
**Status:** Internal research prototype audit — originality **not** peer-reviewed or established

---

## 1. Prior Work Comparison

| Reference | Contribution | Relation & Boundary vs. SensorGuard |
|---|---|---|
| Vovk, Gammerman & Shafer (2005), *Algorithmic Learning in a Random World* | Finite-sample marginal conformal intervals | Standard exchangeability foundation |
| Romano, Patterson & Candès (2019), *Conformalized Quantile Regression (CQR)* | Quantile regression + conformal residual adjustment | Implemented baseline (`lightgbm_quantile`); sharper than SAGE on Q1 2024 calm & natural strata and wins Winkler score on all 8 LOSO folds |
| Gibbs & Candès (2021), *Adaptive Conformal Inference (ACI)* | Online $\alpha_t$ step updates under shift | Implemented baseline (`lightgbm_aci`); exhibits severe width explosion (`MPIW = 141.46`) on this dataset |
| Zaffran et al. (2023, ICML), *Conformal Prediction with Missing Values* | Missing covariates $X$ during prediction | Targets missing features rather than MNAR calibration labels; distinct assumption set |
| Rosenbaum (2002), *Observational Studies* | Pairwise selection odds-ratio bound $\Gamma \ge 1$ | **Important distinction:** SAGE uses a parametric logit slope `g(X) + gamma*(Y - mu)/sigma` plus heuristic residual-std shifts `gamma * sigma_res / 2`, which is *not* mathematically equivalent to Rosenbaum's non-parametric $\Gamma$ sensitivity theorem |
| Manski (2003), *Partial Identification of Probability Distributions* | Assumption-explicit interval bounds | Conceptual motivation; SAGE's bounds are heuristic sensitivity envelopes rather than sharp identification regions |
| *Adaptive Conformal Prediction for Time Series with Missing Values* (TMLR 2026) | Closest time-series missingness conformal work | Not yet implemented and compared — open pre-publication blocker |

---

## 2. Verified Contribution Scope (What Is Demonstrated vs. Not Demonstrated)

### Demonstrated
- End-to-end leak-free evaluation harness preserving evaluator-only ground truth while causally reconstructing lags, rolling statistics, spatial IDW neighbors, and sensor age.
- Transparent, reproducible baseline suite fixing earlier evaluation-denominator bugs and request-path retraining defects.
- Empirically separating upper-bound sensitivity spread ($D_{\text{sensitivity}}$) from total envelope width ($W_{\text{envelope}}$) so freshness gating does not collapse into 100% abstention.

### Not Demonstrated / Explicitly Disclaimed
- **No formal finite-sample MNAR coverage theorem** under general missingness or Rosenbaum odds-ratio bounds.
- **No matched-width superiority over CQR or Persistence:** Under calm Q1 2024 conditions and MNAR $\gamma = 1.5$ tests (`SG-BENCH-20261125-040900`), `LightGBM + CQR` achieves `99.78%`–`99.89%` coverage with ~33% narrower intervals and better Winkler scores.
- **No delivered stale-history gain proven:** The $n=12$ natural (`n=26` MNAR) `age > 12h` subgroup was `100%` abstained (`0` published) under both SAGE and the shared baseline freshness policy.
- **Local disagreement invariance limitation:** Current `D_sensitivity` is globally constant (`3.32 µg/m³ < 8.0`) because sensitivity shifts depend only on calibration variance rather than input covariates $X$.

---

## 3. Conclusion

SensorGuard AI is well-founded as an engineering research and portfolio platform that enforces strict no-leakage hygiene and honest negative reporting, but it must not claim formal theoretical novelty or competitive superiority over CQR until TMLR 2026 comparisons, localized disagreement scaling, and multi-region out-of-sample tests are completed.
