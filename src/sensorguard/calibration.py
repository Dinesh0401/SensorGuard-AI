"""Conformal prediction, interval calibration, and safety policies.

Implements rigorous uncertainty quantification methods:
- Split conformal prediction (absolute residual calibration)
- Conformalized Quantile Regression (CQR, Romano et al. 2019)
- Rolling conformal prediction (sliding-window adaptive recalibration)
- Adaptive Conformal Inference (ACI, Gibbs & Candès 2021)
- Winkler interval scoring and multi-stratum coverage gap diagnostics
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


def conformal_radius(y_true: np.ndarray, y_pred: np.ndarray, alpha: float = 0.1) -> float:
    """Finite-sample split conformal radius for absolute residuals.

    This standard result relies on exchangeability; no unconditional guarantee
    holds for unknown MNAR processes or arbitrarily shifting data streams.
    """
    y, p = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    if y.shape != p.shape or y.ndim != 1 or len(y) < 2:
        raise ValueError("matching one-dimensional calibration arrays required")
    if not (0 < alpha < 1) or not (np.isfinite(y).all() and np.isfinite(p).all()):
        raise ValueError("finite values and 0 < alpha < 1 required")
    n = len(y)
    k = math.ceil((n + 1) * (1 - alpha))
    if k > n:
        return math.inf
    return float(np.partition(np.abs(y - p), k - 1)[k - 1])


@dataclass
class ConformalInterval:
    """Output prediction interval with bounds, width, and coverage indicator."""

    lower: np.ndarray
    upper: np.ndarray
    nominal_coverage: float

    @property
    def width(self) -> np.ndarray:
        return np.maximum(0.0, self.upper - self.lower)

    def contains(self, y_true: np.ndarray) -> np.ndarray:
        return (y_true >= self.lower) & (y_true <= self.upper)


class SplitConformalCalibrator:
    """Split conformal prediction calibrator for point forecasters."""

    def __init__(self, alpha: float = 0.10) -> None:
        self.alpha = alpha
        self.radius: float | None = None

    def fit(self, y_cal: np.ndarray, y_pred_cal: np.ndarray) -> SplitConformalCalibrator:
        self.radius = conformal_radius(y_cal, y_pred_cal, alpha=self.alpha)
        return self

    def predict_interval(self, y_pred: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.radius is None:
            raise RuntimeError("Calibrator must be fit before calling predict_interval")
        p = np.asarray(y_pred, dtype=float)
        lower = np.clip(p - self.radius, 0.0, None)
        upper = p + self.radius
        return lower, upper


class ConformalizedQuantileRegression:
    """Conformalized Quantile Regression (CQR, Romano et al. 2019).

    Uses pinball quantile estimates [q_low, q_high] and calibrates
    conformity scores E_i = max(q_low - y, y - q_high) to achieve exact
    finite-sample marginal coverage under exchangeability.
    """

    def __init__(self, alpha: float = 0.10) -> None:
        self.alpha = alpha
        self.q_adjustment: float | None = None

    def fit(self, y_cal: np.ndarray, q_low_cal: np.ndarray, q_high_cal: np.ndarray) -> ConformalizedQuantileRegression:
        y = np.asarray(y_cal, dtype=float)
        ql = np.asarray(q_low_cal, dtype=float)
        qh = np.asarray(q_high_cal, dtype=float)

        if len(y) < 2 or not (len(y) == len(ql) == len(qh)):
            raise ValueError("Matching 1D arrays with length >= 2 required")

        # Conformity score: distance outside the predicted interval
        scores = np.maximum(ql - y, y - qh)
        n = len(scores)
        k = math.ceil((n + 1) * (1 - self.alpha))
        if k > n:
            self.q_adjustment = math.inf
        else:
            self.q_adjustment = float(np.partition(scores, k - 1)[k - 1])
        return self

    def predict_interval(self, q_low: np.ndarray, q_high: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.q_adjustment is None:
            raise RuntimeError("CQR calibrator must be fit before calling predict_interval")
        ql = np.asarray(q_low, dtype=float)
        qh = np.asarray(q_high, dtype=float)

        lower = np.clip(ql - self.q_adjustment, 0.0, None)
        upper = qh + self.q_adjustment
        # Guarantee non-crossing
        upper = np.maximum(upper, lower)
        return lower, upper


class RollingConformalCalibrator:
    """Rolling conformal calibrator that dynamically updates radius over trailing window."""

    def __init__(self, window_size: int = 168, alpha: float = 0.10) -> None:
        self.window_size = window_size
        self.alpha = alpha
        self.residuals_buffer: list[float] = []

    def update(self, y_true: float, y_pred: float) -> None:
        if np.isfinite(y_true) and np.isfinite(y_pred):
            res = abs(y_true - y_pred)
            self.residuals_buffer.append(res)
            if len(self.residuals_buffer) > self.window_size:
                self.residuals_buffer.pop(0)

    def current_radius(self) -> float:
        if len(self.residuals_buffer) < 10:
            return math.inf
        n = len(self.residuals_buffer)
        k = math.ceil((n + 1) * (1 - self.alpha))
        if k > n:
            return math.inf
        return float(np.partition(self.residuals_buffer, k - 1)[k - 1])

    def predict_interval(self, y_pred: float) -> tuple[float, float]:
        r = self.current_radius()
        if math.isinf(r):
            return 0.0, math.inf
        return max(0.0, y_pred - r), y_pred + r


class AdaptiveConformalInference:
    """Adaptive Conformal Inference (ACI, Gibbs & Candès 2021).

    Dynamically adjusts nominal significance level alpha_t to maintain
    long-run empirical coverage under arbitrary non-exchangeable shift.
    alpha_{t+1} = alpha_t + gamma * (alpha - err_t).
    """

    def __init__(self, target_alpha: float = 0.10, gamma: float = 0.01) -> None:
        self.target_alpha = target_alpha
        self.gamma = gamma
        self.current_alpha = target_alpha

    def step(self, covered: bool) -> float:
        err_t = 0.0 if covered else 1.0
        # Update rule: if uncovered (err=1), decrease alpha (widen interval)
        # if covered (err=0), increase alpha (tighten interval)
        self.current_alpha = float(
            np.clip(self.current_alpha + self.gamma * (self.target_alpha - err_t), 0.001, 0.999)
        )
        return self.current_alpha


def winkler_score(lower: np.ndarray, upper: np.ndarray, y_true: np.ndarray, alpha: float = 0.10) -> float:
    """Winkler Interval Score for evaluating prediction intervals.

    Penalizes both interval width and observations falling outside the bounds.
    Score = (u - l) + (2/alpha)*(l - y)*I(y < l) + (2/alpha)*(y - u)*I(y > u).
    Lower score indicates sharper, well-calibrated intervals.
    """
    l_arr = np.asarray(lower, dtype=float)
    u_arr = np.asarray(upper, dtype=float)
    y_arr = np.asarray(y_true, dtype=float)

    width = np.maximum(0.0, u_arr - l_arr)
    under_penalty = np.where(y_arr < l_arr, (2.0 / alpha) * (l_arr - y_arr), 0.0)
    over_penalty = np.where(y_arr > u_arr, (2.0 / alpha) * (y_arr - u_arr), 0.0)

    scores = width + under_penalty + over_penalty
    return float(np.mean(scores))


def compute_interval_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    alpha: float = 0.10,
    strata_labels: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Compute comprehensive accuracy, calibration, sharpness, and group fairness metrics."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    l_arr = np.asarray(lower, dtype=float)
    u_arr = np.asarray(upper, dtype=float)

    valid_mask = np.isfinite(y) & np.isfinite(p) & np.isfinite(l_arr) & np.isfinite(u_arr)
    if not valid_mask.any():
        return {
            "n_samples": 0,
            "mae": None,
            "rmse": None,
            "picp": None,
            "mpiw": None,
            "winkler_score": None,
            "worst_stratum_coverage_gap": None,
        }

    y_val = y[valid_mask]
    p_val = p[valid_mask]
    l_val = l_arr[valid_mask]
    u_val = u_arr[valid_mask]

    covered = (y_val >= l_val) & (y_val <= u_val)
    picp = float(covered.mean())
    nominal_coverage = 1.0 - alpha

    mae = float(np.mean(np.abs(y_val - p_val)))
    rmse = float(np.sqrt(np.mean((y_val - p_val) ** 2)))
    mpiw = float(np.mean(u_val - l_val))
    winkler = winkler_score(l_val, u_val, y_val, alpha=alpha)

    group_metrics: dict[str, dict[str, Any]] = {}
    worst_gap = 0.0

    if strata_labels:
        for group_name, labels in strata_labels.items():
            labels_val = np.asarray(labels)[valid_mask]
            group_metrics[group_name] = {}
            for unique_label in np.unique(labels_val):
                g_mask = labels_val == unique_label
                if g_mask.sum() >= 5:
                    g_cov = float(covered[g_mask].mean())
                    g_gap = abs(g_cov - nominal_coverage)
                    worst_gap = max(worst_gap, g_gap)
                    group_metrics[group_name][str(unique_label)] = {
                        "count": int(g_mask.sum()),
                        "picp": round(g_cov, 4),
                        "gap": round(g_gap, 4),
                        "mpiw": round(float(np.mean(u_val[g_mask] - l_val[g_mask])), 3),
                    }

    return {
        "n_samples": int(valid_mask.sum()),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "picp": round(picp, 4),
        "coverage_gap": round(abs(picp - nominal_coverage), 4),
        "mpiw": round(mpiw, 4),
        "winkler_score": round(winkler, 4),
        "worst_stratum_coverage_gap": round(worst_gap, 4),
        "strata": group_metrics,
    }


def policy(age_hours: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Safety abstention policy for sensor freshness."""
    ages = np.asarray(age_hours, dtype=float)
    statuses = np.where(ages > 12, "abstain", np.where(ages > 3, "caution", "publish"))
    reasons = np.where(
        ages > 12,
        "stale_sensor_history",
        np.where(ages > 3, "reduced_sensor_freshness", "sufficient_freshness"),
    )
    return statuses, reasons
