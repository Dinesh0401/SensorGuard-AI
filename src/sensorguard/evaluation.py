"""Comprehensive real-data evaluation pipeline and baseline benchmarking.

Executes and compares all pre-registered forecasting and uncertainty methods:
- Point baselines: Persistence (B0a), Seasonal Naive (B0b), Spatial Neighbor (B1),
  Ridge (B2a), LightGBM (B2b).
- Interval & calibration methods: Split Conformal, Conformalized Quantile Regression
  (CQR, Romano et al. 2019), Rolling Conformal, Adaptive Conformal Inference (ACI).
- SAGE methods: Full SAGE (sensitivity envelope + guarded policy), SAGE without
  abstention, SAGE with gamma=0 (MAR assumption).
- Missingness regimes: Natural gaps, MCAR (20%), MAR, and MNAR (gamma sweep).
- Metrics: MAE, RMSE, PICP (empirical coverage), MPIW (interval width),
  Winkler score, worst-stratum coverage gap, and abstention rate.
- Stratified and stress evaluations: time-block temporal evaluation and
  leave-one-station-out cross-validation.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from sensorguard.ablation import prepare_scenario_test_data
from sensorguard.calibration import (
    AdaptiveConformalInference,
    ConformalizedQuantileRegression,
    RollingConformalCalibrator,
    SplitConformalCalibrator,
    compute_interval_metrics,
    policy,
)
from sensorguard.models.data import DatasetSplits, partition_dataset
from sensorguard.models.forecasters import (
    BaseForecaster,
    LightGBMPointForecaster,
    LightGBMQuantileForecaster,
    PersistenceForecaster,
    RidgeForecaster,
    SeasonalNaiveForecaster,
    SpatialNeighborForecaster,
)
from sensorguard.models.sage import SAGEConfig, SAGEForecaster

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
    """Run full benchmarking matrix across models, calibrators, and missingness scenarios.

    Uses leak-free scenario preparation and unified evaluation denominators.
    """
    eval_scenarios = scenarios or ["natural", "mcar_20", "mnar_gamma_1.5", "burst_24h"]
    alpha = 1.0 - nominal_coverage

    train_df = splits.train
    cal_df = splits.calibrate
    test_df = splits.test
    feature_cols = splits.feature_columns

    logger.info("Training point, quantile, and SAGE forecasters on %d observations...", len(train_df))

    # Fit candidate baseline forecasters
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

    # Fit Split Conformal Calibrators for point models
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

    # Fit SAGE models
    sage_cfg = SAGEConfig(
        gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0),
        nominal_coverage=nominal_coverage,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
    )
    sage_full = SAGEForecaster(config=sage_cfg, feature_columns=feature_cols)
    sage_full.fit(X=train_df, y=train_df["target"].to_numpy(), X_cal=cal_df, y_cal=y_cal)

    sage_g0_cfg = SAGEConfig(
        gamma_grid=(0.0,),
        nominal_coverage=nominal_coverage,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
    )
    sage_g0 = SAGEForecaster(config=sage_g0_cfg, feature_columns=feature_cols)
    sage_g0.fit(X=train_df, y=train_df["target"].to_numpy(), X_cal=cal_df, y_cal=y_cal)

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

    train_mean = float(train_df["target"].mean())
    train_std = float(train_df["target"].std())
    history_df = pd.concat([train_df, cal_df], ignore_index=True)

    for scenario in eval_scenarios:
        logger.info("Evaluating benchmark under scenario '%s'...", scenario)
        scenario_test, y_eval_truth = prepare_scenario_test_data(
            test_df=test_df,
            scenario=scenario,
            seed=42,
            train_mean=train_mean,
            train_std=train_std,
            station_coords=splits.station_coords,
            history_df=history_df,
        )

        scenario_results: dict[str, Any] = {}

        # Subgroup strata for fairness/conditional coverage audits
        strata = {
            "pollution_level": np.where(
                y_eval_truth >= np.nanquantile(y_eval_truth, 0.75), "high_pollution", "normal"
            ),
            "freshness": np.where(
                scenario_test["age_hours"] > 12.0,
                "stale_history",
                np.where(scenario_test["age_hours"] > 3.0, "moderate_delay", "fresh"),
            ),
            "diurnal": np.where(
                scenario_test["timestamp"].dt.hour.isin([7, 8, 9, 17, 18, 19]), "rush_hour", "off_peak"
            ),
        }

        # Baseline safety policy based on observation age
        base_statuses, _ = policy(scenario_test["age_hours"].to_numpy())
        base_published = base_statuses != "abstain"

        # 1. Point forecasters + Split Conformal & CQR
        for name, model in models.items():
            pred = model.predict(scenario_test)

            if name == "lightgbm_quantile":
                q_dict = model.predict_quantiles(scenario_test)
                lower, upper = cqr_calibrator.predict_interval(q_dict[0.05], q_dict[0.95])
                cal_method = "CQR (Romano et al. 2019)"
            else:
                cal = point_calibrators[name]
                lower, upper = cal.predict_interval(pred)
                cal_method = "Split Conformal"

            metrics = compute_interval_metrics(
                y_true=y_eval_truth,
                y_pred=pred,
                lower=lower,
                upper=upper,
                alpha=alpha,
                is_published=base_published,
                strata_labels=strata,
            )

            scenario_results[name] = {
                "calibrator": cal_method,
                "metrics": metrics,
            }

        # 2. Rolling Conformal on LightGBM point model
        rolling_cal = RollingConformalCalibrator(window_size=168, alpha=alpha)
        # Pre-populate rolling window with calibration residuals
        for yt, yp in zip(y_cal, cal_preds["lightgbm_point"]):
            rolling_cal.update(yt, yp)

        lgb_pred = models["lightgbm_point"].predict(scenario_test)
        rolling_lowers = []
        rolling_uppers = []
        for i in range(len(lgb_pred)):
            rad = rolling_cal.current_radius()
            if not np.isfinite(rad):
                rad = point_calibrators["lightgbm_point"].radius
            rolling_lowers.append(max(0.0, lgb_pred[i] - rad))
            rolling_uppers.append(lgb_pred[i] + rad)
            rolling_cal.update(y_eval_truth[i], lgb_pred[i])

        rolling_metrics = compute_interval_metrics(
            y_true=y_eval_truth,
            y_pred=lgb_pred,
            lower=np.array(rolling_lowers),
            upper=np.array(rolling_uppers),
            alpha=alpha,
            is_published=base_published,
            strata_labels=strata,
        )
        scenario_results["lightgbm_rolling"] = {
            "calibrator": "Rolling Conformal (168h trailing)",
            "metrics": rolling_metrics,
        }

        # 3. Adaptive Conformal Inference (ACI) on LightGBM point model
        base_cal = point_calibrators["lightgbm_point"]
        aci = AdaptiveConformalInference(target_alpha=alpha, gamma=0.005)
        aci_lowers = []
        aci_uppers = []
        for i in range(len(lgb_pred)):
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
            is_published=base_published,
            strata_labels=strata,
        )
        scenario_results["lightgbm_aci"] = {
            "calibrator": "Adaptive Conformal Inference (Gibbs & Candès 2021)",
            "metrics": aci_metrics,
        }

        # 4. SAGE Candidate Framework (Full SAGE with Sensitivity Envelope)
        res_full = sage_full.predict_guarded(scenario_test)
        metrics_full = compute_interval_metrics(
            y_true=y_eval_truth,
            y_pred=res_full.point_forecast,
            lower=res_full.lower_envelope,
            upper=res_full.upper_envelope,
            alpha=alpha,
            is_published=res_full.is_published,
            strata_labels=strata,
        )
        scenario_results["sage_full"] = {
            "calibrator": "SAGE Sensitivity Envelope + Guarded Policy",
            "metrics": metrics_full,
        }

        # 5. SAGE without Abstention (Always Published)
        metrics_no_abstain = compute_interval_metrics(
            y_true=y_eval_truth,
            y_pred=res_full.point_forecast,
            lower=res_full.lower_envelope,
            upper=res_full.upper_envelope,
            alpha=alpha,
            is_published=np.ones(len(scenario_test), dtype=bool),
            strata_labels=strata,
        )
        scenario_results["sage_no_abstain"] = {
            "calibrator": "SAGE Sensitivity Envelope (Always Published)",
            "metrics": metrics_no_abstain,
        }

        # 6. SAGE Gamma=0 (MAR assumption, no envelope)
        res_g0 = sage_g0.predict_guarded(scenario_test)
        metrics_g0 = compute_interval_metrics(
            y_true=y_eval_truth,
            y_pred=res_g0.point_forecast,
            lower=res_g0.lower_envelope,
            upper=res_g0.upper_envelope,
            alpha=alpha,
            is_published=res_g0.is_published,
            strata_labels=strata,
        )
        scenario_results["sage_gamma_0"] = {
            "calibrator": "SAGE Point Conformal (Gamma=0 MAR)",
            "metrics": metrics_g0,
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


def run_time_block_evaluation(
    splits: DatasetSplits,
    nominal_coverage: float = 0.90,
    n_blocks: int = 3,
) -> dict[str, Any]:
    """Evaluate performance stability across chronological sub-windows of the test set."""
    alpha = 1.0 - nominal_coverage
    test_df = splits.test.sort_values("timestamp").reset_index(drop=True)
    n = len(test_df)
    block_size = n // n_blocks

    time_blocks: dict[str, Any] = {}

    # Fit reference models
    train_df = splits.train
    cal_df = splits.calibrate
    feature_cols = splits.feature_columns

    lgb_point = LightGBMPointForecaster(feature_columns=feature_cols).fit(
        train_df, train_df["target"].to_numpy()
    )
    lgb_quant = LightGBMQuantileForecaster(feature_columns=feature_cols, quantiles=(0.05, 0.50, 0.95)).fit(
        train_df, train_df["target"].to_numpy()
    )
    y_cal = cal_df["target"].to_numpy()

    point_cal = SplitConformalCalibrator(alpha=alpha).fit(y_cal, lgb_point.predict(cal_df))
    cal_q = lgb_quant.predict_quantiles(cal_df)
    cqr_cal = ConformalizedQuantileRegression(alpha=alpha).fit(y_cal, cal_q[0.05], cal_q[0.95])

    sage = SAGEForecaster(
        config=SAGEConfig(nominal_coverage=nominal_coverage),
        feature_columns=feature_cols,
    ).fit(X=train_df, y=train_df["target"].to_numpy(), X_cal=cal_df, y_cal=y_cal)

    for b in range(n_blocks):
        start_idx = b * block_size
        end_idx = (b + 1) * block_size if b < n_blocks - 1 else n
        sub_df = test_df.iloc[start_idx:end_idx].copy()
        y_true = sub_df["target"].to_numpy()
        t_start = sub_df["timestamp"].min().isoformat()
        t_end = sub_df["timestamp"].max().isoformat()
        block_label = f"block_{b + 1}_{t_start[:10]}_to_{t_end[:10]}"

        # LightGBM point + split conformal
        pred_pt = lgb_point.predict(sub_df)
        l_pt, u_pt = point_cal.predict_interval(pred_pt)
        m_pt = compute_interval_metrics(y_true, pred_pt, l_pt, u_pt, alpha=alpha)

        # LightGBM + CQR
        q_dict = lgb_quant.predict_quantiles(sub_df)
        l_cqr, u_cqr = cqr_cal.predict_interval(q_dict[0.05], q_dict[0.95])
        m_cqr = compute_interval_metrics(y_true, pred_pt, l_cqr, u_cqr, alpha=alpha)

        # SAGE
        res_sage = sage.predict_guarded(sub_df)
        m_sage = compute_interval_metrics(
            y_true,
            res_sage.point_forecast,
            res_sage.lower_envelope,
            res_sage.upper_envelope,
            alpha=alpha,
            is_published=res_sage.is_published,
        )

        time_blocks[block_label] = {
            "start": t_start,
            "end": t_end,
            "n_samples": len(sub_df),
            "lightgbm_split": {
                "picp": m_pt["picp"],
                "mpiw": m_pt["mpiw"],
                "winkler_score": m_pt["winkler_score"],
            },
            "lightgbm_cqr": {
                "picp": m_cqr["picp"],
                "mpiw": m_cqr["mpiw"],
                "winkler_score": m_cqr["winkler_score"],
            },
            "sage_full": {
                "picp": m_sage["picp"],
                "published_picp": m_sage["published_picp"],
                "mpiw": m_sage["mpiw"],
                "winkler_score": m_sage["winkler_score"],
                "abstention_rate": m_sage["abstention_rate"],
            },
        }

    return time_blocks


def run_station_held_out_evaluation(
    featured_df: pd.DataFrame,
    station_coords: dict[str, tuple[float, float]],
    nominal_coverage: float = 0.90,
) -> dict[str, Any]:
    """Execute Leave-One-Station-Out cross-validation to assess spatial generalizability."""
    alpha = 1.0 - nominal_coverage
    unique_stations = sorted(featured_df["station_id"].unique())
    logger.info("Executing Leave-One-Station-Out CV across %d stations...", len(unique_stations))

    held_out_results: dict[str, Any] = {}

    for held_station in unique_stations:
        # Split: train/cal on other 7 stations, test on held-out station
        other_stations = [s for s in unique_stations if s != held_station]
        other_coords = {k: v for k, v in station_coords.items() if k in other_stations}
        train_cal_df = featured_df[featured_df["station_id"].isin(other_stations)].copy()
        test_df = featured_df[featured_df["station_id"] == held_station].copy()

        if len(test_df) < 50 or len(train_cal_df) < 200:
            continue

        splits_other = partition_dataset(train_cal_df, other_coords, train_pct=0.75, cal_pct=0.25)
        train_df = splits_other.train
        cal_df = splits_other.calibrate
        feature_cols = splits_other.feature_columns

        # Fit models
        lgb_pt = LightGBMPointForecaster(feature_columns=feature_cols).fit(
            train_df, train_df["target"].to_numpy()
        )
        lgb_q = LightGBMQuantileForecaster(feature_columns=feature_cols, quantiles=(0.05, 0.50, 0.95)).fit(
            train_df, train_df["target"].to_numpy()
        )

        y_cal = cal_df["target"].to_numpy()
        pt_cal = SplitConformalCalibrator(alpha=alpha).fit(y_cal, lgb_pt.predict(cal_df))
        cal_q = lgb_q.predict_quantiles(cal_df)
        cqr_cal = ConformalizedQuantileRegression(alpha=alpha).fit(y_cal, cal_q[0.05], cal_q[0.95])

        sage = SAGEForecaster(
            config=SAGEConfig(nominal_coverage=nominal_coverage),
            feature_columns=feature_cols,
        ).fit(X=train_df, y=train_df["target"].to_numpy(), X_cal=cal_df, y_cal=y_cal)

        y_test = test_df["target"].to_numpy()

        # Evaluate LightGBM Split
        pred_pt = lgb_pt.predict(test_df)
        l_pt, u_pt = pt_cal.predict_interval(pred_pt)
        m_pt = compute_interval_metrics(y_test, pred_pt, l_pt, u_pt, alpha=alpha)

        # Evaluate CQR
        q_test = lgb_q.predict_quantiles(test_df)
        l_cqr, u_cqr = cqr_cal.predict_interval(q_test[0.05], q_test[0.95])
        m_cqr = compute_interval_metrics(y_test, pred_pt, l_cqr, u_cqr, alpha=alpha)

        # Evaluate SAGE
        res_sage = sage.predict_guarded(test_df)
        m_sage = compute_interval_metrics(
            y_test,
            res_sage.point_forecast,
            res_sage.lower_envelope,
            res_sage.upper_envelope,
            alpha=alpha,
            is_published=res_sage.is_published,
        )

        held_out_results[f"station_{held_station}"] = {
            "n_test_samples": len(test_df),
            "persistence_mae": float(np.nanmean(np.abs(y_test - test_df["last_pm25"].to_numpy()))),
            "lightgbm_split": {
                "mae": m_pt["mae"],
                "picp": m_pt["picp"],
                "mpiw": m_pt["mpiw"],
                "winkler_score": m_pt["winkler_score"],
            },
            "lightgbm_cqr": {
                "mae": m_cqr["mae"],
                "picp": m_cqr["picp"],
                "mpiw": m_cqr["mpiw"],
                "winkler_score": m_cqr["winkler_score"],
            },
            "sage_full": {
                "mae": m_sage["mae"],
                "picp": m_sage["picp"],
                "published_picp": m_sage["published_picp"],
                "mpiw": m_sage["mpiw"],
                "winkler_score": m_sage["winkler_score"],
                "abstention_rate": m_sage["abstention_rate"],
            },
        }

    return held_out_results
