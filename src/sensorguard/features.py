"""Point-in-time-safe feature construction from available sensor observations."""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = ("last_pm25", "age_hours", "last_wind", "last_humidity", "hour_sin", "hour_cos")


def build_features(observed: pd.DataFrame) -> pd.DataFrame:
    """Build one-step-ahead features, using ONLY observations before target time.

    Each row has a future target timestamp; weather and pollutant covariates are
    shifted one row per station, preventing target-time observation leakage.
    """
    required = {"station_id", "timestamp", "pm25", "wind_speed", "humidity", "reported"}
    if not required.issubset(observed):
        raise ValueError(f"missing observation fields: {sorted(required - set(observed))}")
    df = observed.sort_values(["station_id", "timestamp"]).reset_index(drop=True).copy()
    if df.duplicated(["station_id", "timestamp"]).any():
        raise ValueError("duplicate station/time key")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if not (df.groupby("station_id")["timestamp"].diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError("expected contiguous hourly station timelines; reindex before featurization")
    group = df.groupby("station_id", sort=False)
    last_observed = df["pm25"].where(df["reported"])
    df["last_pm25"] = last_observed.groupby(df["station_id"]).ffill().groupby(df["station_id"]).shift(1)
    previous_time = df["timestamp"].where(df["reported"])
    previous_time = previous_time.groupby(df["station_id"]).ffill().groupby(df["station_id"]).shift(1)
    df["age_hours"] = (df["timestamp"] - previous_time).dt.total_seconds() / 3600
    df["last_wind"] = group["wind_speed"].shift(1)
    df["last_humidity"] = group["humidity"].shift(1)
    df["hour_sin"] = np.sin(2 * np.pi * df["timestamp"].dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["timestamp"].dt.hour / 24)
    # Expose neither current target value nor simulator truth to the estimator.
    return (
        df[["station_id", "timestamp", "reported", *FEATURE_COLUMNS]]
        .dropna(subset=["last_pm25"])
        .reset_index(drop=True)
    )
