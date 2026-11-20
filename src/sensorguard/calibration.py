"""Explicit split-conformal calibration; validity assumes exchangeability."""

from __future__ import annotations

import math

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


def policy(age_hours: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Heuristic safety policy for the synthetic proof of concept.

    It does not establish calibrated coverage under missingness. Long gaps are
    explicitly abstained from rather than silently presented as confident.
    """
    ages = np.asarray(age_hours, dtype=float)
    statuses = np.where(ages > 12, "abstain", np.where(ages > 3, "caution", "publish"))
    reasons = np.where(
        ages > 12,
        "stale_sensor_history",
        np.where(ages > 3, "reduced_sensor_freshness", "sufficient_freshness"),
    )
    return statuses, reasons
