"""Input-dependent Sensitivity-Aware Guarded Estimation (Adaptive SAGE — M7.4 Research).

Addresses M6.1's constant-disagreement limitation where static SAGE yielded a
globally invariant D_sensitivity = 3.3206 ug/m^3 across all inputs. Fits an
out-of-fold local heteroscedastic residual scale estimator s(x) strictly on
the training partition and scales per-gamma conformal sensitivity shifts by
both local residual scale s(x) and local reporting propensity exposure (1 - p(x)).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from sensorguard.models.forecasters import BaseForecaster, LightGBMPointForecaster, RidgeForecaster
from sensorguard.models.sage import SAGEResult, guarded_decision_rule


@dataclass
class AdaptiveSAGEConfig:
    """Configuration for input-dependent heteroscedastic SAGE."""

    gamma_grid: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0)
    nominal_coverage: float = 0.90
    max_disagreement_threshold: float | str = 8.0  # float threshold or "cal_p95" / "cal_p90"
    min_effective_sample_size: int = 15
    base_model_type: str = "lightgbm"
    stale_age_hours: float = 12.0
    caution_age_hours: float = 3.0
    caution_disagreement_ratio: float = 0.70
    max_envelope_width_threshold: float = 50.0
    propensity_risk_weight: float = 1.0
    min_local_scale: float = 0.25
    n_oof_folds: int = 3
    random_state: int = 42


class AdaptiveSAGEForecaster(BaseForecaster):
    """Input-dependent heteroscedastic SAGE forecaster and calibrator."""

    def __init__(
        self,
        config: AdaptiveSAGEConfig | None = None,
        feature_columns: list[str] | None = None,
    ) -> None:
        self.config = config or AdaptiveSAGEConfig()
        self.feature_columns = feature_columns
        self.propensity_features: list[str] = []

        if self.config.base_model_type == "lightgbm":
            self.base_model: BaseForecaster = LightGBMPointForecaster(feature_columns=self.feature_columns)
        elif self.config.base_model_type == "ridge":
            self.base_model = RidgeForecaster(feature_columns=self.feature_columns)
        else:
            raise ValueError(f"Unsupported base_model_type: {self.config.base_model_type}")

        self.scale_model = LGBMRegressor(
            objective="regression_l1",
            n_estimators=60,
            learning_rate=0.05,
            num_leaves=15,
            max_depth=4,
            random_state=self.config.random_state,
            verbose=-1,
            n_jobs=1,
        )
        self.propensity_pipeline: Pipeline = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=1000, C=1.0, random_state=self.config.random_state),
        )

        self._is_constant_propensity: bool = False
        self._constant_propensity_val: float = 0.90
        self._is_calibrated: bool = False
        self._scale_fitted: bool = False

        self.mu_y: float = 0.0
        self.sigma_y: float = 1.0
        self.sigma_norm_res: float = 0.5
        self.min_scale: float = self.config.min_local_scale
        self.max_scale: float = 25.0
        self.effective_sample_size: float = 0.0
        self.scenario_norm_quantiles: dict[float, float] = {}
        self.calibrated_disagreement_p90: float = 0.0
        self.calibrated_disagreement_p95: float = 0.0
        self.resolved_disagreement_threshold: float = 8.0

    def _get_feature_cols(self, X: pd.DataFrame) -> list[str]:
        excluded = {"target", "station_id", "timestamp", "pm25", "reported", "source"}
        return [c for c in X.columns if c not in excluded and pd.api.types.is_numeric_dtype(X[c])]

    def _determine_reported_indicator(self, X: pd.DataFrame, y: np.ndarray) -> np.ndarray:
        if "reported" in X.columns:
            return np.asarray(X["reported"].fillna(False), dtype=int)
        return np.asarray(np.isfinite(y), dtype=int)

    def _extract_propensity_features(self, X: pd.DataFrame) -> pd.DataFrame:
        if self.propensity_features:
            cols = [c for c in self.propensity_features if c in X.columns]
        else:
            cols = self._get_feature_cols(X)
        return X[cols].copy()

    def _make_base_forecaster(self, cols: list[str]) -> BaseForecaster:
        if self.config.base_model_type == "lightgbm":
            return LightGBMPointForecaster(feature_columns=cols)
        return RidgeForecaster(feature_columns=cols)

    def _predict_propensity(self, X: pd.DataFrame) -> np.ndarray:
        n = len(X)
        if self._is_constant_propensity:
            val = float(np.clip(self._constant_propensity_val, 0.01, 0.99))
            return np.full(n, val, dtype=float)
        prop_X = self._extract_propensity_features(X)
        p_obs = self.propensity_pipeline.predict_proba(prop_X)[:, 1]
        return np.clip(p_obs, 0.01, 0.99)

    def _predict_local_scale(self, X: pd.DataFrame) -> np.ndarray:
        if not self._scale_fitted or self.feature_columns is None:
            return np.full(len(X), self.min_scale, dtype=float)
        log_scale = self.scale_model.predict(X[self.feature_columns])
        raw_scale = np.exp(np.clip(log_scale, -4.0, 5.0))
        return np.clip(raw_scale, self.min_scale, self.max_scale)

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        X_cal: pd.DataFrame | None = None,
        y_cal: np.ndarray | None = None,
    ) -> AdaptiveSAGEForecaster:
        """Fit propensity, OOF local residual scale s(x), base model, and sensitivity quantiles."""
        cols = self.feature_columns or self._get_feature_cols(X)
        self.feature_columns = cols

        # 1. Fit causal propensity P(R=1 | X)
        r_ind = self._determine_reported_indicator(X, y)
        prop_X = self._extract_propensity_features(X)
        self.propensity_features = list(prop_X.columns)

        if len(np.unique(r_ind)) >= 2:
            self._is_constant_propensity = False
            self.propensity_pipeline.fit(prop_X, r_ind)
        else:
            self._is_constant_propensity = True
            self._constant_propensity_val = float(np.mean(r_ind)) if len(r_ind) > 0 else 0.95

        y_arr = np.asarray(y, dtype=float)
        valid_train = np.isfinite(y_arr)
        if valid_train.sum() < 10:
            raise ValueError("AdaptiveSAGE requires at least 10 finite training observations")

        X_tr = X[valid_train].reset_index(drop=True)
        y_tr = y_arr[valid_train]
        n_tr = len(y_tr)

        # 2. Strictly causal forward expanding-window out-of-sample residual estimation
        # Order chronologically by timestamp so historical data strictly precedes future data
        if "timestamp" in X_tr.columns:
            order = np.argsort(pd.to_datetime(X_tr["timestamp"]).to_numpy())
        else:
            order = np.arange(n_tr)

        X_tr_sorted = X_tr.iloc[order].reset_index(drop=True)
        y_tr_sorted = y_tr[order]

        # Use forward expanding folds: fold 0 is initial training history;
        # for fold k in 1..K-1, model is trained STRICTLY on folds 0..k-1 (timestamps t < t_eval)
        k_folds = max(2, min(self.config.n_oof_folds, n_tr // 10))
        fold_slices = np.array_split(np.arange(n_tr), k_folds)

        causal_eval_indices_list: list[np.ndarray] = []
        causal_eval_res_list: list[np.ndarray] = []

        for k in range(1, len(fold_slices)):
            train_idx = np.concatenate(fold_slices[:k])
            test_idx = fold_slices[k]
            if len(train_idx) < 5 or len(test_idx) == 0:
                continue
            sub_model = self._make_base_forecaster(cols)
            sub_model.fit(X_tr_sorted.iloc[train_idx][cols], y_tr_sorted[train_idx])
            sub_pred = sub_model.predict(X_tr_sorted.iloc[test_idx][cols])
            e_k = np.abs(y_tr_sorted[test_idx] - sub_pred)
            causal_eval_indices_list.append(test_idx)
            causal_eval_res_list.append(e_k)

        if causal_eval_indices_list:
            causal_idx = np.concatenate(causal_eval_indices_list)
            causal_res = np.concatenate(causal_eval_res_list)
        else:
            causal_idx = np.arange(n_tr)
            causal_res = np.full(n_tr, 1.0, dtype=float)

        self.causal_oof_count = len(causal_res)
        self.causal_oof_median = float(np.median(causal_res)) if len(causal_res) > 0 else 1.0

        # 3. Fit local error scale estimator s(x) strictly on forward causal out-of-sample residuals
        med_oof = self.causal_oof_median
        self.min_scale = max(self.config.min_local_scale, 0.15)
        self.max_scale = max(self.min_scale * 4.0, 6.0 * max(med_oof, 0.5))
        log_targets = np.log(np.clip(causal_res, self.min_scale, self.max_scale))
        self.scale_model.fit(X_tr_sorted.iloc[causal_idx][cols], log_targets)
        self._scale_fitted = True

        # 4. Fit full point forecaster on training split
        self.base_model = self._make_base_forecaster(cols)
        self.base_model.fit(X_tr[cols], y_tr)
        if hasattr(self.base_model, "model") and hasattr(self.base_model.model, "set_params"):
            self.base_model.model.set_params(n_jobs=1)

        # 5. Calibrate normalized conformity & propensity-modulated sensitivity on X_cal
        if X_cal is not None and y_cal is not None:
            self.calibrate(X_cal, y_cal)
        else:
            self.calibrate(X_tr, y_tr)
        return self

    def calibrate(self, X_cal: pd.DataFrame, y_cal: np.ndarray) -> AdaptiveSAGEForecaster:
        """Calibrate weighted normalized residual quantiles across sensitivity grid."""
        y_c = np.asarray(y_cal, dtype=float)
        valid_mask = np.isfinite(y_c)
        if valid_mask.sum() < 2:
            raise ValueError("Calibration set requires at least 2 finite target observations")

        X_c = X_cal[valid_mask].copy()
        y_c = y_c[valid_mask]
        n_cal = len(y_c)

        preds = self.base_model.predict(X_c)
        scales = self._predict_local_scale(X_c)
        norm_residuals = np.abs(y_c - preds) / np.clip(scales, self.min_scale, self.max_scale)

        self.mu_y = float(np.mean(y_c))
        self.sigma_y = float(max(np.std(y_c), 1.0))
        self.sigma_norm_res = float(max(np.std(norm_residuals), 0.25))

        p_obs = self._predict_propensity(X_c)
        g_x = np.log(p_obs / (1.0 - p_obs))
        z = (y_c - self.mu_y) / self.sigma_y

        target_quantile = self.config.nominal_coverage
        finite_sample_q = min(1.0, math.ceil((n_cal + 1) * target_quantile) / n_cal)

        self.scenario_norm_quantiles = {}
        ess_list: list[float] = []
        sorted_gammas = sorted(self.config.gamma_grid)
        prev_q = 0.0

        for gamma in sorted_gammas:
            logit_p = np.clip(g_x + gamma * z, -20.0, 20.0)
            pi_gamma = 1.0 / (1.0 + np.exp(-logit_p))
            weights = 1.0 / np.clip(pi_gamma, 1e-4, 1.0)
            w_sum = np.sum(weights)
            w_norm = weights / w_sum if w_sum > 0 else np.full(n_cal, 1.0 / n_cal)

            n_eff = float(1.0 / np.sum(w_norm**2))
            ess_list.append(n_eff)

            sort_idx = np.argsort(norm_residuals)
            res_sorted = norm_residuals[sort_idx]
            w_sorted = w_norm[sort_idx]
            cum_w = np.cumsum(w_sorted)

            q_idx = min(int(np.searchsorted(cum_w, finite_sample_q)), n_cal - 1)
            q_gamma = max(float(res_sorted[q_idx]), prev_q)
            prev_q = q_gamma
            self.scenario_norm_quantiles[gamma] = q_gamma

        self.effective_sample_size = float(min(ess_list)) if ess_list else float(n_cal)
        self._is_calibrated = True

        # Record calibration disagreement quantiles strictly on X_cal (zero test peeking)
        cal_scenarios = self.predict_all_scenarios(X_c)
        cal_uppers = [hi for _, hi in cal_scenarios.values()]
        cal_disag = np.max(cal_uppers, axis=0) - np.min(cal_uppers, axis=0)
        self.calibrated_disagreement_p90 = float(np.quantile(cal_disag, 0.90))
        self.calibrated_disagreement_p95 = float(np.quantile(cal_disag, 0.95))

        thr = self.config.max_disagreement_threshold
        if thr == "cal_p95":
            self.resolved_disagreement_threshold = max(self.calibrated_disagreement_p95, 1e-6)
        elif thr == "cal_p90":
            self.resolved_disagreement_threshold = max(self.calibrated_disagreement_p90, 1e-6)
        else:
            self.resolved_disagreement_threshold = float(thr)

        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.feature_columns is None:
            raise RuntimeError("Forecaster must be fit before calling predict")
        return self.base_model.predict(X[self.feature_columns])

    def predict_all_scenarios(self, X: pd.DataFrame) -> dict[float, tuple[np.ndarray, np.ndarray]]:
        """Generate input-dependent [lower, upper] bounds across all gamma scenarios."""
        if not self._is_calibrated:
            raise RuntimeError("Forecaster must be fit/calibrated before predicting")

        pred = self.predict(X)
        scales = self._predict_local_scale(X)
        p_obs = self._predict_propensity(X)
        risk_mult = 1.0 + self.config.propensity_risk_weight * (1.0 - p_obs)

        res: dict[float, tuple[np.ndarray, np.ndarray]] = {}
        prev_hw = np.zeros(len(X), dtype=float)
        for gamma in sorted(self.config.gamma_grid):
            q_gamma = self.scenario_norm_quantiles[gamma]
            shift_x = gamma * (scales / 2.0) * risk_mult
            hw = np.maximum(scales * q_gamma + shift_x, prev_hw)
            prev_hw = hw
            lower = np.clip(pred - hw, 0.0, None)
            upper = pred + hw
            res[gamma] = (lower, upper)
        return res

    def predict_interval(
        self, X: pd.DataFrame, nominal_coverage: float = 0.90
    ) -> tuple[np.ndarray, np.ndarray]:
        res = self.predict_guarded(X)
        return res.lower_envelope, res.upper_envelope

    def predict_guarded(self, X: pd.DataFrame) -> SAGEResult:
        """Generate full input-dependent guarded forecast and decision flags."""
        if not self._is_calibrated:
            raise RuntimeError("Forecaster must be fit/calibrated before predicting")

        point_pred = self.predict(X)
        scenarios = self.predict_all_scenarios(X)

        lowers = [lo for lo, _ in scenarios.values()]
        uppers = [hi for _, hi in scenarios.values()]
        lower_envelope = np.min(lowers, axis=0)
        upper_envelope = np.max(uppers, axis=0)

        scenario_disagreement = np.max(uppers, axis=0) - np.min(uppers, axis=0)
        envelope_width = upper_envelope - lower_envelope

        if "age_hours" in X.columns:
            age_hours = pd.to_numeric(X["age_hours"], errors="coerce").fillna(0.0).to_numpy()
        else:
            age_hours = np.zeros(len(X), dtype=float)

        statuses, reasons = guarded_decision_rule(
            scenario_disagreement=scenario_disagreement,
            effective_sample_size=self.effective_sample_size,
            age_hours=age_hours,
            max_disagreement_threshold=self.resolved_disagreement_threshold,
            min_effective_sample_size=self.config.min_effective_sample_size,
            stale_age_hours=self.config.stale_age_hours,
            caution_age_hours=self.config.caution_age_hours,
            caution_disagreement_ratio=self.config.caution_disagreement_ratio,
            envelope_width=envelope_width,
            max_envelope_width_threshold=self.config.max_envelope_width_threshold,
        )

        return SAGEResult(
            point_forecast=point_pred,
            lower_envelope=lower_envelope,
            upper_envelope=upper_envelope,
            scenario_disagreement=scenario_disagreement,
            status=np.asarray(statuses, dtype=object),
            reason=np.asarray(reasons, dtype=object),
            effective_sample_size=self.effective_sample_size,
            gamma_intervals=scenarios,
            envelope_width=envelope_width,
        )


def compute_wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Compute exact 95% Wilson score confidence interval for binomial coverage."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + (z**2) / n
    center = (p + (z**2) / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p) + (z**2) / (4.0 * n)) / n)
    return (max(0.0, center - half), min(1.0, center + half))


def masked_observation_aci_intervals(
    y_cal: np.ndarray,
    pred_cal: np.ndarray,
    y_test: np.ndarray,
    pred_test: np.ndarray,
    reported_test: np.ndarray,
    target_alpha: float = 0.10,
    gamma_step: float = 0.05,
    window: int = 168,
) -> tuple[np.ndarray, np.ndarray]:
    """Online missingness-aware ACI baseline updating alpha_t ONLY when R_t = 1.

    Uses a bounded quantile trajectory (trailing calibration/observed residuals)
    to contrast observation-gated online updates against static-grid ACI (Gibbs & Candes 2021).
    Only strictly past reported test labels (t' < t, R_{t'} = 1) inform time t.
    """
    hist_res = list(np.abs(y_cal - pred_cal)[-window:])
    alpha_t = target_alpha
    n = len(pred_test)
    lowers = np.zeros(n, dtype=float)
    uppers = np.zeros(n, dtype=float)

    for i in range(n):
        q_level = float(np.clip(1.0 - alpha_t, 0.50, 0.99))
        q_val = float(np.quantile(hist_res[-window:], q_level)) if hist_res else 5.0
        lo = max(0.0, float(pred_test[i]) - q_val)
        hi = float(pred_test[i]) + q_val
        lowers[i] = lo
        uppers[i] = hi

        # Update online state ONLY if true observation at t is actually reported (R_t = 1)
        if bool(reported_test[i]) and np.isfinite(y_test[i]):
            err_t = 0.0 if (lo <= float(y_test[i]) <= hi) else 1.0
            alpha_t = float(np.clip(alpha_t + gamma_step * (target_alpha - err_t), 0.01, 0.45))
            hist_res.append(abs(float(y_test[i]) - float(pred_test[i])))

    return lowers, uppers
