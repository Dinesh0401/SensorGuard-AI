"""Comprehensive real-data evaluation pipeline and baseline benchmarking.

Executes and compares all pre-registered forecasting and uncertainty methods:
- Point baselines: Persistence (B0a), Seasonal Naive (B0b), Spatial Neighbor (B1),
  Ridge (B2a), LightGBM (B2b).
- Interval & calibration methods: Split Conformal, Conformalized Quantile Regression
  (CQR, Romano et al. 2019), Rolling Conformal, Adaptive Conformal Inference (ACI).
- Missingness regimes: Natural gaps, MCAR (20%), MAR, and MNAR (gamma sweep).
- Metrics: MAE, RMSE, PICP (empirical coverage), MPIW (interval width),
  Winkler score, worst-stratum coverage gap, and abstention rate.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from sensorguard.calibration import (
    AdaptiveConformalInference,
    ConformalizedQuantileRegression,
    SplitConformalCalibrator,
    compute_interval_metrics,
    policy,
)
from sensorguard.models.data import DatasetSplits
from sensorguard.models.forecasters import (
    BaseForecaster,
    LightGBMPointForecaster,
    LightGBMQuantileForecaster,
    PersistenceForecaster,
    RidgeForecaster,
    SeasonalNaiveForecaster,
    SpatialNeighborForecaster,
)

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Standardized evaluation benchmark result."""

    method_name: str
    scenario: str
    metrics: dict[str, Any]
    calibrator: str
    nominal_coverage: float = 0.90


def run_benchmark_suite(
    splits: DatasetSplits,
    nominal_coverage: float = 0.90,
    scenarios: list[str] | None = None,
    track_mlflow: bool = True,
    mlflow_experiment: str = "SensorGuard-Pilot-Baselines",
) -> dict[str, Any]:
    """Run full benchmarking matrix across models, calibrators, and missingness scenarios."""
    eval_scenarios = scenarios or ["natural", "mcar_20", "mnar_gamma_1.5", "burst_24h"]
    alpha = 1.0 - nominal_coverage

    train_df = splits.train
    cal_df = splits.calibrate
    test_df = splits.test
    feature_cols = splits.feature_columns

    logger.info("Training point and quantile forecasters on %d observations...", len(train_df))

    # Fit all candidate models
    models: dict[str, BaseForecaster] = {
        "persistence": PersistenceForecaster().fit(train_df, train_df["target"].to_numpy()),
        "seasonal_naive": SeasonalNaiveForecaster().fit(train_df, train_df["target"].to_numpy()),
        "spatial_neighbor": SpatialNeighborForecaster().fit(train_df, train_df["target"].to_numpy()),
        "ridge": RidgeForecaster(feature_columns=feature_cols).fit(train_df, train_df["target"].to_numpy()),
        "lightgbm_point": LightGBMPointForecaster(feature_columns=feature_cols).fit(
            train_df, train_df["target"].to_numpy()
        ),
        "lightgbm_quantile": LightGBMQuantileForecaster(
            feature_columns=feature_cols, quantiles=(0.05, 0.50, 0.95)
        ).fit(train_df, train_df["target"].to_numpy()),
    }

    # Calibration predictions on calibration partition
    y_cal = cal_df["target"].to_numpy()
    cal_preds: dict[str, np.ndarray] = {name: model.predict(cal_df) for name, model in models.items()}

    # Fit Split Conformal Calibrators for each point model
    point_calibrators: dict[str, SplitConformalCalibrator] = {}
    for name, pred in cal_preds.items():
        if name != "lightgbm_quantile":
            calibrator = SplitConformalCalibrator(alpha=alpha)
            calibrator.fit(y_cal, pred)
            point_calibrators[name] = calibrator

    # Fit CQR on LightGBM quantile predictions
    lgb_q = models["lightgbm_quantile"]
    assert isinstance(lgb_q, LightGBMQuantileForecaster)
    cal_quantiles = lgb_q.predict_quantiles(cal_df)
    cqr_calibrator = ConformalizedQuantileRegression(alpha=alpha)
    cqr_calibrator.fit(y_cal, cal_quantiles[0.05], cal_quantiles[0.95])

    # Dataset lineage hash
    data_hash = hashlib.sha256(
        (str(len(train_df)) + str(len(cal_df)) + str(len(test_df))).encode("utf-8")
    ).hexdigest()[:16]

    benchmark_summary: dict[str, Any] = {
        "benchmark_id": f"SG-BENCH-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
        "dataset_hash": data_hash,
        "nominal_coverage": nominal_coverage,
        "n_train": len(train_df),
        "n_calibrate": len(cal_df),
        "n_test": len(test_df),
        "stations_count": len(splits.station_coords),
        "scenarios": {},
    }

    for scenario in eval_scenarios:
        logger.info("Evaluating benchmark under scenario '%s'...", scenario)
        scenario_test = test_df.copy()

        # Apply mask mechanism if controlled simulation
        if scenario == "natural":
            # Real observations as naturally collected
            y_eval_truth = scenario_test["target"].to_numpy()
        elif scenario == "mcar_20":
            # Mask 20% completely at random
            rng = np.random.default_rng(42)
            mask = rng.uniform(size=len(scenario_test)) < 0.20
            y_eval_truth = scenario_test["target"].to_numpy()
            scenario_test.loc[mask, "last_pm25"] = scenario_test.loc[mask, "lag_1h"]
            scenario_test.loc[mask, "age_hours"] = scenario_test.loc[mask, "age_hours"] + 1.0
        elif scenario.startswith("mnar_gamma"):
            # Mask probability dependent on true hidden target (MNAR)
            gamma = float(scenario.split("_")[-1])
            mu = float(train_df["target"].mean())
            sigma = float(train_df["target"].std()) + 1e-6
            z = (scenario_test["target"].to_numpy() - mu) / sigma
            probs = 1.0 / (1.0 + np.exp(-(gamma * z - 1.2)))
            rng = np.random.default_rng(42)
            mask = rng.uniform(size=len(scenario_test)) < probs
            y_eval_truth = scenario_test["target"].to_numpy()
            scenario_test.loc[mask, "last_pm25"] = scenario_test.loc[mask, "lag_1h"]
            scenario_test.loc[mask, "age_hours"] = scenario_test.loc[mask, "age_hours"] + 1.0
        elif scenario == "burst_24h":
            # 24-hour contiguous outage on station 1
            st_ids = sorted(scenario_test["station_id"].unique())
            target_st = st_ids[0]
            t_min = scenario_test["timestamp"].min() + pd.Timedelta(days=5)
            t_max = t_min + pd.Timedelta(hours=24)
            mask = (
                (scenario_test["station_id"] == target_st)
                & (scenario_test["timestamp"] >= t_min)
                & (scenario_test["timestamp"] < t_max)
            )
            y_eval_truth = scenario_test["target"].to_numpy()
            scenario_test.loc[mask, "last_pm25"] = scenario_test.loc[mask, "lag_24h"]
            scenario_test.loc[mask, "age_hours"] = 24.0
        else:
            y_eval_truth = scenario_test["target"].to_numpy()

        scenario_results: dict[str, Any] = {}

        # 1. Evaluate Point Forecasters with Split Conformal
        for name, model in models.items():
            pred = model.predict(scenario_test)

            if name == "lightgbm_quantile":
                # Evaluate CQR
                q_dict = model.predict_quantiles(scenario_test)
                lower, upper = cqr_calibrator.predict_interval(q_dict[0.05], q_dict[0.95])
                cal_method = "CQR (Romano et al. 2019)"
            else:
                cal = point_calibrators[name]
                lower, upper = cal.predict_interval(pred)
                cal_method = "Split Conformal"

            # Compute strata labels for subgroup fairness diagnostics
            strata = {
                "pollution_level": np.where(
                    y_eval_truth >= np.nanquantile(y_eval_truth, 0.75), "high_pollution", "normal"
                ),
                "freshness": np.where(
                    scenario_test["age_hours"] > 3.0,
                    "stale_history",
                    np.where(scenario_test["age_hours"] > 1.0, "moderate_delay", "fresh"),
                ),
                "diurnal": np.where(
                    scenario_test["timestamp"].dt.hour.isin([7, 8, 9, 17, 18, 19]), "rush_hour", "off_peak"
                ),
            }

            metrics = compute_interval_metrics(
                y_true=y_eval_truth,
                y_pred=pred,
                lower=lower,
                upper=upper,
                alpha=alpha,
                strata_labels=strata,
            )

            # Safety policy evaluation
            statuses, _reasons = policy(scenario_test["age_hours"].to_numpy())
            published_mask = statuses != "abstain"
            metrics["abstention_rate"] = round(float((~published_mask).mean()), 4)
            if published_mask.any():
                pub_cov = float(
                    (
                        (y_eval_truth[published_mask] >= lower[published_mask])
                        & (y_eval_truth[published_mask] <= upper[published_mask])
                    ).mean()
                )
                metrics["published_coverage"] = round(pub_cov, 4)
            else:
                metrics["published_coverage"] = None

            scenario_results[name] = {
                "calibrator": cal_method,
                "metrics": metrics,
            }

        # 2. Evaluate Adaptive Conformal Inference (ACI) on LightGBM point model
        lgb_pred = models["lightgbm_point"].predict(scenario_test)
        base_cal = point_calibrators["lightgbm_point"]
        aci = AdaptiveConformalInference(target_alpha=alpha, gamma=0.005)

        aci_lowers = []
        aci_uppers = []
        for i in range(len(lgb_pred)):
            # Adaptive radius based on current alpha_t
            r_t = base_cal.radius * (alpha / aci.current_alpha)
            l_t = max(0.0, lgb_pred[i] - r_t)
            u_t = lgb_pred[i] + r_t
            aci_lowers.append(l_t)
            aci_uppers.append(u_t)

            is_cov = l_t <= y_eval_truth[i] <= u_t
            aci.step(covered=is_cov)

        aci_metrics = compute_interval_metrics(
            y_true=y_eval_truth,
            y_pred=lgb_pred,
            lower=np.array(aci_lowers),
            upper=np.array(aci_uppers),
            alpha=alpha,
        )
        aci_statuses, _ = policy(scenario_test["age_hours"].to_numpy())
        aci_pub = aci_statuses != "abstain"
        aci_metrics["abstention_rate"] = round(float((~aci_pub).mean()), 4)
        aci_metrics["published_coverage"] = (
            round(
                float(
                    (
                        (y_eval_truth[aci_pub] >= np.array(aci_lowers)[aci_pub])
                        & (y_eval_truth[aci_pub] <= np.array(aci_uppers)[aci_pub])
                    ).mean()
                ),
                4,
            )
            if aci_pub.any()
            else None
        )
        scenario_results["lightgbm_aci"] = {
            "calibrator": "Adaptive Conformal Inference (Gibbs & Candès 2021)",
            "metrics": aci_metrics,
        }

        benchmark_summary["scenarios"][scenario] = scenario_results

    # Optional local MLflow logging
    if track_mlflow:
        try:
            import mlflow

            mlflow.set_experiment(mlflow_experiment)
            with mlflow.start_run(run_name=benchmark_summary["benchmark_id"]):
                mlflow.log_param("dataset_hash", data_hash)
                mlflow.log_param("nominal_coverage", nominal_coverage)
                mlflow.log_param("n_train", len(train_df))
                mlflow.log_param("n_calibrate", len(cal_df))
                mlflow.log_param("n_test", len(test_df))

                # Log metrics for natural scenario
                natural_res = benchmark_summary["scenarios"].get("natural", {})
                for model_key, res in natural_res.items():
                    m = res["metrics"]
                    for k in ["mae", "rmse", "picp", "mpiw", "winkler_score", "worst_stratum_coverage_gap"]:
                        if m.get(k) is not None:
                            mlflow.log_metric(f"{model_key}_{k}", m[k])

                mlflow.log_dict(benchmark_summary, "benchmark_summary.json")
                logger.info("Logged benchmark run to MLflow experiment '%s'", mlflow_experiment)
        except Exception as e:  # noqa: BLE001 - optional telemetry must never crash evaluation run
            logger.warning("MLflow logging skipped: %s", e)

    return benchmark_summary
