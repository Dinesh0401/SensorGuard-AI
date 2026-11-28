"""Forecasting models and baseline implementations for SensorGuard AI."""

from __future__ import annotations

from sensorguard.models.forecasters import (
    BaseForecaster,
    LightGBMPointForecaster,
    LightGBMQuantileForecaster,
    PersistenceForecaster,
    RidgeForecaster,
    SeasonalNaiveForecaster,
    SpatialNeighborForecaster,
)
from sensorguard.models.manifest import ModelManifest
from sensorguard.models.sage import SAGEConfig, SAGEForecaster, SAGEResult, guarded_decision_rule

__all__ = [
    "BaseForecaster",
    "LightGBMPointForecaster",
    "LightGBMQuantileForecaster",
    "ModelManifest",
    "PersistenceForecaster",
    "RidgeForecaster",
    "SAGEConfig",
    "SAGEForecaster",
    "SAGEResult",
    "SeasonalNaiveForecaster",
    "SpatialNeighborForecaster",
    "guarded_decision_rule",
]
