"""Baseline and Machine Learning Forecasters for SensorGuard AI.

Implements point and interval forecasters for +1-hour ahead PM2.5:
- B0a: Persistence (last observation carried forward)
- B0b: Seasonal Naive (24-hour diurnal lag)
- B1: Spatial Neighbor (inverse-distance-weighted spatial interpolation)
- B2a: Ridge Regression (with point-in-time temporal and spatial features)
- B2b: LightGBM Point Forecaster (gradient boosted decision trees)
- B2c: LightGBM Quantile Forecaster (pinball loss quantile regression)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler


class BaseForecaster(ABC):
    """Abstract base forecaster interface."""

    @abstractmethod
    def fit(self, X: pd.DataFrame, y: np.ndarray) -> BaseForecaster:
        """Fit forecaster parameters on historical training observations."""
        ...

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Generate point forecasts for target time (one step ahead)."""
        ...

    def predict_interval(self, X: pd.DataFrame, nominal_coverage: float = 0.90) -> tuple[np.ndarray, np.ndarray]:
        """Generate prediction interval [lower, upper].

        Default implementation raises NotImplementedError if the model
        does not natively generate quantile/interval predictions.
        """
        raise NotImplementedError("Interval forecasting not natively supported; use ConformalCalibrator.")


class PersistenceForecaster(BaseForecaster):
    """B0a: Predicts last available reading y_t for target y_{t+1}."""

    def __init__(self, feature_col: str = "last_pm25") -> None:
        self.feature_col = feature_col

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> PersistenceForecaster:
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.feature_col not in X.columns:
            raise ValueError(f"Feature column '{self.feature_col}' not found in X")
        return np.asarray(X[self.feature_col].to_numpy(), dtype=float)


class SeasonalNaiveForecaster(BaseForecaster):
    """B0b: Predicts reading from 24 hours prior (y_{t-23}), falling back to y_t."""

    def __init__(self, lag_24_col: str = "lag_24h", fallback_col: str = "last_pm25") -> None:
        self.lag_24_col = lag_24_col
        self.fallback_col = fallback_col

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> SeasonalNaiveForecaster:
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.lag_24_col in X.columns:
            val = X[self.lag_24_col].to_numpy()
            fallback = X[self.fallback_col].to_numpy() if self.fallback_col in X.columns else val
            return np.where(np.isfinite(val) & (val > 0), val, fallback)
        if self.fallback_col in X.columns:
            return np.asarray(X[self.fallback_col].to_numpy(), dtype=float)
        raise ValueError(f"Neither '{self.lag_24_col}' nor '{self.fallback_col}' found in X")


class SpatialNeighborForecaster(BaseForecaster):
    """B1: Predicts target using inverse-distance-weighted interpolation of active neighbors."""

    def __init__(self, neighbor_col: str = "neighbor_idw_pm25", fallback_col: str = "last_pm25") -> None:
        self.neighbor_col = neighbor_col
        self.fallback_col = fallback_col

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> SpatialNeighborForecaster:
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.neighbor_col in X.columns:
            val = X[self.neighbor_col].to_numpy()
            fallback = X[self.fallback_col].to_numpy() if self.fallback_col in X.columns else val
            return np.where(np.isfinite(val), val, fallback)
        if self.fallback_col in X.columns:
            return np.asarray(X[self.fallback_col].to_numpy(), dtype=float)
        raise ValueError(f"Neither '{self.neighbor_col}' nor '{self.fallback_col}' found in X")


class RidgeForecaster(BaseForecaster):
    """B2a: Regularized linear regression over standardized features with median imputation."""

    def __init__(self, feature_columns: list[str] | None = None, alpha: float = 1.0) -> None:
        self.feature_columns = feature_columns
        self.alpha = alpha
        self.pipeline: Pipeline | None = None

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> RidgeForecaster:
        cols = self.feature_columns or [c for c in X.columns if c not in {"target", "station_id", "timestamp"}]
        self.feature_columns = cols
        feat_df = X[cols].copy()

        self.pipeline = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            Ridge(alpha=self.alpha, random_state=42),
        )
        self.pipeline.fit(feat_df, y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.pipeline is None or self.feature_columns is None:
            raise RuntimeError("Forecaster must be fit before calling predict")
        feat_df = X[self.feature_columns].copy()
        pred = self.pipeline.predict(feat_df)
        return np.clip(pred, 0.0, None)


class LightGBMPointForecaster(BaseForecaster):
    """B2b: Gradient boosted decision trees for point forecasting with MAE (L1) loss."""

    def __init__(
        self,
        feature_columns: list[str] | None = None,
        n_estimators: int = 100,
        learning_rate: float = 0.05,
        num_leaves: int = 31,
        random_state: int = 42,
    ) -> None:
        self.feature_columns = feature_columns
        self.params: dict[str, Any] = {
            "objective": "regression_l1",
            "n_estimators": n_estimators,
            "learning_rate": learning_rate,
            "num_leaves": num_leaves,
            "random_state": random_state,
            "verbose": -1,
            "n_jobs": -1,
        }
        self.model: LGBMRegressor | None = None

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> LightGBMPointForecaster:
        cols = self.feature_columns or [c for c in X.columns if c not in {"target", "station_id", "timestamp"}]
        self.feature_columns = cols
        feat_df = X[cols].copy()

        self.model = LGBMRegressor(**self.params)
        self.model.fit(feat_df, y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.model is None or self.feature_columns is None:
            raise RuntimeError("Forecaster must be fit before calling predict")
        feat_df = X[self.feature_columns].copy()
        pred = self.model.predict(feat_df)
        return np.clip(pred, 0.0, None)


class LightGBMQuantileForecaster(BaseForecaster):
    """B2c: Pinball loss quantile forecaster for interval prediction."""

    def __init__(
        self,
        feature_columns: list[str] | None = None,
        quantiles: tuple[float, float, float] = (0.05, 0.50, 0.95),
        n_estimators: int = 100,
        learning_rate: float = 0.05,
        num_leaves: int = 31,
        random_state: int = 42,
    ) -> None:
        self.feature_columns = feature_columns
        self.quantiles = quantiles
        self.params = {
            "n_estimators": n_estimators,
            "learning_rate": learning_rate,
            "num_leaves": num_leaves,
            "random_state": random_state,
            "verbose": -1,
            "n_jobs": -1,
        }
        self.models: dict[float, LGBMRegressor] = {}

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> LightGBMQuantileForecaster:
        cols = self.feature_columns or [c for c in X.columns if c not in {"target", "station_id", "timestamp"}]
        self.feature_columns = cols
        feat_df = X[cols].copy()

        for q in self.quantiles:
            model = LGBMRegressor(objective="quantile", alpha=q, **self.params)
            model.fit(feat_df, y)
            self.models[q] = model
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Median quantile (0.50) point prediction."""
        if 0.50 in self.models:
            pred = self.models[0.50].predict(X[self.feature_columns])
            return np.clip(pred, 0.0, None)
        raise RuntimeError("Median model (0.50) not fitted")

    def predict_quantiles(self, X: pd.DataFrame) -> dict[float, np.ndarray]:
        """Predict all fitted quantiles."""
        if self.feature_columns is None or not self.models:
            raise RuntimeError("Forecaster must be fit before calling predict_quantiles")
        feat_df = X[self.feature_columns]
        return {q: np.clip(model.predict(feat_df), 0.0, None) for q, model in self.models.items()}

    def predict_interval(self, X: pd.DataFrame, nominal_coverage: float = 0.90) -> tuple[np.ndarray, np.ndarray]:
        """Uncalibrated pinball prediction interval."""
        q_low = round((1.0 - nominal_coverage) / 2.0, 3)
        q_high = round(1.0 - q_low, 3)
        if q_low in self.models and q_high in self.models:
            lower = np.clip(self.models[q_low].predict(X[self.feature_columns]), 0.0, None)
            upper = np.clip(self.models[q_high].predict(X[self.feature_columns]), 0.0, None)
            # Enforce monotonicity
            upper = np.maximum(upper, lower)
            return lower, upper
        raise ValueError(f"Models for quantiles {q_low} and {q_high} not fitted")
