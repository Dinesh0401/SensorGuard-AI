"""Point-in-time benchmark with honest observed-label calibration and full synthetic audit."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sensorguard.calibration import conformal_radius, policy
from sensorguard.features import FEATURE_COLUMNS, build_features
from sensorguard.simulation import OutageConfig, generate_network, inject_outages


@dataclass(frozen=True)
class ExperimentConfig:
    """Reproducible inputs to a controlled, synthetic benchmark."""

    days: int = 90
    stations: int = 4
    data_seed: int = 11
    outage: OutageConfig = field(default_factory=OutageConfig)
    alpha: float = 0.1


def run_experiment(config: ExperimentConfig = ExperimentConfig()) -> dict:
    """Evaluate ridge vs persistence using identical chronological partitions.

    Train/calibrate on labels that WOULD HAVE BEEN OBSERVED by each cutoff;
    retain all hidden synthetic truths only for final held-out evaluation.
    """
    if config.days < 12 or config.stations < 1:
        raise ValueError("needs >=12 days and >=1 station")
    truth_source = generate_network(config.days, config.stations, config.data_seed)
    observed, evaluator_truth = inject_outages(truth_source, config.outage)
    features = build_features(observed)
    examples = features.merge(evaluator_truth, on=["station_id", "timestamp"], validate="one_to_one")
    timestamps = examples["timestamp"].sort_values().unique()
    t1, t2 = timestamps[int(0.6 * len(timestamps))], timestamps[int(0.8 * len(timestamps))]
    train = examples[(examples["timestamp"] < t1) & examples["reported"]]
    calibrate = examples[(examples["timestamp"] >= t1) & (examples["timestamp"] < t2) & examples["reported"]]
    test = examples[examples["timestamp"] >= t2]
    if len(train) < 25 or len(calibrate) < 25 or test.empty:
        raise ValueError("not enough observed labels for train/calibrate/test partitions")
    x_train, y_train = train[list(FEATURE_COLUMNS)], train["true_pm25"].to_numpy()
    x_cal, y_cal = calibrate[list(FEATURE_COLUMNS)], calibrate["true_pm25"].to_numpy()
    x_test, y_test = test[list(FEATURE_COLUMNS)], test["true_pm25"].to_numpy()
    ridge = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=1.0))
    ridge.fit(x_train, y_train)
    models = {
        "persistence": (calibrate["last_pm25"].to_numpy(), test["last_pm25"].to_numpy()),
        "ridge": (ridge.predict(x_cal), ridge.predict(x_test)),
    }
    statuses, reasons = policy(test["age_hours"].to_numpy())
    output = {
        "data_mode": "synthetic",
        "calibration_claim": "empirical only; no MNAR coverage guarantee",
        "config": {**asdict(config), "outage": asdict(config.outage)},
        "partition": {
            "train_observed": len(train),
            "calibration_observed": len(calibrate),
            "test_all_truth_known_to_evaluator": len(test),
        },
        "actual_missing_fraction": float((~observed["reported"]).mean()),
        "models": {},
    }
    for name, (cal_pred, pred) in models.items():
        radius = conformal_radius(y_cal, cal_pred, config.alpha)
        covered = np.abs(y_test - pred) <= radius
        published = statuses != "abstain"
        metrics = {
            "mae_all_synthetic_truth": float(np.abs(y_test - pred).mean()),
            "coverage_all_synthetic_truth": float(covered.mean()),
            "coverage_published_only": float(covered[published].mean()) if published.any() else None,
            "mean_interval_width": float(2 * radius),
            "published_fraction": float(published.mean()),
            "n_test": len(test),
            "n_published": int(published.sum()),
        }
        strata = {}
        for label in ("publish", "caution", "abstain"):
            mask = statuses == label
            strata[label] = {
                "count": int(mask.sum()),
                "coverage": float(covered[mask].mean()) if mask.any() else None,
            }
        output["models"][name] = {"metrics": metrics, "by_status": strata}
    output["policy_counts"] = {label: int((reasons == label).sum()) for label in np.unique(reasons)}
    return output
