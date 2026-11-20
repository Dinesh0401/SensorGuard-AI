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

import pandas as pd
from sqlalchemy import select
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
            # Prospective mode: only data already fetched and persisted by cutoff
            stmt = (
                select(
                    Observation.id,
                    Observation.sensor_id,
                    Observation.observed_at,
                    Observation.period_start,
                    Observation.period_end,
                    Observation.received_at,
                    Observation.fetched_at,
                    Observation.value,
                    Observation.unit,
                    Observation.quality_flag,
                    Observation.source_status,
                )
                .where(
                    Observation.sensor_id.in_(sensor_ids),
                    Observation.fetched_at <= cutoff_utc,
                )
                .order_by(Observation.observed_at)
            )
            simulated = False
            effective_delay = 0.0

        elif mode == "retrospective":
            # Retrospective mode: simulated availability with declared delay
            effective_delay = max(0.0, delay_hours)
            effective_cutoff = cutoff_utc - timedelta(hours=effective_delay)
            stmt = (
                select(
                    Observation.id,
                    Observation.sensor_id,
                    Observation.observed_at,
                    Observation.period_start,
                    Observation.period_end,
                    Observation.received_at,
                    Observation.fetched_at,
                    Observation.value,
                    Observation.unit,
                    Observation.quality_flag,
                    Observation.source_status,
                )
                .where(
                    Observation.sensor_id.in_(sensor_ids),
                    Observation.observed_at <= effective_cutoff,
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
                    "observed_at": r.observed_at,
                    "period_start": r.period_start,
                    "period_end": r.period_end,
                    "received_at": r.received_at,
                    "fetched_at": r.fetched_at,
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
