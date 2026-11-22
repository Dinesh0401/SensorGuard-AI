"""Tests for evaluation benchmark suite and chronological data partitioning."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sensorguard.calibration import compute_interval_metrics
from sensorguard.evaluation import run_benchmark_suite
from sensorguard.models.data import partition_dataset


def test_partition_dataset_chronological_boundaries():
    ts = pd.date_range("2024-01-01T00:00:00Z", periods=100, freq="1h")
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "station_id": "st1",
            "target": np.linspace(10, 50, 100),
            "last_pm25": np.linspace(10, 50, 100),
            "reported": [True] * 100,
            "lag_1h": np.linspace(10, 50, 100),
            "lag_2h": np.linspace(10, 50, 100),
            "lag_24h": np.linspace(10, 50, 100),
            "rolling_mean_6h": np.linspace(10, 50, 100),
            "rolling_std_6h": [1.0] * 100,
            "rolling_mean_24h": np.linspace(10, 50, 100),
            "neighbor_idw_pm25": np.linspace(10, 50, 100),
            "age_hours": [1.0] * 100,
            "hour_sin": [0.0] * 100,
            "hour_cos": [1.0] * 100,
            "dow_sin": [0.0] * 100,
            "dow_cos": [1.0] * 100,
        }
    )
    coords = {"st1": (51.5, -0.1)}

    splits = partition_dataset(df, coords, train_pct=0.60, cal_pct=0.20)
    assert len(splits.train) == 60
    assert len(splits.calibrate) == 20
    assert len(splits.test) == 20

    # Ensure strict chronological order without overlap
    assert splits.train["timestamp"].max() < splits.calibrate["timestamp"].min()
    assert splits.calibrate["timestamp"].max() < splits.test["timestamp"].min()


def test_run_benchmark_suite_smoke():
    ts = pd.date_range("2024-01-01T00:00:00Z", periods=60, freq="1h")
    records = []
    for s_id in ["st1", "st2"]:
        for t in ts:
            records.append(
                {
                    "timestamp": t,
                    "station_id": s_id,
                    "target": 15.0,
                    "last_pm25": 14.8,
                    "reported": True,
                    "lag_1h": 14.5,
                    "lag_2h": 14.0,
                    "lag_24h": 15.0,
                    "rolling_mean_6h": 14.7,
                    "rolling_std_6h": 0.5,
                    "rolling_mean_24h": 14.6,
                    "neighbor_idw_pm25": 14.9,
                    "age_hours": 1.0,
                    "hour_sin": 0.0,
                    "hour_cos": 1.0,
                    "dow_sin": 0.0,
                    "dow_cos": 1.0,
                }
            )
    df = pd.DataFrame(records)
    coords = {"st1": (51.5, -0.1), "st2": (51.6, -0.2)}

    splits = partition_dataset(df, coords, train_pct=0.50, cal_pct=0.25)
    results = run_benchmark_suite(
        splits=splits,
        nominal_coverage=0.90,
        scenarios=["natural", "mcar_20"],
        track_mlflow=False,
    )

    assert "natural" in results["scenarios"]
    assert "mcar_20" in results["scenarios"]
    assert "persistence" in results["scenarios"]["natural"]
    assert "lightgbm_quantile" in results["scenarios"]["natural"]
    assert "lightgbm_aci" in results["scenarios"]["natural"]


def test_compute_interval_metrics_unified_denominator():
    """Verify unified evaluation mask and published coverage equality under no abstention."""
    y_true = np.array([10.0, 20.0, 30.0, 40.0, np.nan])
    y_pred = np.array([11.0, 19.0, 31.0, 42.0, 15.0])
    lower = np.array([8.0, 15.0, 25.0, 35.0, 10.0])
    upper = np.array([12.0, 25.0, 35.0, 45.0, 20.0])

    # Case 1: All published
    m_all = compute_interval_metrics(y_true, y_pred, lower, upper, alpha=0.10)
    assert m_all["n_total"] == 5
    assert m_all["n_valid_eval"] == 4
    assert m_all["n_published"] == 4
    assert m_all["n_abstained"] == 0
    assert m_all["abstention_rate"] == 0.0
    assert m_all["picp"] == 1.0
    assert m_all["published_picp"] == 1.0

    # Case 2: Partial abstention
    # row 3 (val=40, bounds=[35,45]) is covered, but abstained
    is_pub = np.array([True, True, True, False, True])
    m_part = compute_interval_metrics(y_true, y_pred, lower, upper, alpha=0.10, is_published=is_pub)
    assert m_part["n_valid_eval"] == 4
    assert m_part["n_published"] == 3
    assert m_part["n_abstained"] == 1
    assert m_part["abstention_rate"] == 0.25
    assert m_part["picp"] == 1.0
    assert m_part["published_picp"] == 1.0
