"""Persistence helpers for materialized station forecast runs and intervals.

Stores versioned ForecastRun metadata and individual ForecastRecord predictions
into the PostgreSQL / SQLAlchemy database so served intervals, abstention decisions,
sensitivity metrics, and model SHA-256 hashes remain fully auditable.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from sensorguard.db.schema import ForecastRecord, ForecastRun, Sensor
from sensorguard.models.manifest import ModelManifest
from sensorguard.models.sage import SAGEForecaster


def _to_utc(dt: Any) -> datetime | None:
    """Convert Timestamp/datetime/ISO string to timezone-aware UTC datetime."""
    if dt is None or (isinstance(dt, float) and math.isnan(dt)):
        return None
    if hasattr(dt, "to_pydatetime"):
        dt = dt.to_pydatetime()
    elif isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def materialize_station_forecasts(
    session: Session,
    featured_df: pd.DataFrame,
    model: SAGEForecaster,
    manifest: ModelManifest,
    limit_per_station: int = 24,
    notes: str | None = None,
) -> ForecastRun:
    """Compute guarded SAGE predictions across all stations and persist them to DB."""
    if featured_df.empty:
        raise ValueError("Cannot materialize forecasts from an empty feature dataframe.")

    pm25_sensors = {
        s.station_id: s.id for s in session.scalars(select(Sensor).where(Sensor.parameter == "pm25")).all()
    }
    if not pm25_sensors:
        pm25_sensors = {s.station_id: s.id for s in session.scalars(select(Sensor)).all()}

    max_ts = _to_utc(featured_df["timestamp"].max()) or datetime.now(UTC)
    run = ForecastRun(
        model_id=manifest.model_id,
        model_version=manifest.version,
        model_sha256=manifest.sha256,
        dataset_hash=manifest.dataset_hash,
        algorithm=manifest.algorithm,
        nominal_coverage=float(manifest.nominal_coverage),
        issued_at=datetime.now(UTC),
        decision_cutoff=max_ts,
        notes=notes,
    )
    session.add(run)
    session.flush()

    feature_cols = list(getattr(model, "feature_columns", []))
    unique_stations = sorted({str(st) for st in featured_df["station_id"].unique()}, key=int)

    for st_key in unique_stations:
        st_id = int(st_key)
        sensor_id = pm25_sensors.get(st_id)
        if sensor_id is None:
            continue

        st_df = (
            featured_df[featured_df["station_id"].astype(str) == st_key]
            .sort_values("timestamp")
            .tail(limit_per_station)
            .reset_index(drop=True)
        )
        if st_df.empty:
            continue

        sage_res = model.predict_guarded(st_df)

        for i in range(len(st_df)):
            row = st_df.iloc[i]
            cutoff_dt = _to_utc(row["timestamp"]) or max_ts
            target_dt = cutoff_dt + timedelta(hours=1)
            age_hours_raw = row.get("age_hours")
            newest_obs_at: datetime | None = None
            if age_hours_raw is not None and pd.notna(age_hours_raw):
                age_val = float(age_hours_raw)
                if math.isfinite(age_val) and age_val < 1e6:
                    newest_obs_at = cutoff_dt - timedelta(hours=age_val)

            feature_payload = {
                col: (
                    None
                    if pd.isna(row[col])
                    else float(row[col])
                    if isinstance(row[col], (int, float))
                    else str(row[col])
                )
                for col in feature_cols
                if col in st_df.columns
            }
            feature_hash = hashlib.sha256(
                json.dumps(feature_payload, sort_keys=True).encode("utf-8")
            ).hexdigest()

            scenario_bounds = {
                str(gamma): {
                    "lower": round(float(lo[i]), 4),
                    "upper": round(float(hi[i]), 4),
                }
                for gamma, (lo, hi) in sage_res.gamma_intervals.items()
            }

            raw_status = str(sage_res.status[i]).upper()
            abstain_reason = str(sage_res.reason[i])
            if raw_status == "ABSTAIN":
                lower_val = None
                upper_val = None
            else:
                lower_val = round(float(sage_res.lower_envelope[i]), 4)
                upper_val = round(float(sage_res.upper_envelope[i]), 4)

            rec = ForecastRecord(
                run_id=run.id,
                station_id=st_id,
                sensor_id=sensor_id,
                decision_cutoff=cutoff_dt,
                target_time=target_dt,
                point_forecast=round(float(sage_res.point_forecast[i]), 4),
                lower_bound=lower_val,
                upper_bound=upper_val,
                nominal_coverage=float(manifest.nominal_coverage),
                status=raw_status,
                abstain_reason=abstain_reason,
                d_sensitivity=round(float(sage_res.scenario_disagreement[i]), 4),
                newest_source_observed_at=newest_obs_at,
                feature_hash=feature_hash,
                scenario_bounds_json=json.dumps(scenario_bounds, sort_keys=True),
                created_at=datetime.now(UTC),
            )
            session.add(rec)

    session.commit()
    session.refresh(run)
    return run


def get_latest_station_forecasts(
    session: Session,
    station_id: int,
    limit: int = 24,
) -> tuple[ForecastRun | None, list[ForecastRecord]]:
    """Fetch the most recent persisted forecast batch for a given station."""
    latest_run_id = session.scalars(
        select(ForecastRecord.run_id)
        .where(ForecastRecord.station_id == station_id)
        .order_by(desc(ForecastRecord.run_id))
        .limit(1)
    ).first()
    if latest_run_id is None:
        return None, []

    run = session.get(ForecastRun, latest_run_id)
    records_desc = list(
        session.scalars(
            select(ForecastRecord)
            .where(
                ForecastRecord.run_id == latest_run_id,
                ForecastRecord.station_id == station_id,
            )
            .order_by(desc(ForecastRecord.target_time))
            .limit(limit)
        ).all()
    )
    records_desc.reverse()
    return run, records_desc
