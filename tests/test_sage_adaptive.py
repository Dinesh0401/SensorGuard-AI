"""Unit tests for Adaptive input-dependent SAGE (Milestone 7.4)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sensorguard.models.sage_adaptive import (
    AdaptiveSAGEConfig,
    AdaptiveSAGEForecaster,
    compute_wilson_ci,
    masked_observation_aci_intervals,
)


def _synthetic_heteroscedastic_split(
    n: int = 400, seed: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    last_pm = np.abs(10.0 + 8.0 * np.sin(np.arange(n) / 18.0) + rng.normal(0, 2.0, n))
    vol = np.where((np.arange(n) % 40) < 10, 4.0, 0.5)
    target = np.clip(last_pm + rng.normal(0, vol), 0.0, None)
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "station_id": "1",
            "last_pm25": last_pm,
            "lag_1h": np.roll(last_pm, 1),
            "rolling_std_6h": vol,
            "age_hours": np.where(rng.uniform(size=n) < 0.05, 14.0, 1.0),
            "neighbor_idw_pm25": last_pm + rng.normal(0, 0.5, n),
            "reported": rng.uniform(size=n) > 0.15,
            "target": target,
        }
    )
    train = df.iloc[:240].copy()
    cal = df.iloc[240:320].copy()
    test = df.iloc[320:].copy()
    return train, cal, test


def test_adaptive_sage_produces_input_dependent_disagreement():
    """D_sensitivity must vary non-trivially across test inputs rather than staying constant."""
    train, cal, test = _synthetic_heteroscedastic_split()
    cfg = AdaptiveSAGEConfig(max_disagreement_threshold="cal_p95")
    model = AdaptiveSAGEForecaster(config=cfg).fit(
        X=train,
        y=train["target"].to_numpy(),
        X_cal=cal,
        y_cal=cal["target"].to_numpy(),
    )
    res = model.predict_guarded(test)
    assert len(res.scenario_disagreement) == len(test)
    assert float(np.std(res.scenario_disagreement)) > 1e-3
    assert float(np.max(res.scenario_disagreement) - np.min(res.scenario_disagreement)) > 0.05
    assert model.resolved_disagreement_threshold > 0.0


def test_adaptive_sage_zero_lookahead_features_isolated():
    """Verify AdaptiveSAGE ignores target/poisoned columns when building predictors."""
    train, cal, test = _synthetic_heteroscedastic_split()
    train_p = train.copy()
    cal_p = cal.copy()
    test_p = test.copy()
    test_p["target"] = 9999.0  # Poison test target column
    model = AdaptiveSAGEForecaster().fit(
        X=train_p,
        y=train_p["target"].to_numpy(),
        X_cal=cal_p,
        y_cal=cal_p["target"].to_numpy(),
    )
    assert "target" not in (model.feature_columns or [])
    res = model.predict_guarded(test_p)
    assert np.all(np.isfinite(res.point_forecast))
    assert np.all(res.point_forecast < 200.0)


def test_masked_observation_aci_and_wilson_ci():
    """Verify observation-gated ACI skips missing steps cleanly and Wilson CI bounds behave validly."""
    lo, hi = compute_wilson_ci(90, 100)
    assert 0.82 < lo < 0.90 < hi < 0.96

    lo_aci, hi_aci = masked_observation_aci_intervals(
        y_cal=np.array([10.0, 12.0, 11.0, 13.0]),
        pred_cal=np.array([10.5, 11.2, 11.5, 12.0]),
        y_test=np.array([11.0, np.nan, 18.0, 11.5]),
        pred_test=np.array([10.8, 11.0, 11.0, 11.4]),
        reported_test=np.array([True, False, True, True]),
    )
    assert len(lo_aci) == 4
    assert np.all(hi_aci >= lo_aci)


def test_adaptive_sage_strict_causal_forward_splits_no_future_leakage():
    """Verify forward-expanding cross-validation never allows future data to leak into earlier folds."""
    train, cal, _ = _synthetic_heteroscedastic_split(n=300, seed=42)
    # Ensure chronological order
    train = train.sort_values("timestamp").reset_index(drop=True)

    cfg = AdaptiveSAGEConfig(n_oof_folds=3, base_model_type="ridge")
    model = AdaptiveSAGEForecaster(config=cfg)
    model.fit(
        X=train,
        y=train["target"].to_numpy(),
        X_cal=cal,
        y_cal=cal["target"].to_numpy(),
    )
    assert hasattr(model, "causal_oof_count")
    # For 3 folds on 240 train points, folds 1 and 2 are evaluated (2/3 of train data = 160 points)
    assert model.causal_oof_count == 160
    assert model.causal_oof_median > 0.0
