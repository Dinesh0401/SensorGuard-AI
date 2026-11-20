"""Deterministic synthetic sensor data and evaluator-only outage injection.

All hidden targets are returned separately from observed inputs. Never pass truth
into model feature generation. This module is for research simulations, not live data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class OutageConfig:
    """Controls a reproducible failure mechanism.

    ``gamma`` controls dependence on the hidden pollutant (MNAR only). A fitted
    gamma is not identifiable from missing observations without assumptions.
    """

    mechanism: str = "mcar"
    missing_rate: float = 0.2
    gamma: float = 1.5
    seed: int = 17
    burst_hours: int = 24

    def __post_init__(self) -> None:
        if self.mechanism not in {"mcar", "mar", "mnar", "burst"}:
            raise ValueError("mechanism must be mcar, mar, mnar, or burst")
        if not 0 <= self.missing_rate < 1:
            raise ValueError("missing_rate must be in [0, 1)")
        if self.burst_hours < 1:
            raise ValueError("burst_hours must be positive")


def generate_network(days: int = 120, stations: int = 6, seed: int = 11) -> pd.DataFrame:
    """Generate time-aligned hourly *synthetic* station readings.

    Values and physical associations are illustrative, not scientifically
    validated measurements or representative air-quality simulations.
    """
    if days < 4 or stations < 1:
        raise ValueError("days must be >= 4 and stations must be >= 1")
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2025-01-01", periods=days * 24, freq="h", tz="UTC")
    hour = np.arange(len(timestamps))
    daily = 7 * np.sin(2 * np.pi * hour / 24)
    weekly = 4 * np.cos(2 * np.pi * hour / (24 * 7))
    episode = 34 * np.exp(-0.5 * ((hour - len(hour) * 0.73) / 30) ** 2)
    common = 30 + daily + weekly + episode
    frames: list[pd.DataFrame] = []
    for station in range(stations):
        noise = rng.normal(0, 3.2, size=len(hour))
        wind = np.clip(4 + 2 * np.sin(2 * np.pi * hour / 48) + rng.normal(0, 0.7, len(hour)), 0, None)
        humidity = np.clip(55 + 12 * np.cos(2 * np.pi * hour / 24) + rng.normal(0, 3, len(hour)), 0, 100)
        pm25 = np.clip(common + station * 2 - wind + noise, 0, None)
        frames.append(pd.DataFrame({
            "station_id": f"synthetic-{station + 1:02d}",
            "timestamp": timestamps,
            "pm25": pm25,
            "wind_speed": wind,
            "humidity": humidity,
            "source": "synthetic",
        }))
    return pd.concat(frames, ignore_index=True).sort_values(["station_id", "timestamp"]).reset_index(drop=True)


def _sigmoid(logits: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(logits, -40, 40)))


def _calibrated_probability(scores: np.ndarray, rate: float) -> np.ndarray:
    """Choose an intercept so mean simulated missingness probability matches rate."""
    if rate == 0:
        return np.zeros_like(scores, dtype=float)
    low, high = -50.0, 50.0
    for _ in range(90):
        center = (low + high) / 2
        if _sigmoid(scores + center).mean() < rate:
            low = center
        else:
            high = center
    return _sigmoid(scores + (low + high) / 2)


def inject_outages(
    raw: pd.DataFrame, config: OutageConfig
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return masked observations and separate evaluator-only hidden targets.

    The ``observed`` result NEVER contains a truth/original target column.
    Models must only receive ``observed``. In a real deployment, hidden truth
    would generally remain unknowable without independent instrumentation.
    """
    required = {"station_id", "timestamp", "pm25", "wind_speed", "humidity"}
    if not required.issubset(raw):
        raise ValueError(f"missing columns: {sorted(required - set(raw))}")
    rng = np.random.default_rng(config.seed)
    rows = raw.sort_values(["station_id", "timestamp"]).reset_index(drop=True).copy()
    if rows.duplicated(["station_id", "timestamp"]).any():
        raise ValueError("duplicate sensor timestamps")
    if rows["pm25"].isna().any():
        raise ValueError("input truth must be complete for controlled experiments")
    observed = rows.copy()
    truth = rows[["station_id", "timestamp", "pm25"]].rename(columns={"pm25": "true_pm25"}).copy()
    if config.mechanism == "mcar":
        missing = rng.random(len(rows)) < config.missing_rate
    elif config.mechanism == "mar":
        observed_covariate = ((rows["humidity"] - 55) / 12).to_numpy()
        missing = rng.random(len(rows)) < _calibrated_probability(observed_covariate, config.missing_rate)
    elif config.mechanism == "mnar":
        # This hidden dependence is known only to the SIMULATOR, not the model.
        hidden = ((rows["pm25"] - rows["pm25"].mean()) / max(rows["pm25"].std(), 1e-8)).to_numpy()
        missing = rng.random(len(rows)) < _calibrated_probability(config.gamma * hidden, config.missing_rate)
    else:
        missing = np.zeros(len(rows), dtype=bool)
        for _, indexes in rows.groupby("station_id", sort=False).indices.items():
            pos = np.asarray(indexes)
            if config.missing_rate == 0:
                continue
            count = max(1, int(np.ceil(len(pos) * config.missing_rate / config.burst_hours)))
            starts = rng.choice(max(1, len(pos) - config.burst_hours + 1), size=count, replace=True)
            for start in starts:
                missing[pos[start : start + config.burst_hours]] = True
    observed.loc[missing, "pm25"] = np.nan
    observed["reported"] = ~missing
    return observed, truth
