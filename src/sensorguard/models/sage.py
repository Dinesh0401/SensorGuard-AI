"""Sensitivity-Aware Guarded Estimation (SAGE) for robust uncertainty quantification.

Implements the SAGE framework for environmental sensor networks under nonrandom missingness (MNAR):
- Base point/quantile forecasting (LightGBM / Ridge)
- Propensity modeling for sensor reporting probability P(R=1 | X) via LogisticRegression
- Sensitivity reweighting and bound shifts across an analyst-imposed gamma grid:
    logit P(R=1 | X, Y) = g(X) + gamma * (Y - mu) / sigma
- Conservative interval envelope [L_envelope, U_envelope] = [min_gamma L_gamma, max_gamma U_gamma]
- Scenario disagreement metric: max_gamma U_gamma - min_gamma L_gamma
- Guarded safety policy abstaining under severe outage, excessive disagreement, or stale data
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from sensorguard.models.forecasters import (
    BaseForecaster,
    LightGBMPointForecaster,
    RidgeForecaster,
)


@dataclass
class SAGEConfig:
    """Configuration parameters for Sensitivity-Aware Guarded Estimation (SAGE).

    Attributes:
        gamma_grid: Hypothesized MNAR sensitivity values.
        nominal_coverage: Target prediction interval coverage level (1 - alpha).
        max_disagreement_threshold: Max scenario disagreement (envelope width) before abstention.
        min_effective_sample_size: Minimum Kish effective sample size required to publish.
        base_model_type: Forecaster architecture ('lightgbm' or 'ridge').
        stale_age_hours: Sensor history age (hours) beyond which forecast must abstain.
        caution_age_hours: Sensor history age (hours) triggering caution status.
        caution_disagreement_ratio: Fraction of max disagreement threshold triggering caution.
    """

    gamma_grid: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0)
    nominal_coverage: float = 0.90
    max_disagreement_threshold: float = 8.0
    min_effective_sample_size: int = 15
    base_model_type: str = "lightgbm"
    stale_age_hours: float = 12.0
    caution_age_hours: float = 3.0
    caution_disagreement_ratio: float = 0.70


@dataclass
class SAGEResult:
    """Output container for SAGE guarded predictions and sensitivity diagnostics."""

    point_forecast: np.ndarray
    lower_envelope: np.ndarray
    upper_envelope: np.ndarray
    scenario_disagreement: np.ndarray
    status: np.ndarray
    reason: np.ndarray
    effective_sample_size: float
    gamma_intervals: dict[float, tuple[np.ndarray, np.ndarray]]

    @property
    def width(self) -> np.ndarray:
        """Width of the conservative prediction interval envelope."""
        return np.maximum(0.0, self.upper_envelope - self.lower_envelope)

    @property
    def is_published(self) -> np.ndarray:
        """Boolean mask of forecasts approved for publication (not abstained)."""
        return self.status != "abstain"


def guarded_decision_rule(
    scenario_disagreement: np.ndarray | float,
    effective_sample_size: float,
    age_hours: np.ndarray | float,
    max_disagreement_threshold: float = 8.0,
    min_effective_sample_size: int = 15,
    stale_age_hours: float = 12.0,
    caution_age_hours: float = 3.0,
    caution_disagreement_ratio: float = 0.70,
) -> tuple[Any, Any]:
    """Guarded decision policy evaluating disagreement, effective sample size, and freshness.

    Returns:
        tuple (status, reason) where status is in {'publish', 'caution', 'abstain'}.
    """
    is_scalar = np.isscalar(scenario_disagreement) and np.isscalar(age_hours)
    disag = np.atleast_1d(np.asarray(scenario_disagreement, dtype=float))
    ages = np.atleast_1d(np.asarray(age_hours, dtype=float))

    if len(disag) != len(ages) and len(ages) == 1:
        ages = np.full_like(disag, ages[0])
    elif len(disag) != len(ages) and len(disag) == 1:
        disag = np.full_like(ages, disag[0])

    n = len(disag)
    statuses = np.empty(n, dtype=object)
    reasons = np.empty(n, dtype=object)

    for i in range(n):
        age_val = ages[i]
        disag_val = disag[i]

        if age_val > stale_age_hours:
            statuses[i] = "abstain"
            reasons[i] = "stale_sensor_history"
        elif effective_sample_size < min_effective_sample_size:
            statuses[i] = "abstain"
            reasons[i] = "insufficient_effective_sample_size"
        elif disag_val > max_disagreement_threshold:
            statuses[i] = "abstain"
            reasons[i] = "scenario_disagreement_exceeds_threshold"
        elif age_val > caution_age_hours:
            statuses[i] = "caution"
            reasons[i] = "reduced_sensor_freshness"
        elif disag_val > caution_disagreement_ratio * max_disagreement_threshold:
            statuses[i] = "caution"
            reasons[i] = "moderate_scenario_disagreement"
        else:
            statuses[i] = "publish"
            reasons[i] = "sufficient_evidence"

    if is_scalar:
        return str(statuses[0]), str(reasons[0])
    return statuses, reasons


class SAGEForecaster(BaseForecaster):
    """Sensitivity-Aware Guarded Estimation (SAGE) Forecaster.

    Combines:
    1. Base machine learning point forecasting (LightGBM / Ridge).
    2. Propensity modeling of reporting indicator R from observable features X.
    3. Nonconformity quantile calibration and sensitivity bound shifts over gamma_grid.
    4. Conservative interval envelopment across hypothesized MNAR scenarios.
    5. Guarded decision rule with explicit abstention logging.
    """

    def __init__(
        self,
        config: SAGEConfig | None = None,
        base_model: BaseForecaster | None = None,
        feature_columns: list[str] | None = None,
        propensity_features: list[str] | None = None,
    ) -> None:
        self.config = config or SAGEConfig()
        self.feature_columns = feature_columns
        self.propensity_features = propensity_features

        # Initialize base model
        if base_model is not None:
            self.base_model = base_model
        elif self.config.base_model_type.lower() == "ridge":
            self.base_model = RidgeForecaster(feature_columns=feature_columns)
        else:
            self.base_model = LightGBMPointForecaster(feature_columns=feature_columns)

        # Propensity pipeline
        self.propensity_pipeline: Pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(random_state=42, max_iter=500)),
            ]
        )
        self._is_constant_propensity: bool = False
        self._constant_propensity_val: float = 0.95

        # Calibration state
        self._is_calibrated: bool = False
        self.mu_y: float = 0.0
        self.sigma_y: float = 1.0
        self.sigma_res: float = 1.0
        self.effective_sample_size: float = 0.0
        self.scenario_radii: dict[float, float] = {}
        self.scenario_bound_shifts: dict[float, float] = {}
        self.scenario_half_widths: dict[float, float] = {}

    def _get_feature_cols(self, X: pd.DataFrame) -> list[str]:
        exclude = {"target", "true_pm25", "pm25", "station_id", "timestamp", "reported", "R", "is_observed"}
        return [c for c in X.columns if c not in exclude]

    def _extract_propensity_features(self, X: pd.DataFrame) -> pd.DataFrame:
        """Extract observable features (hour, day, neighbor values) for reporting propensity."""
        prop_df = pd.DataFrame(index=X.index)

        # 1. Temporal cyclic / hour features
        if "hour_sin" in X.columns and "hour_cos" in X.columns:
            prop_df["hour_sin"] = X["hour_sin"]
            prop_df["hour_cos"] = X["hour_cos"]
        elif "hour" in X.columns:
            h = X["hour"].to_numpy().astype(float)
            prop_df["hour_sin"] = np.sin(2 * np.pi * h / 24.0)
            prop_df["hour_cos"] = np.cos(2 * np.pi * h / 24.0)
        elif "timestamp" in X.columns:
            ts = pd.to_datetime(X["timestamp"], utc=True)
            prop_df["hour_sin"] = np.sin(2 * np.pi * ts.dt.hour / 24.0)
            prop_df["hour_cos"] = np.cos(2 * np.pi * ts.dt.hour / 24.0)

        # 2. Day-of-week cyclic features
        if "dow_sin" in X.columns and "dow_cos" in X.columns:
            prop_df["dow_sin"] = X["dow_sin"]
            prop_df["dow_cos"] = X["dow_cos"]
        elif "dayofweek" in X.columns or "dow" in X.columns:
            d_col = "dayofweek" if "dayofweek" in X.columns else "dow"
            d = X[d_col].to_numpy().astype(float)
            prop_df["dow_sin"] = np.sin(2 * np.pi * d / 7.0)
            prop_df["dow_cos"] = np.cos(2 * np.pi * d / 7.0)
        elif "timestamp" in X.columns:
            ts = pd.to_datetime(X["timestamp"], utc=True)
            prop_df["dow_sin"] = np.sin(2 * np.pi * ts.dt.dayofweek / 7.0)
            prop_df["dow_cos"] = np.cos(2 * np.pi * ts.dt.dayofweek / 7.0)

        # 3. Spatial neighbor and freshness features
        candidate_cols = [
            c
            for c in X.columns
            if any(
                k in c.lower() for k in ["neighbor", "last_pm25", "rolling_mean", "humidity", "wind", "age"]
            )
        ]
        for col in candidate_cols:
            if col not in prop_df.columns:
                prop_df[col] = pd.to_numeric(X[col], errors="coerce")

        # Fallback if no specific columns identified
        if prop_df.empty or len(prop_df.columns) == 0:
            feat_cols = self._get_feature_cols(X)
            for c in feat_cols:
                prop_df[c] = pd.to_numeric(X[c], errors="coerce")

        return prop_df

    def _determine_reported_indicator(self, X: pd.DataFrame, y: np.ndarray) -> np.ndarray:
        """Identify binary observation indicator R."""
        if "reported" in X.columns:
            return X["reported"].to_numpy().astype(int)
        if "R" in X.columns:
            return X["R"].to_numpy().astype(int)
        if "is_observed" in X.columns:
            return X["is_observed"].to_numpy().astype(int)
        if np.isnan(y).any():
            return (~np.isnan(y)).astype(int)
        if "age_hours" in X.columns:
            return (X["age_hours"].to_numpy() <= 1.0).astype(int)
        return np.ones(len(X), dtype=int)

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        X_cal: pd.DataFrame | None = None,
        y_cal: np.ndarray | None = None,
    ) -> SAGEForecaster:
        """Fit base forecaster, propensity model, and sensitivity calibration parameters.

        Args:
            X: Training features dataframe.
            y: Target values array.
            X_cal: Optional dedicated calibration features.
            y_cal: Optional dedicated calibration targets.
        """
        cols = self.feature_columns or self._get_feature_cols(X)
        self.feature_columns = cols

        # 1. Fit Propensity Model P(R=1 | X)
        R = self._determine_reported_indicator(X, y)
        prop_X = self._extract_propensity_features(X)
        self.propensity_features = list(prop_X.columns)

        unique_R = np.unique(R)
        if len(unique_R) >= 2:
            self._is_constant_propensity = False
            self.propensity_pipeline.fit(prop_X, R)
        else:
            self._is_constant_propensity = True
            self._constant_propensity_val = float(np.mean(R)) if len(R) > 0 else 0.95

        # 2. Fit Base Forecaster on valid target observations
        y_arr = np.asarray(y, dtype=float)
        valid_train = np.isfinite(y_arr)
        if valid_train.sum() == 0:
            raise ValueError("No finite target observations found in training set")

        X_train_valid = X[valid_train].copy()
        y_train_valid = y_arr[valid_train]
        self.base_model.fit(X_train_valid[cols], y_train_valid)

        # 3. Calibrate conformal quantiles and sensitivity bounds
        if X_cal is not None and y_cal is not None:
            self.calibrate(X_cal, y_cal)
        else:
            self.calibrate(X_train_valid[cols], y_train_valid)

        return self

    def calibrate(self, X_cal: pd.DataFrame, y_cal: np.ndarray) -> SAGEForecaster:
        """Calibrate nonconformity quantiles and bounds across sensitivity gamma grid.

        Computes:
            logit P(R=1 | X, Y) = g(X) + gamma * (Y - mu) / sigma
        and derives scenario-adjusted intervals and Kish effective sample sizes.
        """
        y_c = np.asarray(y_cal, dtype=float)
        valid_mask = np.isfinite(y_c)
        if valid_mask.sum() < 2:
            raise ValueError("Calibration set requires at least 2 finite target observations")

        X_c = X_cal[valid_mask].copy()
        y_c = y_c[valid_mask]
        n_cal = len(y_c)

        # Point predictions and calibration residuals
        preds = self.base_model.predict(X_c)
        residuals = np.abs(y_c - preds)

        self.mu_y = float(np.mean(y_c))
        self.sigma_y = float(max(np.std(y_c), 1.0))
        self.sigma_res = float(max(np.std(residuals), 0.5))

        # Propensity scores P(R=1 | X)
        if self._is_constant_propensity:
            p_obs = np.full(n_cal, self._constant_propensity_val)
        else:
            prop_X = self._extract_propensity_features(X_c)
            p_obs = self.propensity_pipeline.predict_proba(prop_X)[:, 1]

        p_obs = np.clip(p_obs, 0.01, 0.99)
        g_X = np.log(p_obs / (1.0 - p_obs))
        z = (y_c - self.mu_y) / self.sigma_y

        alpha = 1.0 - self.config.nominal_coverage
        target_quantile = 1.0 - alpha

        self.scenario_radii = {}
        self.scenario_bound_shifts = {}
        self.scenario_half_widths = {}
        ess_list: list[float] = []

        # Sort gamma grid in ascending order to guarantee strict monotonicity
        sorted_gammas = sorted(self.config.gamma_grid)
        prev_half_width = 0.0

        for gamma in sorted_gammas:
            # logit P(R=1 | X, Y) = g(X) + gamma * (Y - mu) / sigma
            logit_p = np.clip(g_X + gamma * z, -20.0, 20.0)
            pi_gamma = 1.0 / (1.0 + np.exp(-logit_p))

            # Sensitivity importance weights w_i = 1 / pi_gamma
            weights = 1.0 / np.clip(pi_gamma, 1e-4, 1.0)
            w_sum = np.sum(weights)
            w_norm = weights / w_sum if w_sum > 0 else np.full(n_cal, 1.0 / n_cal)

            # Kish's Effective Sample Size: (sum w)^2 / sum(w^2) = 1 / sum(w_norm^2)
            n_eff = float(1.0 / np.sum(w_norm**2))
            ess_list.append(n_eff)

            # Weighted conformal residual quantile
            sort_idx = np.argsort(residuals)
            res_sorted = residuals[sort_idx]
            w_sorted = w_norm[sort_idx]
            cum_w = np.cumsum(w_sorted)

            # Finite sample conformal correction factor: ceil((n+1)*(1-alpha)) / n
            finite_sample_q = min(1.0, math.ceil((n_cal + 1) * target_quantile) / n_cal)
            q_idx = np.searchsorted(cum_w, finite_sample_q)
            q_idx = min(q_idx, n_cal - 1)
            r_gamma = float(res_sorted[q_idx])

            # Bound shift proportional to hypothesized sensitivity parameter
            shift_gamma = float(gamma * (self.sigma_res / 2.0))

            # Enforce non-decreasing half-width across increasing gamma
            half_width = max(r_gamma + shift_gamma, prev_half_width)
            prev_half_width = half_width

            self.scenario_radii[gamma] = r_gamma
            self.scenario_bound_shifts[gamma] = shift_gamma
            self.scenario_half_widths[gamma] = half_width

        self.effective_sample_size = float(min(ess_list)) if ess_list else float(n_cal)
        self._is_calibrated = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Generate point forecasts (PM2.5 >= 0.0)."""
        cols = self.feature_columns or self._get_feature_cols(X)
        feat_df = X[cols] if cols and set(cols).issubset(X.columns) else X
        pred = self.base_model.predict(feat_df)
        return np.clip(pred, 0.0, None)

    def predict_scenario(self, X: pd.DataFrame, gamma: float) -> tuple[np.ndarray, np.ndarray]:
        """Generate prediction interval for a single hypothesized gamma scenario."""
        if not self._is_calibrated:
            raise RuntimeError("Forecaster must be fit/calibrated before predicting")
        if gamma not in self.scenario_half_widths:
            # Interpolate or extrapolate half width for unseen gamma
            half_width = float(self.sigma_res + gamma * (self.sigma_res / 2.0))
        else:
            half_width = self.scenario_half_widths[gamma]

        pred = self.predict(X)
        lower = np.clip(pred - half_width, 0.0, None)
        upper = pred + half_width
        return lower, upper

    def predict_all_scenarios(self, X: pd.DataFrame) -> dict[float, tuple[np.ndarray, np.ndarray]]:
        """Generate prediction intervals across all gamma values in config.gamma_grid."""
        pred = self.predict(X)
        res: dict[float, tuple[np.ndarray, np.ndarray]] = {}
        for gamma in self.config.gamma_grid:
            half_width = self.scenario_half_widths.get(
                gamma, float(self.sigma_res + gamma * (self.sigma_res / 2.0))
            )
            lower = np.clip(pred - half_width, 0.0, None)
            upper = pred + half_width
            res[gamma] = (lower, upper)
        return res

    def predict_interval(
        self, X: pd.DataFrame, nominal_coverage: float = 0.90
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate conservative envelope prediction interval [L_envelope, U_envelope]."""
        if not self._is_calibrated:
            raise RuntimeError("Forecaster must be fit/calibrated before predicting intervals")

        scenarios = self.predict_all_scenarios(X)
        lowers = [l_arr for l_arr, _ in scenarios.values()]
        uppers = [u_arr for _, u_arr in scenarios.values()]

        # [L_envelope, U_envelope] = [min_gamma L_gamma, max_gamma U_gamma]
        lower_envelope = np.min(lowers, axis=0)
        upper_envelope = np.max(uppers, axis=0)
        return lower_envelope, upper_envelope

    def predict_guarded(self, X: pd.DataFrame) -> SAGEResult:
        """Generate full sensitivity-aware guarded forecast with safety decision."""
        if not self._is_calibrated:
            raise RuntimeError("Forecaster must be fit/calibrated before predicting")

        point_pred = self.predict(X)
        scenarios = self.predict_all_scenarios(X)

        lowers = [l_arr for l_arr, _ in scenarios.values()]
        uppers = [u_arr for _, u_arr in scenarios.values()]

        # [L_envelope, U_envelope] = [min_gamma L_gamma, max_gamma U_gamma]
        lower_envelope = np.min(lowers, axis=0)
        upper_envelope = np.max(uppers, axis=0)

        # Scenario disagreement: max_gamma U_gamma - min_gamma L_gamma
        scenario_disagreement = upper_envelope - lower_envelope

        # Determine age of observations for freshness guard
        if "age_hours" in X.columns:
            age_hours = pd.to_numeric(X["age_hours"], errors="coerce").fillna(0.0).to_numpy()
        elif "age" in X.columns:
            age_hours = pd.to_numeric(X["age"], errors="coerce").fillna(0.0).to_numpy()
        else:
            age_hours = np.zeros(len(X), dtype=float)

        statuses, reasons = guarded_decision_rule(
            scenario_disagreement=scenario_disagreement,
            effective_sample_size=self.effective_sample_size,
            age_hours=age_hours,
            max_disagreement_threshold=self.config.max_disagreement_threshold,
            min_effective_sample_size=self.config.min_effective_sample_size,
            stale_age_hours=self.config.stale_age_hours,
            caution_age_hours=self.config.caution_age_hours,
            caution_disagreement_ratio=self.config.caution_disagreement_ratio,
        )

        return SAGEResult(
            point_forecast=point_pred,
            lower_envelope=lower_envelope,
            upper_envelope=upper_envelope,
            scenario_disagreement=scenario_disagreement,
            status=np.asarray(statuses),
            reason=np.asarray(reasons),
            effective_sample_size=self.effective_sample_size,
            gamma_intervals=scenarios,
        )

    def guarded_decision(
        self,
        scenario_disagreement: np.ndarray | float,
        effective_sample_size: float | None = None,
        age_hours: np.ndarray | float = 0.0,
    ) -> tuple[Any, Any]:
        """Evaluate guarded decision rule using this forecaster's config and state."""
        ess = self.effective_sample_size if effective_sample_size is None else effective_sample_size
        return guarded_decision_rule(
            scenario_disagreement=scenario_disagreement,
            effective_sample_size=ess,
            age_hours=age_hours,
            max_disagreement_threshold=self.config.max_disagreement_threshold,
            min_effective_sample_size=self.config.min_effective_sample_size,
            stale_age_hours=self.config.stale_age_hours,
            caution_age_hours=self.config.caution_age_hours,
            caution_disagreement_ratio=self.config.caution_disagreement_ratio,
        )
