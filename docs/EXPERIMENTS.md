# SensorGuard AI — Experiment Registry

**Benchmark ID:** `SG-BENCH-20261125-040900`  
**Ablation ID:** `SG-SAGE-ABLATION-20261124-162613`  
**Dataset content hash:** `33805c42097fc86e`  
**Dataset window:** `2024-01-01T01:00:00Z` to `2024-03-31T23:00:00Z` (Q1 2024, 8 UK AURN stations)  
**Nominal coverage:** 90% ($\alpha = 0.10$)

---

## Registered Experiments

| ID | Type | Seeds | Scenarios | Output Artifact |
|---|---|---|---|---|
| `SG-BENCH-20261125-040900` | Multi-method benchmark + time blocks + 8-fold LOSO CV | `42` | `natural`, `mcar_20`, `mnar_gamma_1.5`, `burst_24h` | [benchmark_results.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/benchmark_results.json) |
| `SG-SAGE-ABLATION-20261124-162613` | 5-condition SAGE ablation study | `[11, 42, 107]` | `natural`, `mcar_20`, `mnar_gamma_1.5`, `burst_24h` | [sage_ablation_results.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/sage_ablation_results.json) |

---

## Experiment Configuration (Matching Code)

```json
{
  "nominal_coverage": 0.90,
  "max_disagreement_threshold": 8.0,
  "max_envelope_width_threshold": 50.0,
  "stale_age_hours": 12.0,
  "caution_age_hours": 3.0,
  "min_effective_sample_size": 15,
  "gamma_grid": [0.0, 0.5, 1.0, 1.5, 2.0],
  "train_fraction": 0.60,
  "calibration_fraction": 0.20,
  "test_fraction": 0.20
}
```

### Outage Scenarios (`prepare_scenario_test_data`)

| Scenario | Mechanism | Seed(s) | Notes |
|---|---|---|---|
| `natural` | Observed gaps only (~22.0% test missingness) | Fixed | Evaluated strictly on `2,727` valid observed targets |
| `mcar_20` | 20% independent random masking on `3,496` test rows | `42` (bench) / `[11, 42, 107]` (abl) | Causal lags/IDW/ages rebuilt from visible history |
| `mnar_gamma_1.5` | $\text{logit}\,P(\text{outage}) = 1.5 \cdot z_{\text{true}} - 1.2$ | `42` (bench) / `[11, 42, 107]` (abl) | Evaluator ground truth isolated |
| `burst_24h` | Contiguous 24h station outage | `42` (bench) / `[11, 42, 107]` (abl) | Tests freshness `age_hours > 12.0` abstention |

---

## Verified Key Results Summary

### Benchmark (`SG-BENCH-20261125-040900`, $N_{\text{valid}} = 2,727$)

| Regime & Method | Overall PICP | Published PICP | Overall MPIW | Winkler | Abstention |
|---|---:|---:|---:|---:|---:|
| `natural` — Persistence + Split | 96.88% | 97.02% | 2.00 | 3.16 | 0.44% (`12`) |
| `natural` — LightGBM + CQR | 99.89% | 99.89% | 11.35 | 11.45 | 0.44% (`12`) |
| `natural` — SAGE Full | **99.93%** | **99.93%** | 17.01 | 17.06 | 0.44% (`12`) |
| `mnar_gamma_1.5` — LightGBM + CQR | 99.78% | 99.93% | 11.42 | 11.70 | 0.95% (`26`) |
| `mnar_gamma_1.5` — SAGE Full | **99.85%** | **99.96%** | 16.98 | 17.10 | 0.95% (`26`) |

*Abstention note:* Because $D_{\text{sensitivity}} = 3.32 < 8.0$ on all rows in the pilot model, abstentions above (`12` natural, `16` MCAR, `26` MNAR seed=42, `25` burst) equal the `age_hours > 12.0` stale-history subset in both CQR and SAGE. Across 3 seeds `[11, 42, 107]` in `sage_ablation_results.json`, MNAR `full_sage` abstention averages `1.09% ± 0.17%`.

---

## Reproducibility Commands (Verified)

```bash
# 1. Reproduce full benchmark, time blocks, and 8-fold LOSO CV
python scripts/run_real_benchmarks.py

# 2. Reproduce 3-seed SAGE ablation study
python scripts/run_sage_ablations.py

# 3. Retrain offline pilot SAGE serving model
python scripts/train_pilot_model.py

# 4. Run concurrent network-socket latency benchmark
python scripts/benchmark_network_concurrency.py
```

*Note:* Both `scripts/run_sage_ablations.py` (canonical) and wrapper `scripts/run_sage_ablation.py` run the ablation suite, but all docs standardize on `scripts/run_sage_ablations.py`.

---

## Pre-Registration & Stop-Rule Assessment

- **Null Hypothesis ($H_0$):** SAGE does not improve worst-stratum interval reliability over strong baselines at matched width and abstention.
- **Outcome:** **Not rejected vs. LightGBM + CQR at matched width.** Under `mnar_gamma_1.5`, CQR already achieves `worst_stratum_coverage_gap = 10.0%` (`99.78%` PICP) with `33%` narrower intervals (`11.42` vs `16.98`) and better Winkler score (`11.70` vs `17.10`). Under 8-fold LOSO CV, CQR beats SAGE on Winkler score across all 8 stations, while SAGE preserves ≥ 90% coverage on the difficult `station_4` fold (`90.72%` vs `81.72%`). Under `natural`, stale-history observations ($n=12$) were `100%` abstained (`0` published) by both baselines and SAGE, so stale-history counterfactual containment (`100%` vs `66.7%`) does **not** establish delivered forecast superiority.
