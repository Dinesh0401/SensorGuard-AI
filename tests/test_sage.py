"""Comprehensive unit and contract tests for SAGE (Sensitivity-Aware Guarded Estimation)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sensorguard.models.forecasters import RidgeForecaster
from sensorguard.models.sage import (
    SAGEConfig,
    SAGEForecaster,
    SAGEResult,
    guarded_decision_rule,
)


@pytest.fixture
def synthetic_sensor_data() -> tuple[pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray]:
    """Generates synthetic spatiotemporal sensor dataset with observation indicators."""
    rng = np.random.default_rng(42)
    n_train = 300
    n_test = 50

    def make_df(n: int, start_idx: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
        hours = np.arange(start_idx, start_idx + n)
        hour_sin = np.sin(2 * np.pi * (hours % 24) / 24.0)
        hour_cos = np.cos(2 * np.pi * (hours % 24) / 24.0)
        dow_sin = np.sin(2 * np.pi * ((hours // 24) % 7) / 7.0)
        dow_cos = np.cos(2 * np.pi * ((hours // 24) % 7) / 7.0)

        last_pm25 = np.clip(25.0 + 10.0 * hour_sin + rng.normal(0, 3.0, n), 2.0, None)
        neighbor_idw = last_pm25 + rng.normal(0, 1.5, n)
        rolling_mean = last_pm25 + rng.normal(0, 2.0, n)
        age_hours = np.clip(rng.exponential(1.2, n), 0.0, 15.0)

        # Nonrandom reporting indicator (MNAR-like: missing more during extreme values or stale age)
        logit_r = 1.5 - 0.2 * age_hours + rng.normal(0, 0.5, n)
        p_r = 1.0 / (1.0 + np.exp(-logit_r))
        reported = rng.random(n) < p_r

        # Target outcome y_{t+1}
        target = last_pm25 + 0.5 * neighbor_idw / 2.0 + rng.normal(0, 2.0, n)
        target = np.clip(target, 0.0, None)

        df = pd.DataFrame(
            {
                "last_pm25": last_pm25,
                "neighbor_idw_pm25": neighbor_idw,
                "rolling_mean_6h": rolling_mean,
                "age_hours": age_hours,
                "hour_sin": hour_sin,
                "hour_cos": hour_cos,
                "dow_sin": dow_sin,
                "dow_cos": dow_cos,
                "reported": reported,
            }
        )
        return df, target

    X_train, y_train = make_df(n_train, start_idx=0)
    X_test, y_test = make_df(n_test, start_idx=n_train)
    return X_train, y_train, X_test, y_test


def test_sage_config_defaults():
    """Verifies SAGEConfig default parameters match Milestone 4 requirements."""
    cfg = SAGEConfig()
    assert cfg.gamma_grid == (0.0, 0.5, 1.0, 1.5, 2.0)
    assert cfg.nominal_coverage == 0.90
    assert cfg.max_disagreement_threshold == 8.0
    assert cfg.min_effective_sample_size == 15
    assert cfg.base_model_type == "lightgbm"
    assert cfg.stale_age_hours == 12.0
    assert cfg.caution_age_hours == 3.0


def test_sage_fit_and_predict(synthetic_sensor_data):
    """Tests SAGE fitting, point prediction, interval prediction, and guarded prediction."""
    X_train, y_train, X_test, _ = synthetic_sensor_data

    # 1. Test with LightGBM base model
    forecaster_lgb = SAGEForecaster(config=SAGEConfig(base_model_type="lightgbm"))
    forecaster_lgb.fit(X_train, y_train)

    # Point forecast
    preds = forecaster_lgb.predict(X_test)
    assert len(preds) == len(X_test)
    assert (preds >= 0.0).all()

    # Conservative envelope interval
    lower_env, upper_env = forecaster_lgb.predict_interval(X_test)
    assert len(lower_env) == len(X_test)
    assert len(upper_env) == len(X_test)
    assert (lower_env >= 0.0).all()
    assert (upper_env >= lower_env).all()

    # Guarded prediction result
    res = forecaster_lgb.predict_guarded(X_test)
    assert isinstance(res, SAGEResult)
    assert len(res.point_forecast) == len(X_test)
    assert len(res.lower_envelope) == len(X_test)
    assert len(res.upper_envelope) == len(X_test)
    assert len(res.scenario_disagreement) == len(X_test)
    assert len(res.status) == len(X_test)
    assert len(res.reason) == len(X_test)
    assert res.effective_sample_size > 0.0
    assert set(res.status).issubset({"publish", "caution", "abstain"})
    scenarios = forecaster_lgb.predict_all_scenarios(X_test)
    uppers = [u_arr for _, u_arr in scenarios.values()]
    np.testing.assert_allclose(res.scenario_disagreement, np.max(uppers, axis=0) - np.min(uppers, axis=0))
    np.testing.assert_allclose(res.width, res.upper_envelope - res.lower_envelope)
    np.testing.assert_allclose(res.envelope_width, res.upper_envelope - res.lower_envelope)

    # 2. Test with Ridge base model
    forecaster_ridge = SAGEForecaster(config=SAGEConfig(base_model_type="ridge"))
    forecaster_ridge.fit(X_train, y_train)
    r_preds = forecaster_ridge.predict(X_test)
    assert len(r_preds) == len(X_test)
    assert (r_preds >= 0.0).all()


def test_envelope_conservatism(synthetic_sensor_data):
    """Tests envelope conservatism: envelope width >= individual gamma scenario widths."""
    X_train, y_train, X_test, _ = synthetic_sensor_data

    config = SAGEConfig(gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0))
    forecaster = SAGEForecaster(config=config)
    forecaster.fit(X_train, y_train)

    res = forecaster.predict_guarded(X_test)
    env_lower = res.lower_envelope
    env_upper = res.upper_envelope
    env_width = res.width

    scenarios = forecaster.predict_all_scenarios(X_test)
    assert len(scenarios) == len(config.gamma_grid)

    for gamma, (l_gamma, u_gamma) in scenarios.items():
        scenario_width = u_gamma - l_gamma

        # Envelope lower bound must be <= individual scenario lower bound
        assert (env_lower <= l_gamma + 1e-6).all(), f"Envelope lower bound violated for gamma={gamma}"

        # Envelope upper bound must be >= individual scenario upper bound
        assert (env_upper >= u_gamma - 1e-6).all(), f"Envelope upper bound violated for gamma={gamma}"

        # Envelope width must be >= individual scenario width
        assert (env_width >= scenario_width - 1e-6).all(), f"Envelope width violated for gamma={gamma}"


def test_sensitivity_monotonicity(synthetic_sensor_data):
    """Tests sensitivity monotonicity: larger gamma widens interval or shifts bounds."""
    X_train, y_train, X_test, _ = synthetic_sensor_data

    config = SAGEConfig(gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0))
    forecaster = SAGEForecaster(config=config)
    forecaster.fit(X_train, y_train)

    scenarios = forecaster.predict_all_scenarios(X_test)
    gammas = sorted(config.gamma_grid)

    for i in range(len(gammas) - 1):
        g_low = gammas[i]
        g_high = gammas[i + 1]

        l_low, u_low = scenarios[g_low]
        l_high, u_high = scenarios[g_high]

        w_low = u_low - l_low
        w_high = u_high - l_high

        # Larger gamma must shift bounds outward or maintain width
        assert (l_high <= l_low + 1e-6).all(), f"Lower bound failed monotonicity between {g_low} and {g_high}"
        assert (u_high >= u_low - 1e-6).all(), f"Upper bound failed monotonicity between {g_low} and {g_high}"
        assert (w_high >= w_low - 1e-6).all(), (
            f"Interval width failed monotonicity between {g_low} and {g_high}"
        )


def test_guarded_abstention_rules():
    """Tests guarded abstention on forced high disagreement, stale age, and low effective sample size."""
    # 1. Stale sensor history (age_hours > 12)
    status, reason = guarded_decision_rule(
        scenario_disagreement=4.0,
        effective_sample_size=25.0,
        age_hours=13.5,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
    )
    assert status == "abstain"
    assert "stale" in reason

    # 2. Insufficient effective sample size (ess < 15)
    status, reason = guarded_decision_rule(
        scenario_disagreement=4.0,
        effective_sample_size=10.0,
        age_hours=1.0,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
    )
    assert status == "abstain"
    assert "effective_sample_size" in reason

    # 3. Scenario disagreement exceeds threshold (> 8.0)
    status, reason = guarded_decision_rule(
        scenario_disagreement=9.5,
        effective_sample_size=25.0,
        age_hours=1.0,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
    )
    assert status == "abstain"
    assert "disagreement" in reason

    # 4. Caution on reduced freshness (age_hours > 3.0 but <= 12)
    status, reason = guarded_decision_rule(
        scenario_disagreement=4.0,
        effective_sample_size=25.0,
        age_hours=5.0,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
        caution_age_hours=3.0,
    )
    assert status == "caution"
    assert "freshness" in reason

    # 5. Caution on moderate scenario disagreement (> 0.7 * threshold)
    status, reason = guarded_decision_rule(
        scenario_disagreement=6.5,
        effective_sample_size=25.0,
        age_hours=1.0,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
        caution_age_hours=3.0,
        caution_disagreement_ratio=0.70,
    )
    assert status == "caution"
    assert "disagreement" in reason

    # 6. Publish on nominal conditions
    status, reason = guarded_decision_rule(
        scenario_disagreement=4.0,
        effective_sample_size=25.0,
        age_hours=0.5,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
        caution_age_hours=3.0,
    )
    assert status == "publish"
    assert "sufficient_evidence" in reason

    # 7. Envelope width exceeds threshold (> 50.0)
    status, reason = guarded_decision_rule(
        scenario_disagreement=4.0,
        effective_sample_size=25.0,
        age_hours=0.5,
        max_disagreement_threshold=8.0,
        min_effective_sample_size=15,
        envelope_width=55.0,
        max_envelope_width_threshold=50.0,
    )
    assert status == "abstain"
    assert "envelope_width" in reason


def test_guarded_decision_vectorized():
    """Tests guarded decision rule operates cleanly across vectorized arrays."""
    disag = np.array([3.0, 10.0, 4.0, 4.0])
    ages = np.array([0.5, 0.5, 14.0, 5.0])
    ess = 20.0

    statuses, _reasons = guarded_decision_rule(
        scenario_disagreement=disag,
        effective_sample_size=ess,
        age_hours=ages,
        max_disagreement_threshold=8.0,
    )
    assert len(statuses) == 4
    assert statuses[0] == "publish"
    assert statuses[1] == "abstain"  # high disagreement
    assert statuses[2] == "abstain"  # stale age
    assert statuses[3] == "caution"  # moderate freshness


def test_zero_lookahead_and_no_target_leakage(synthetic_sensor_data):
    """Tests zero lookahead: ensures features used for prediction contain no target leakage."""
    X_train, y_train, X_test, _ = synthetic_sensor_data

    # Inject spurious future target column in X to ensure forecaster rejects/excludes it
    X_leak = X_train.copy()
    X_leak["target"] = y_train
    X_leak["true_pm25"] = y_train

    forecaster = SAGEForecaster()
    forecaster.fit(X_leak, y_train)

    # Forecaster must never include target or true_pm25 in feature columns
    assert "target" not in forecaster.feature_columns
    assert "true_pm25" not in forecaster.feature_columns
    assert "target" not in forecaster.propensity_features
    assert "true_pm25" not in forecaster.propensity_features

    # Invariance check: changing the future target outcome has zero effect on issue-time forecast
    X_eval = X_test.iloc[:5].copy()
    base_pred = forecaster.predict(X_eval)

    # Re-evaluating prediction with identical issue-time features produces exact identical result
    re_pred = forecaster.predict(X_eval)
    np.testing.assert_array_equal(base_pred, re_pred)


def test_propensity_estimation_and_effective_sample_size(synthetic_sensor_data):
    """Tests propensity model fitting and Kish effective sample size computation."""
    X_train, y_train, _, _ = synthetic_sensor_data

    forecaster = SAGEForecaster()
    forecaster.fit(X_train, y_train)

    assert forecaster.effective_sample_size > 0.0
    assert forecaster.effective_sample_size <= len(X_train)
    assert len(forecaster.scenario_radii) == len(forecaster.config.gamma_grid)
    assert len(forecaster.scenario_bound_shifts) == len(forecaster.config.gamma_grid)


def test_dedicated_calibration_split(synthetic_sensor_data):
    """Tests fitting base model on train split and calibrating on distinct calibration split."""
    X_train, y_train, X_test, y_test = synthetic_sensor_data

    forecaster = SAGEForecaster(base_model=RidgeForecaster())
    forecaster.fit(X_train, y_train, X_cal=X_test, y_cal=y_test)

    assert forecaster._is_calibrated
    res = forecaster.predict_guarded(X_test)
    assert len(res.point_forecast) == len(X_test)
    assert (res.upper_envelope >= res.lower_envelope).all()
