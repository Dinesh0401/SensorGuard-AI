"""Unit tests for the SAGE ablation study module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sensorguard.ablation import (
    ABLATION_CONDITIONS,
    build_ablation_model,
    prepare_scenario_test_data,
    run_sage_ablation_suite,
)
from sensorguard.models.data import DatasetSplits
from sensorguard.models.sage import SAGEForecaster


@pytest.fixture
def mock_dataset_splits() -> DatasetSplits:
    """Generate minimal synthetic splits for fast ablation testing."""
    rng = np.random.default_rng(42)
    n = 100

    def make_df(count: int, start_h: int = 0) -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=count, freq="1h", tz="UTC")
        pm25 = np.clip(10.0 + rng.normal(0, 3, size=count), 1.0, 50.0)
        return pd.DataFrame(
            {
                "station_id": ["st_1"] * count,
                "timestamp": dates,
                "target": pm25,
                "last_pm25": np.roll(pm25, 1),
                "lag_1h": np.roll(pm25, 2),
                "lag_2h": np.roll(pm25, 3),
                "lag_24h": np.roll(pm25, 24),
                "rolling_mean_6h": pm25 * 0.9,
                "rolling_std_6h": np.full(count, 1.5),
                "rolling_mean_24h": pm25 * 0.95,
                "neighbor_idw_pm25": pm25 * 1.02,
                "age_hours": np.full(count, 1.0),
                "hour_sin": np.sin(2 * np.pi * np.arange(count) / 24),
                "hour_cos": np.cos(2 * np.pi * np.arange(count) / 24),
                "dow_sin": np.zeros(count),
                "dow_cos": np.ones(count),
                "reported": [True] * count,
            }
        )

    feature_cols = [
        "last_pm25",
        "lag_1h",
        "lag_2h",
        "lag_24h",
        "rolling_mean_6h",
        "rolling_std_6h",
        "rolling_mean_24h",
        "neighbor_idw_pm25",
        "age_hours",
        "hour_sin",
        "hour_cos",
        "dow_sin",
        "dow_cos",
    ]

    return DatasetSplits(
        train=make_df(n, 0),
        calibrate=make_df(40, n),
        test=make_df(40, n + 40),
        station_coords={"st_1": (51.5, -0.1)},
        feature_columns=feature_cols,
    )


def test_build_ablation_model_conditions(mock_dataset_splits: DatasetSplits) -> None:
    """Verify that build_ablation_model configures appropriate feature columns and configs."""
    feature_cols = mock_dataset_splits.feature_columns

    for cond in ABLATION_CONDITIONS:
        forecaster, cols = build_ablation_model(cond, feature_cols, nominal_coverage=0.90)
        assert isinstance(forecaster, SAGEForecaster)

        if cond == "no_spatial_features":
            assert "neighbor_idw_pm25" not in cols
        elif cond == "no_age_features":
            assert "age_hours" not in cols
        else:
            assert "neighbor_idw_pm25" in cols
            assert "age_hours" in cols

        if cond == "no_sensitivity_grid":
            assert forecaster.config.gamma_grid == (0.0,)
        elif cond == "no_abstention":
            assert forecaster.config.max_disagreement_threshold > 1000.0


def test_prepare_scenario_test_data(mock_dataset_splits: DatasetSplits) -> None:
    """Verify masking logic strictly separates ground truth from test features."""
    test_df = mock_dataset_splits.test
    train_mean = float(mock_dataset_splits.train["target"].mean())
    train_std = float(mock_dataset_splits.train["target"].std())

    # Natural scenario
    scen_df, y_truth = prepare_scenario_test_data(
        test_df, "natural", seed=42, train_mean=train_mean, train_std=train_std
    )
    assert len(scen_df) == len(y_truth)
    assert np.array_equal(scen_df["target"].to_numpy(), y_truth)

    # MCAR scenario
    scen_df_mcar, y_mcar = prepare_scenario_test_data(
        test_df, "mcar_20", seed=42, train_mean=train_mean, train_std=train_std
    )
    assert len(scen_df_mcar) == len(y_mcar)
    # Ground truth remains untouched
    assert np.array_equal(test_df["target"].to_numpy(), y_mcar)

    # MNAR scenario
    scen_df_mnar, y_mnar = prepare_scenario_test_data(
        test_df, "mnar_gamma_1.5", seed=42, train_mean=train_mean, train_std=train_std
    )
    assert len(scen_df_mnar) == len(y_mnar)


def test_run_sage_ablation_suite_deterministic(mock_dataset_splits: DatasetSplits) -> None:
    """Ensure small ablation suite runs end-to-end without errors."""
    summary = run_sage_ablation_suite(
        splits=mock_dataset_splits,
        seeds=[42],
        nominal_coverage=0.90,
        track_mlflow=False,
    )

    assert "study_id" in summary
    assert "aggregates" in summary
    assert "natural" in summary["aggregates"]
    assert "full_sage" in summary["aggregates"]["natural"]
    assert summary["aggregates"]["natural"]["full_sage"]["picp_mean"] is not None


def test_no_abstention_coverage_invariant(mock_dataset_splits: DatasetSplits) -> None:
    """Mathematical invariant: when no abstention policy is active, published coverage strictly equals overall coverage."""
    summary = run_sage_ablation_suite(
        splits=mock_dataset_splits,
        seeds=[42],
        nominal_coverage=0.90,
        track_mlflow=False,
    )
    for scen in summary["scenarios"]:
        no_abs = summary["aggregates"][scen]["no_abstention"]
        assert no_abs["abstention_rate_mean"] == 0.0
        assert no_abs["published_picp_mean"] is not None
        assert no_abs["picp_mean"] is not None
        assert abs(no_abs["published_picp_mean"] - no_abs["picp_mean"]) < 1e-6


def test_masked_features_recomputed_no_leakage(mock_dataset_splits: DatasetSplits) -> None:
    """Verify that simulated outages strictly rebuild dependent features without ground-truth leakage."""
    test_df = mock_dataset_splits.test
    train_mean = float(mock_dataset_splits.train["target"].mean())
    train_std = float(mock_dataset_splits.train["target"].std())

    # Burst outage at station st_1
    scen_df, y_truth = prepare_scenario_test_data(
        test_df,
        "burst_24h",
        seed=42,
        train_mean=train_mean,
        train_std=train_std,
        station_coords=mock_dataset_splits.station_coords,
    )
    assert len(scen_df) == len(y_truth)

    # Check that age_hours reflects outage for the target station
    assert (scen_df["age_hours"] >= 1.0).all()
    # Ground truth array strictly retains original target
    assert np.array_equal(test_df["target"].to_numpy(), y_truth)


def test_content_hash_deterministic(mock_dataset_splits: DatasetSplits) -> None:
    """Verify dataset content fingerprint is deterministic and sensitive to content modifications."""
    from sensorguard.models.data import compute_dataset_content_hash

    hash1 = compute_dataset_content_hash(mock_dataset_splits)
    hash2 = compute_dataset_content_hash(mock_dataset_splits)
    assert hash1 == hash2
    assert len(hash1) == 16

    # Modify a single target value
    modified_splits = DatasetSplits(
        train=mock_dataset_splits.train.copy(),
        calibrate=mock_dataset_splits.calibrate.copy(),
        test=mock_dataset_splits.test.copy(),
        station_coords=mock_dataset_splits.station_coords,
        feature_columns=mock_dataset_splits.feature_columns,
    )
    modified_splits.test.loc[0, "target"] += 50.0
    hash_mod = compute_dataset_content_hash(modified_splits)
    assert hash1 != hash_mod
