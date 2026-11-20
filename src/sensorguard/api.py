"""Small read-only API exposing reproducible synthetic demonstration results."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, Query

from sensorguard.experiment import ExperimentConfig, run_experiment
from sensorguard.simulation import OutageConfig

app = FastAPI(title="SensorGuard AI", version="0.1.0", description="Synthetic research preview; NOT live air quality")


@app.get("/health")
def health() -> dict[str, str]:
    """Return process liveness; not a claim of external provider health."""
    return {"status": "ok", "data_mode": "synthetic_only"}


@lru_cache(maxsize=16)
def _demo(mechanism: str, seed: int) -> dict:
    return run_experiment(ExperimentConfig(outage=OutageConfig(mechanism=mechanism, seed=seed)))


@app.get("/v1/demo/experiment")
def demo_experiment(
    mechanism: str = Query("mnar", pattern="^(mcar|mar|mnar|burst)$"),
    seed: int = Query(17, ge=0, le=1000000),
) -> dict:
    """Return measured performance on generated data; no real readings involved."""
    return _demo(mechanism, seed)
