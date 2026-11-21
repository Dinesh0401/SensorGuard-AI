"""Point-in-time historical replay engine preventing future data leakage.

Features two distinct replay modes:
1. Prospective replay:
   Strictly filters by `fetched_at <= decision_cutoff`.
   Only measurements physically stored in the system by the decision cutoff are visible.
   Prevents retroactive backfill contamination.

2. Retrospective research replay:
   Used for historical simulation when real-time ingestion timestamps are absent.
   Filters by `observed_at <= decision_cutoff - delay_hours`.
   Labels all outputs as simulated availability with explicit assumed delay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sensorguard.db.schema import Observation

ReplayMode = Literal["prospective", "retrospective"]


@dataclass(frozen=True)
class ReplayCutoffSummary:
    """Metadata describing a historical replay execution."""

    decision_cutoff: str
    mode: str
    assumed_delay_hours: float
    total_visible_records: int
    sensors_reporting: int
    is_simulated_availability: bool


class PointInTimeReplayEngine:
    """Query engine ensuring zero-future-leakage point-in-time observations."""

    def __init__(self, session: Session) -> None:
        """Initialize the replay engine with an active database session."""
        self.session = session

    def query_as_of(
        self,
        sensor_ids: list[int],
        decision_cutoff: datetime,
        mode: ReplayMode = "prospective",
        delay_hours: float = 0.0,
    ) -> tuple[pd.DataFrame, ReplayCutoffSummary]:
        """Query observations strictly known and available as of the decision cutoff.

        Args:
            sensor_ids: List of database Sensor IDs to query.
            decision_cutoff: Point-in-time decision cutoff (must have UTC timezone).
            mode: 'prospective' (uses fetched_at) or 'retrospective' (uses observed_at - delay).
            delay_hours: Delay parameter used in retrospective mode.

        Returns:
            Tuple of (DataFrame of visible observations, ReplayCutoffSummary).
        """
        if decision_cutoff.tzinfo is None:
            cutoff_utc = decision_cutoff.replace(tzinfo=UTC)
        else:
            cutoff_utc = decision_cutoff.astimezone(UTC)

        if mode == "prospective":
            # Prospective mode: only data already fetched and persisted by cutoff,
            # observed at or before cutoff, with physical time not exceeding fetch receipt time.
            # Picks the highest version available as-of the cutoff timestamp.
            subq = (
                select(
                    Observation.sensor_id.label("s_id"),
                    Observation.observed_at.label("obs_at"),
                    func.max(Observation.version).label("max_version"),
                )
                .where(
                    Observation.sensor_id.in_(sensor_ids),
                    Observation.fetched_at <= cutoff_utc,
                    Observation.persisted_at <= cutoff_utc,
                    Observation.observed_at <= cutoff_utc,
                    Observation.observed_at <= Observation.fetched_at,
                )
                .group_by(Observation.sensor_id, Observation.observed_at)
                .subquery()
            )
            stmt = (
                select(
                    Observation.id,
                    Observation.sensor_id,
                    Observation.version,
                    Observation.observed_at,
                    Observation.period_start,
                    Observation.period_end,
                    Observation.received_at,
                    Observation.fetched_at,
                    Observation.persisted_at,
                    Observation.value,
                    Observation.unit,
                    Observation.quality_flag,
                    Observation.source_status,
                )
                .join(
                    subq,
                    (Observation.sensor_id == subq.c.s_id)
                    & (Observation.observed_at == subq.c.obs_at)
                    & (Observation.version == subq.c.max_version),
                )
                .order_by(Observation.observed_at)
            )
            simulated = False
            effective_delay = 0.0

        elif mode == "retrospective":
            # Retrospective mode: simulated availability with declared delay
            effective_delay = max(0.0, delay_hours)
            effective_cutoff = cutoff_utc - timedelta(hours=effective_delay)
            subq = (
                select(
                    Observation.sensor_id.label("s_id"),
                    Observation.observed_at.label("obs_at"),
                    func.max(Observation.version).label("max_version"),
                )
                .where(
                    Observation.sensor_id.in_(sensor_ids),
                    Observation.observed_at <= effective_cutoff,
                )
                .group_by(Observation.sensor_id, Observation.observed_at)
                .subquery()
            )
            stmt = (
                select(
                    Observation.id,
                    Observation.sensor_id,
                    Observation.version,
                    Observation.observed_at,
                    Observation.period_start,
                    Observation.period_end,
                    Observation.received_at,
                    Observation.fetched_at,
                    Observation.persisted_at,
                    Observation.value,
                    Observation.unit,
                    Observation.quality_flag,
                    Observation.source_status,
                )
                .join(
                    subq,
                    (Observation.sensor_id == subq.c.s_id)
                    & (Observation.observed_at == subq.c.obs_at)
                    & (Observation.version == subq.c.max_version),
                )
                .order_by(Observation.observed_at)
            )
            simulated = True
        else:
            raise ValueError(f"Unknown replay mode: {mode}. Must be 'prospective' or 'retrospective'")

        rows = self.session.execute(stmt).all()
        if not rows:
            empty_df = pd.DataFrame(
                columns=[
                    "id",
                    "sensor_id",
                    "version",
                    "observed_at",
                    "period_start",
                    "period_end",
                    "received_at",
                    "fetched_at",
                    "value",
                    "unit",
                    "quality_flag",
                    "source_status",
                    "replay_mode",
                    "simulated_availability",
                ]
            )
            summary = ReplayCutoffSummary(
                decision_cutoff=cutoff_utc.isoformat(),
                mode=mode,
                assumed_delay_hours=effective_delay,
                total_visible_records=0,
                sensors_reporting=0,
                is_simulated_availability=simulated,
            )
            return empty_df, summary

        df = pd.DataFrame(
            [
                {
                    "id": r.id,
                    "sensor_id": r.sensor_id,
                    "version": r.version,
                    "observed_at": r.observed_at,
                    "period_start": r.period_start,
                    "period_end": r.period_end,
                    "received_at": r.received_at,
                    "fetched_at": r.fetched_at,
                    "persisted_at": r.persisted_at,
                    "value": r.value,
                    "unit": r.unit,
                    "quality_flag": r.quality_flag,
                    "source_status": r.source_status,
                    "replay_mode": mode,
                    "simulated_availability": simulated,
                }
                for r in rows
            ]
        )

        df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True)
        df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True)
        df["persisted_at"] = pd.to_datetime(df["persisted_at"], utc=True)

        summary = ReplayCutoffSummary(
            decision_cutoff=cutoff_utc.isoformat(),
            mode=mode,
            assumed_delay_hours=effective_delay,
            total_visible_records=len(df),
            sensors_reporting=int(df["sensor_id"].nunique()),
            is_simulated_availability=simulated,
        )
        return df, summary

    def build_point_in_time_forecast_features(
        self,
        sensor_id: int,
        decision_cutoff: datetime,
        mode: ReplayMode = "prospective",
        delay_hours: float = 0.0,
    ) -> dict[str, float | str | None]:
        """Construct feature vector for a +1 hour ahead forecast target.

        Target is strictly at decision_cutoff + 1 hour.
        Features use ONLY observations strictly visible as of decision_cutoff.
        """
        df, _ = self.query_as_of(
            sensor_ids=[sensor_id],
            decision_cutoff=decision_cutoff,
            mode=mode,
            delay_hours=delay_hours,
        )

        valid_df = df[df["source_status"] == "HEALTHY"].dropna(subset=["value"])

        if valid_df.empty:
            return {
                "sensor_id": sensor_id,
                "cutoff_time": decision_cutoff.isoformat(),
                "target_time": (decision_cutoff + timedelta(hours=1)).isoformat(),
                "last_observed_pm25": None,
                "age_hours": float("inf"),
                "status": "abstain",
                "status_reason": "no_visible_observations",
            }

        last_record = valid_df.iloc[-1]
        last_val = float(last_record["value"])
        raw_last_dt = last_record["observed_at"]
        if hasattr(raw_last_dt, "to_pydatetime"):
            last_observed_at = raw_last_dt.to_pydatetime()
        elif isinstance(raw_last_dt, str):
            last_observed_at = datetime.fromisoformat(raw_last_dt)
        else:
            last_observed_at = raw_last_dt

        if last_observed_at.tzinfo is None:
            last_observed_at = last_observed_at.replace(tzinfo=UTC)
        else:
            last_observed_at = last_observed_at.astimezone(UTC)

        if decision_cutoff.tzinfo is None:
            cutoff_utc = decision_cutoff.replace(tzinfo=UTC)
        else:
            cutoff_utc = decision_cutoff.astimezone(UTC)

        age = max(0.0, (cutoff_utc - last_observed_at).total_seconds() / 3600.0)

        # Policy classification
        if age > 12.0:
            status = "abstain"
            reason = "stale_sensor_history"
        elif age > 3.0:
            status = "caution"
            reason = "reduced_sensor_freshness"
        else:
            status = "publish"
            reason = "sufficient_freshness"

        return {
            "sensor_id": sensor_id,
            "cutoff_time": cutoff_utc.isoformat(),
            "target_time": (cutoff_utc + timedelta(hours=1)).isoformat(),
            "last_observed_pm25": last_val,
            "age_hours": round(age, 2),
            "status": status,
            "status_reason": reason,
        }

    def build_replay_timeline(
        self,
        sensor_id: int,
        start_time: datetime,
        end_time: datetime,
        mode: ReplayMode = "prospective",
        delay_hours: float = 0.0,
    ) -> pd.DataFrame:
        """Construct continuous hourly timeline explicitly categorizing missing slots and delays.

        Fills absent hours with explicit `NO_REPORT` records to prevent unobserved
        hours from being silently ignored or conflated with hardware failure.
        """
        start_utc = start_time.astimezone(UTC) if start_time.tzinfo else start_time.replace(tzinfo=UTC)
        end_utc = end_time.astimezone(UTC) if end_time.tzinfo else end_time.replace(tzinfo=UTC)

        df, _ = self.query_as_of(
            sensor_ids=[sensor_id],
            decision_cutoff=end_utc,
            mode=mode,
            delay_hours=delay_hours,
        )

        expected_hours = pd.date_range(start_utc, end_utc, freq="1h", tz=UTC)
        if df.empty:
            timeline_records = []
            for h in expected_hours:
                timeline_records.append(
                    {
                        "sensor_id": sensor_id,
                        "observed_at": h,
                        "value": np.nan,
                        "unit": "µg/m³",
                        "quality_flag": "MISSING_SLOT",
                        "source_status": "NO_REPORT",
                        "age_hours": (end_utc - h).total_seconds() / 3600.0,
                        "reporting_latency_hours": None,
                        "backfill_lag_hours": None,
                    }
                )
            return pd.DataFrame(timeline_records)

        obs_map = {}
        for _, row in df.iterrows():
            obs_dt = row["observed_at"]
            if hasattr(obs_dt, "to_pydatetime"):
                obs_dt = obs_dt.to_pydatetime()
            if obs_dt.tzinfo is None:
                obs_dt = obs_dt.replace(tzinfo=UTC)
            else:
                obs_dt = obs_dt.astimezone(UTC)
            obs_map[obs_dt.replace(minute=0, second=0, microsecond=0)] = row

        timeline = []
        for h in expected_hours:
            floor_h = h.to_pydatetime().replace(minute=0, second=0, microsecond=0)
            if floor_h in obs_map:
                row = obs_map[floor_h]
                val = row["value"]
                status = row["source_status"]
                flag = row["quality_flag"]

                fetched = row["fetched_at"]
                if hasattr(fetched, "to_pydatetime"):
                    fetched = fetched.to_pydatetime()
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=UTC)
                else:
                    fetched = fetched.astimezone(UTC)

                # Reporting latency vs backfill ingestion lag:
                received = row["received_at"]
                if pd.notna(received):
                    if hasattr(received, "to_pydatetime"):
                        received = received.to_pydatetime()
                    if received.tzinfo is None:
                        received = received.replace(tzinfo=UTC)
                    else:
                        received = received.astimezone(UTC)
                    reporting_latency_hours = max(0.0, (received - floor_h).total_seconds() / 3600.0)
                    if reporting_latency_hours > 3.0 and status == "HEALTHY":
                        status = "LATE"
                else:
                    reporting_latency_hours = None
                    # When received_at is unavailable:
                    # Distinguish near-real-time lag (<=24h) from historical backfill (>24h).
                    # Historical backfills must NOT be falsely marked as LATE.
                    backfill_lag = (fetched - floor_h).total_seconds() / 3600.0
                    if 3.0 < backfill_lag <= 24.0 and status == "HEALTHY":
                        status = "LATE"

                timeline.append(
                    {
                        "sensor_id": sensor_id,
                        "observed_at": h,
                        "value": val,
                        "unit": row["unit"],
                        "quality_flag": flag,
                        "source_status": status,
                        "age_hours": (end_utc - h).total_seconds() / 3600.0,
                        "reporting_latency_hours": reporting_latency_hours,
                        "backfill_lag_hours": round(max(0.0, (fetched - floor_h).total_seconds() / 3600.0), 2),
                    }
                )
            else:
                timeline.append(
                    {
                        "sensor_id": sensor_id,
                        "observed_at": h,
                        "value": np.nan,
                        "unit": "µg/m³",
                        "quality_flag": "MISSING_SLOT",
                        "source_status": "NO_REPORT",
                        "age_hours": (end_utc - h).total_seconds() / 3600.0,
                        "reporting_latency_hours": None,
                        "backfill_lag_hours": None,
                    }
                )

        return pd.DataFrame(timeline)
