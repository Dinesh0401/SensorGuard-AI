"""Unit tests for Adaptive input-dependent SAGE (Milestone 7.4)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

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
    """Verify forward-expanding cross-validation splits complete hours, enforces embargo, and is future-invariant."""
    rng = np.random.default_rng(123)
    hours = pd.date_range("2025-01-01", periods=120, freq="h", tz="UTC")
    # Construct multi-station panel sharing identical timestamps (3 stations per hour = 360 rows)
    rows = []
    for st in ["1", "2", "3"]:
        pm = 12.0 + 4.0 * np.sin(np.arange(120) / 10.0) + rng.normal(0, 1.0, 120)
        for i, t in enumerate(hours):
            rows.append(
                {
                    "timestamp": t,
                    "station_id": st,
                    "last_pm25": pm[max(0, i - 1)],
                    "lag_1h": pm[max(0, i - 2)],
                    "rolling_std_6h": float(1.0 + (i % 5) * 0.2),
                    "age_hours": 1.0,
                    "neighbor_idw_pm25": pm[max(0, i - 1)] + 0.5,
                    "reported": True,
                    "target": pm[i],
                }
            )
    panel = pd.DataFrame(rows).sample(frac=1.0, random_state=7).reset_index(drop=True)  # Shuffled!

    cfg = AdaptiveSAGEConfig(
        n_oof_folds=3,
        horizon_hours=1.0,
        embargo_hours=1.0,
        base_model_type="ridge",
    )
    model_base = AdaptiveSAGEForecaster(config=cfg)
    _idx_b, res_b, pred_b, audit_b = model_base.compute_causal_oof_residuals(
        panel, panel["target"].to_numpy()
    )

    # 1. Complete unique timestamp splitting & embargo audit
    assert len(audit_b) == 2
    for fold_info in audit_b:
        assert fold_info["same_hour_disjoint"] is True
        assert fold_info["embargo_gap_hours"] >= 2.0  # horizon(1h) + embargo(1h)
    # Every timestamp in fold 1 (hours 40..79) contains ALL 3 stations together
    fold1_ts = hours[40:80]
    fold1_mask = model_base.causal_oof_timestamps_.isin(fold1_ts)
    assert int(fold1_mask.sum()) == 40 * 3

    # 2. True Future-Perturbation Invariance Test:
    # Massively corrupt targets and features in the future fold (hours >= 80)
    perturbed = panel.copy()
    future_mask = pd.to_datetime(perturbed["timestamp"], utc=True) >= hours[80]
    perturbed.loc[future_mask, "target"] = 99999.0
    perturbed.loc[future_mask, "last_pm25"] = -50000.0
    perturbed.loc[future_mask, "neighbor_idw_pm25"] = 88888.0

    model_pert = AdaptiveSAGEForecaster(config=cfg)
    _idx_p, res_p, pred_p, _audit_p = model_pert.compute_causal_oof_residuals(
        perturbed, perturbed["target"].to_numpy()
    )

    # Fold 1 OOF predictions and residuals must be BIT-FOR-BIT IDENTICAL despite corrupted future fold 2
    np.testing.assert_allclose(pred_b[fold1_mask], pred_p[fold1_mask], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(res_b[fold1_mask], res_p[fold1_mask], rtol=0.0, atol=0.0)

    # Even stronger: perturb the embargo buffer hour immediately before fold 1 (hours 38 & 39)
    # Because embargo = horizon(1h) + embargo(1h) = 2h, hours >= 39 are purged before fold 1 starts at hour 40!
    embargo_perturbed = panel.copy()
    purge_mask = pd.to_datetime(embargo_perturbed["timestamp"], utc=True) >= hours[39]
    embargo_perturbed.loc[purge_mask, "target"] = 77777.0
    embargo_perturbed.loc[purge_mask, "last_pm25"] = -77777.0
    embargo_perturbed.loc[purge_mask, "reported"] = True
    # Only evaluate non-target-perturbed fold 1 (keep fold 1 features/targets intact, perturb hour 39 target!)
    embargo_target_only = panel.copy()
    h39_mask = pd.to_datetime(embargo_target_only["timestamp"], utc=True) == hours[39]
    embargo_target_only.loc[h39_mask, "target"] = 77777.0
    model_emb = AdaptiveSAGEForecaster(config=cfg)
    _idx_e, res_e, pred_e, _ = model_emb.compute_causal_oof_residuals(
        embargo_target_only, embargo_target_only["target"].to_numpy()
    )
    np.testing.assert_allclose(pred_b[fold1_mask], pred_e[fold1_mask], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(res_b[fold1_mask], res_e[fold1_mask], rtol=0.0, atol=0.0)


def test_adaptive_sage_rejects_untrustworthy_or_noncausal_timestamps():
    """Reject missing/NaT timestamps and causal violations across decision/feature/availability columns."""
    train, _cal, _ = _synthetic_heteroscedastic_split(n=120, seed=5)

    # Missing timestamp column
    no_ts = train.drop(columns=["timestamp"])
    with pytest.raises(ValueError, match="trustworthy timestamps"):
        AdaptiveSAGEForecaster().fit(no_ts, no_ts["target"].to_numpy())

    # NaT timestamp entry
    nat_ts = train.copy()
    nat_ts.loc[nat_ts.index[0], "timestamp"] = pd.NaT
    with pytest.raises(ValueError, match="missing or unparseable"):
        AdaptiveSAGEForecaster().fit(nat_ts, nat_ts["target"].to_numpy())

    # Future issued_at violating 1h horizon
    bad_issued = train.copy()
    bad_issued["issued_at"] = pd.to_datetime(bad_issued["timestamp"], utc=True)
    with pytest.raises(ValueError, match="Causal violation: 'issued_at'"):
        AdaptiveSAGEForecaster().fit(bad_issued, bad_issued["target"].to_numpy())

    # Future feature_timestamp exceeding decision time
    bad_feat = train.copy()
    bad_feat["feature_timestamp"] = pd.to_datetime(bad_feat["timestamp"], utc=True) + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="Causal violation: 'feature_timestamp'"):
        AdaptiveSAGEForecaster().fit(bad_feat, bad_feat["target"].to_numpy())

    # Invalid target_available_at preceding observation target
    bad_avail = train.copy()
    bad_avail["target_available_at"] = pd.to_datetime(bad_avail["timestamp"], utc=True) - pd.Timedelta(
        hours=2
    )
    with pytest.raises(ValueError, match="Causal violation: 'target_available_at'"):
        AdaptiveSAGEForecaster().fit(bad_avail, bad_avail["target"].to_numpy())
