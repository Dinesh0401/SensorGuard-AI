"""Data loading, spatiotemporal feature engineering, and leak-free splitting.

Constructs point-in-time spatial and temporal features for environmental sensor networks.
Enforces strict chronological train/calibration/test boundaries and separate
evaluator-only ground truth for outage simulation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from sensorguard.db.schema import Observation, Sensor, Station

EARTH_RADIUS_KM = 6371.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two geographic coordinates in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


@dataclass
class DatasetSplits:
    """Chronologically partitioned datasets for reproducible MLOps."""

    train: pd.DataFrame
    calibrate: pd.DataFrame
    test: pd.DataFrame
    station_coords: dict[str, tuple[float, float]]
    feature_columns: list[str]
    target_column: str = "target"


def load_pilot_dataset_from_db(session: Session) -> tuple[pd.DataFrame, dict[str, tuple[float, float]]]:
    """Load Q1 2024 PM2.5 observations and station coordinates from database."""
    stations = session.scalars(select(Station)).all()
    station_coords = {str(s.id): (s.latitude, s.longitude) for s in stations}

    stmt = (
        select(
            Station.id.label("station_id"),
            Observation.observed_at.label("timestamp"),
            Observation.value.label("pm25"),
        )
        .join(Sensor, Sensor.id == Observation.sensor_id)
        .join(Station, Station.id == Sensor.station_id)
        .where(Sensor.parameter == "pm25")
        .where(Observation.quality_flag == "VALID")
        .order_by(Station.id, Observation.observed_at)
    )

    rows = session.execute(stmt).all()
    if not rows:
        raise ValueError("No valid PM2.5 observations found in database")

    df = pd.DataFrame(rows, columns=["station_id", "timestamp", "pm25"])
    df["station_id"] = df["station_id"].astype(str)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["pm25"] = df["pm25"].astype(float)
    df["reported"] = True

    return df, station_coords


def build_spatiotemporal_features(
    df: pd.DataFrame,
    station_coords: dict[str, tuple[float, float]],
    idw_power: float = 2.0,
) -> pd.DataFrame:
    """Build point-in-time features for predicting target at t+1 from observations <= t.

    Features include:
    - Temporal: last_pm25 (y_t), lag_1h (y_{t-1}), lag_2h (y_{t-2}), lag_24h (y_{t-23})
    - Rolling stats: rolling_mean_6h, rolling_std_6h, rolling_mean_24h
    - Freshness: age_hours (hours since last available observation)
    - Spatial: neighbor_idw_pm25 (inverse-distance weighted average of neighboring stations)
    - Cyclic time harmonics: hour_sin, hour_cos, dow_sin, dow_cos
    - Target: true pm25 at target timestamp (t+1)
    """
    if df.empty:
        raise ValueError("Input dataframe is empty")

    data = df.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)

    # Reindex each station onto continuous hourly grid
    all_stations = sorted(data["station_id"].unique())
    min_time = data["timestamp"].min().floor("h")
    max_time = data["timestamp"].max().ceil("h")
    full_idx = pd.date_range(start=min_time, end=max_time, freq="1h", tz=UTC)

    station_frames = []
    for s_id in all_stations:
        s_data = data[data["station_id"] == s_id].drop_duplicates("timestamp").set_index("timestamp")
        s_reindexed = s_data.reindex(full_idx)
        s_reindexed["station_id"] = s_id
        s_reindexed["reported"] = s_reindexed["reported"].infer_objects(copy=False).fillna(False).astype(bool)
        s_reindexed = s_reindexed.reset_index().rename(columns={"index": "timestamp"})
        station_frames.append(s_reindexed)

    grid_df = pd.concat(station_frames, ignore_index=True)

    # Temporal feature construction per station
    feature_frames = []
    for s_id, group in grid_df.groupby("station_id", sort=False):
        grp = group.sort_values("timestamp").reset_index(drop=True).copy()

        # Target is PM2.5 at current row
        grp["target"] = grp["pm25"]

        # Visible PM2.5 carried forward strictly from prior steps (shift(1) prevents lookahead)
        visible_series = grp["pm25"].where(grp["reported"])
        last_val = visible_series.ffill().shift(1)
        grp["last_pm25"] = last_val

        # Lags relative to t (shift(2) is t-1, shift(3) is t-2, shift(24) is t-23)
        grp["lag_1h"] = visible_series.ffill().shift(2)
        grp["lag_2h"] = visible_series.ffill().shift(3)
        grp["lag_24h"] = visible_series.ffill().shift(24)

        # Rolling statistics strictly over past visible values
        grp["rolling_mean_6h"] = last_val.rolling(6, min_periods=1).mean()
        grp["rolling_std_6h"] = last_val.rolling(6, min_periods=2).std().fillna(0.0)
        grp["rolling_mean_24h"] = last_val.rolling(24, min_periods=1).mean()

        # Age of last observation in hours
        visible_time = grp["timestamp"].where(grp["reported"])
        last_time = visible_time.ffill().shift(1)
        grp["age_hours"] = (grp["timestamp"] - last_time).dt.total_seconds() / 3600.0

        feature_frames.append(grp)

    featured_df = pd.concat(feature_frames, ignore_index=True)

    # Spatial neighbor feature computation at each timestamp slice
    # Computes inverse-distance weighted average of all other stations reporting at <= t
    featured_df["neighbor_idw_pm25"] = np.nan

    # Precompute pairwise distances
    coords = station_coords
    dist_matrix: dict[tuple[str, str], float] = {}
    for s1 in all_stations:
        for s2 in all_stations:
            if s1 != s2 and s1 in coords and s2 in coords:
                d = haversine_distance(coords[s1][0], coords[s1][1], coords[s2][0], coords[s2][1])
                dist_matrix[(s1, s2)] = max(d, 0.5)  # floor at 0.5 km

    # Compute spatial neighbor interpolation per timestamp
    pivoted_last = featured_df.pivot(index="timestamp", columns="station_id", values="last_pm25")
    pivoted_age = featured_df.pivot(index="timestamp", columns="station_id", values="age_hours")

    idw_values = []
    for row in featured_df.itertuples():
        t = row.timestamp
        s_id = row.station_id
        # Candidate neighbors that have fresh data (age <= 3h)
        weights = []
        vals = []
        for other_id in all_stations:
            if other_id != s_id and (s_id, other_id) in dist_matrix:
                other_val = pivoted_last.at[t, other_id]
                other_age = pivoted_age.at[t, other_id]
                if pd.notna(other_val) and pd.notna(other_age) and other_age <= 3.0:
                    d = dist_matrix[(s_id, other_id)]
                    w = 1.0 / (d**idw_power)
                    weights.append(w)
                    vals.append(other_val)

        if weights:
            idw = sum(w * v for w, v in zip(weights, vals)) / sum(weights)
        else:
            idw = row.last_pm25
        idw_values.append(idw)

    featured_df["neighbor_idw_pm25"] = idw_values

    # Cyclic time harmonics
    featured_df["hour_sin"] = np.sin(2.0 * np.pi * featured_df["timestamp"].dt.hour / 24.0)
    featured_df["hour_cos"] = np.cos(2.0 * np.pi * featured_df["timestamp"].dt.hour / 24.0)
    featured_df["dow_sin"] = np.sin(2.0 * np.pi * featured_df["timestamp"].dt.dayofweek / 7.0)
    featured_df["dow_cos"] = np.cos(2.0 * np.pi * featured_df["timestamp"].dt.dayofweek / 7.0)

    # Filter out initial warmup hours where last_pm25 is unavailable
    clean_df = featured_df.dropna(subset=["last_pm25"]).reset_index(drop=True)
    return clean_df


def partition_dataset(
    df: pd.DataFrame,
    station_coords: dict[str, tuple[float, float]],
    train_pct: float = 0.60,
    cal_pct: float = 0.20,
) -> DatasetSplits:
    """Split dataset chronologically into train, calibration, and test sets."""
    unique_ts = np.sort(df["timestamp"].unique())
    n = len(unique_ts)

    idx_train_end = int(n * train_pct)
    idx_cal_end = int(n * (train_pct + cal_pct))

    t_train_end = unique_ts[idx_train_end]
    t_cal_end = unique_ts[idx_cal_end]

    # Training and calibration use observed labels that would have arrived
    train_mask = (df["timestamp"] < t_train_end) & df["reported"]
    cal_mask = (df["timestamp"] >= t_train_end) & (df["timestamp"] < t_cal_end) & df["reported"]
    # Test set includes all ground truth for evaluator-only scoring
    test_mask = df["timestamp"] >= t_cal_end

    feature_cols = [
        "last_pm25",
        "lag_1h",
        "lag_2h",
        "lag_24h",
        "rolling_mean_6h",
        "rolling_std_6h",
        "rolling_mean_24h",
        "neighbor_idw_pm25",
        "age_hours",
        "hour_sin",
        "hour_cos",
        "dow_sin",
        "dow_cos",
    ]

    return DatasetSplits(
        train=df[train_mask].reset_index(drop=True),
        calibrate=df[cal_mask].reset_index(drop=True),
        test=df[test_mask].reset_index(drop=True),
        station_coords=station_coords,
        feature_columns=feature_cols,
    )
