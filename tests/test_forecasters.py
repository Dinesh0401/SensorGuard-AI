"""Unit and contract tests for forecasting models, conformal calibration, and feature isolation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sensorguard.calibration import (
    AdaptiveConformalInference,
    ConformalizedQuantileRegression,
    RollingConformalCalibrator,
    SplitConformalCalibrator,
    compute_interval_metrics,
    winkler_score,
)
from sensorguard.models.data import build_spatiotemporal_features, haversine_distance, partition_dataset
from sensorguard.models.forecasters import (
    LightGBMPointForecaster,
    LightGBMQuantileForecaster,
    PersistenceForecaster,
    RidgeForecaster,
    SeasonalNaiveForecaster,
    SpatialNeighborForecaster,
)


def test_haversine_distance_computation():
    # London (51.5074, -0.1278) to Paris (48.8566, 2.3522) ~ 343 km
    dist = haversine_distance(51.5074, -0.1278, 48.8566, 2.3522)
    assert 340.0 < dist < 350.0

    # Same point distance is 0.0
    assert haversine_distance(51.5, -0.1, 51.5, -0.1) == 0.0


def test_persistence_and_seasonal_naive_forecasters():
    df = pd.DataFrame({
        "last_pm25": [12.0, 15.0, 20.0],
        "lag_24h": [10.0, np.nan, 18.0],
    })

    p_model = PersistenceForecaster()
    p_pred = p_model.predict(df)
    np.testing.assert_array_equal(p_pred, [12.0, 15.0, 20.0])

    s_model = SeasonalNaiveForecaster()
    s_pred = s_model.predict(df)
    # 2nd sample has NaN lag_24h -> falls back to last_pm25 (15.0)
    np.testing.assert_array_equal(s_pred, [10.0, 15.0, 18.0])


def test_spatial_neighbor_forecaster():
    df = pd.DataFrame({
        "last_pm25": [10.0, 20.0],
        "neighbor_idw_pm25": [14.5, np.nan],
    })
    model = SpatialNeighborForecaster()
    pred = model.predict(df)
    # First uses neighbor IDW, second falls back to last_pm25
    assert pred[0] == 14.5
    assert pred[1] == 20.0


def test_ridge_and_lightgbm_point_forecasters():
    rng = np.random.default_rng(42)
    n = 100
    X = pd.DataFrame({
        "last_pm25": rng.uniform(5, 35, n),
        "lag_1h": rng.uniform(5, 35, n),
        "hour_sin": rng.uniform(-1, 1, n),
        "hour_cos": rng.uniform(-1, 1, n),
    })
    y = X["last_pm25"].to_numpy() + rng.normal(0, 1.0, n)

    ridge = RidgeForecaster()
    ridge.fit(X, y)
    r_pred = ridge.predict(X)
    assert len(r_pred) == n
    assert (r_pred >= 0.0).all()

    lgb = LightGBMPointForecaster(n_estimators=10)
    lgb.fit(X, y)
    l_pred = lgb.predict(X)
    assert len(l_pred) == n
    assert (l_pred >= 0.0).all()


def test_lightgbm_quantile_forecaster_and_cqr():
    rng = np.random.default_rng(42)
    n_train = 200
    n_test = 50

    X_train = pd.DataFrame({
        "last_pm25": rng.uniform(5, 30, n_train),
        "hour_sin": rng.uniform(-1, 1, n_train),
    })
    y_train = X_train["last_pm25"].to_numpy() + rng.normal(0, 2.0, n_train)

    X_test = pd.DataFrame({
        "last_pm25": rng.uniform(5, 30, n_test),
        "hour_sin": rng.uniform(-1, 1, n_test),
    })
    y_test = X_test["last_pm25"].to_numpy() + rng.normal(0, 2.0, n_test)

    q_model = LightGBMQuantileForecaster(n_estimators=15, quantiles=(0.05, 0.50, 0.95))
    q_model.fit(X_train, y_train)

    q_preds = q_model.predict_quantiles(X_test)
    assert 0.05 in q_preds and 0.50 in q_preds and 0.95 in q_preds
    # Verify monotonicity
    assert (q_preds[0.95] >= q_preds[0.05]).all()

    # Fit CQR
    cqr = ConformalizedQuantileRegression(alpha=0.10)
    cqr.fit(y_train, q_model.predict_quantiles(X_train)[0.05], q_model.predict_quantiles(X_train)[0.95])
    lower, upper = cqr.predict_interval(q_preds[0.05], q_preds[0.95])

    assert len(lower) == n_test
    assert len(upper) == n_test
    assert (upper >= lower).all()
    # Check empirical coverage
    cov = float(((y_test >= lower) & (y_test <= upper)).mean())
    assert cov >= 0.75  # Reasonable coverage on small test set


def test_adaptive_conformal_inference_step():
    aci = AdaptiveConformalInference(target_alpha=0.10, gamma=0.02)
    initial_alpha = aci.current_alpha

    # When covered=True, error=0 -> alpha should increase (intervals tighten)
    new_alpha = aci.step(covered=True)
    assert new_alpha > initial_alpha

    # When covered=False, error=1 -> alpha should decrease (intervals widen)
    wider_alpha = aci.step(covered=False)
    assert wider_alpha < new_alpha


def test_rolling_conformal_calibrator():
    rc = RollingConformalCalibrator(window_size=10, alpha=0.10)
    assert rc.current_radius() == float("inf")

    # Add 15 residual observations
    for i in range(15):
        rc.update(y_true=10.0 + i * 0.1, y_pred=10.0)

    r = rc.current_radius()
    assert np.isfinite(r)
    assert r > 0.0

    lower, upper = rc.predict_interval(15.0)
    assert lower == 15.0 - r
    assert upper == 15.0 + r


def test_winkler_score_and_interval_metrics():
    y_true = np.array([10.0, 20.0, 30.0])
    lower = np.array([8.0, 18.0, 25.0])
    upper = np.array([12.0, 22.0, 28.0])  # 30.0 is above upper (28.0)
    pred = np.array([10.0, 20.0, 27.0])

    score = winkler_score(lower, upper, y_true, alpha=0.10)
    assert score > 0.0

    metrics = compute_interval_metrics(y_true, pred, lower, upper, alpha=0.10)
    assert metrics["n_samples"] == 3
    assert metrics["picp"] == pytest.approx(2.0 / 3.0, rel=1e-2)  # 2 of 3 covered
    assert metrics["mae"] > 0.0
    assert metrics["rmse"] > 0.0


def test_no_target_leakage_in_spatiotemporal_features():
    # Construct synthetic 2-station sequence
    ts = pd.date_range("2024-01-01T00:00:00Z", periods=48, freq="1h")
    records = []
    for s_id in ["st1", "st2"]:
        for i, t in enumerate(ts):
            records.append({
                "station_id": s_id,
                "timestamp": t,
                "pm25": float(10 + i * 2),
                "reported": True,
            })
    df = pd.DataFrame(records)
    coords = {"st1": (51.5, -0.1), "st2": (51.6, -0.2)}

    featured = build_spatiotemporal_features(df, coords)

    # For every row, last_pm25 MUST strictly equal the PM2.5 of the previous hour, NOT current target!
    st1_featured = featured[featured["station_id"] == "st1"].sort_values("timestamp").reset_index(drop=True)
    for idx in range(1, len(st1_featured)):
        curr_target = st1_featured.loc[idx, "target"]
        curr_last = st1_featured.loc[idx, "last_pm25"]
        prev_target = st1_featured.loc[idx - 1, "target"]

        # Crucial anti-leakage invariant:
        assert curr_last == prev_target
        assert curr_last != curr_target
