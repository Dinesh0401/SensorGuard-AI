# SensorGuard AI — Experiment Registry

**Benchmark ID:** `SG-BENCH-20261125-040900`  
**Ablation ID:** `SG-SAGE-ABLATION-20261124-162613`  
**Dataset hash:** `33805c42097fc86e`  
**Nominal coverage:** 90% (α = 0.10)

---

## Registered Experiments

| ID | Type | Date | Seeds | Scenarios | Status |
|---|---|---|---|---|---|
| `SG-BENCH-20261125-040900` | Multi-method benchmark | 2026-11-25 | Fixed | natural, mcar_20, mnar_gamma_1.5, burst_24h | ✅ Complete |
| `SG-SAGE-ABLATION-20261124-162613` | SAGE ablation study | 2026-11-24 | 11, 42, 107 | natural, mcar_20, mnar_gamma_1.5, burst_24h | ✅ Complete |

---

## Experiment Configuration

### Common settings

```json
{
  "nominal_coverage": 0.9,
  "max_disagreement_threshold": 8.0,
  "max_envelope_width_threshold": 50.0,
  "gamma_grid": [0.0, 0.5, 1.0, 1.5],
  "train_fraction": 0.6,
  "calibration_fraction": 0.2,
  "test_fraction": 0.2
}
```

### Outage scenarios

| Scenario | Description | Miss Rate |
|---|---|---|
| `natural` | No induced missingness | ~22% (natural) |
| `mcar_20` | 20% MCAR deletion | +20% |
| `mnar_gamma_1.5` | MNAR Gamma=1.5 deletion | Variable |
| `burst_24h` | 24-hour contiguous station outage | Variable |

### Ablation conditions

| Condition | Description |
|---|---|
| `full_sage` | Full SAGE: sensitivity grid + abstention + spatial + age features |
| `no_sensitivity_grid` | Gamma=0 (MAR) only; no MNAR calibration |
| `no_abstention` | Sensitivity grid active; guarded policy disabled |
| `no_spatial_features` | No neighbour station features |
| `no_age_features` | No `hours_since_last_obs` or freshness features |

---

## Key Results Summary

### Natural-regime coverage (n_valid = 2,727)

| Method | Coverage | MPIW (µg/m³) | Abstention |
|---|---|---|---|
| Persistence + SplitConformal | 96.88% | 2.00 | 0.44% |
| LightGBM + CQR | 99.89% | 11.35 | 0.44% |
| SAGE (full) | **99.93%** | 17.00 | **0.44%** |

### MNAR Gamma=1.5 (full SAGE)

| Metric | Value |
|---|---|
| Coverage | 99.85% |
| Published coverage | 99.96% |
| Abstention rate | 1.09% |

### Ablation (natural, full vs. no_sensitivity_grid)

| Condition | Coverage | MPIW | Δ Coverage |
|---|---|---|---|
| Full SAGE | 99.93% | 17.00 | — |
| No sensitivity grid | 99.85% | 13.22 | -0.08pp |

---

## Artefact Locations

| Artefact | Path |
|---|---|
| Benchmark JSON | `docs/audit/benchmark_results.json` |
| Ablation JSON | `docs/audit/sage_ablation_results.json` |
| Network audit JSON | `docs/audit/pilot_network_audit.json` |
| Trained model | `artifacts/models/sage_pilot_q1_2024.joblib` |
| Model manifest | `artifacts/models/manifest.json` |
| Model SHA-256 | `artifacts/models/sage_pilot_q1_2024.sha256` |

---

## Reproducibility

```bash
# Reproduce benchmark
python scripts/run_real_benchmarks.py

# Reproduce ablation
python scripts/run_sage_ablation.py

# Retrain model (use only when dataset changes)
python scripts/train_pilot_model.py
```

All experiments are deterministic with the same dataset and configuration. See `docs/RESEARCH_REPORT.md` Appendix A for full reproducibility instructions.

---

## Pre-registration Statement

The research hypothesis (H0: SAGE does not improve interval reliability under matched width/abstention vs. strongest baseline) was established before running the final evaluation. The abstention threshold τ = 8 µg/m³ was set based on the disagreement distribution in the calibration set, not post-hoc on test results. The stop rule and hurdle (5pp improvement in worst-stratum coverage gap under MNAR without disproportionate width inflation) was established in `docs/RESEARCH_PROTOCOL.md`.

**Result against H0:** SAGE improves stale-history stratum coverage from 66.7% → 100% under natural conditions, and maintains 99.9%+ coverage under MNAR at 1.1% abstention, meeting the pre-registered research hurdle. CQR remains the sharper alternative for calm conditions.
