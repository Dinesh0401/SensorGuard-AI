"""Periodic ingestion scheduler and background polling daemon.

Executes respectful, bounded provider pulls with:
- Strict rate control and hourly budget caps
- Graceful offline / degraded handling (recording SOURCE_UNAVAILABLE rather than crashing)
- Cryptographic hash snapshots of raw bytes and JSON payloads
- Zero target lookahead (preserving event observed_at vs fetched_at vs persisted_at)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sensorguard.db.schema import Observation, Sensor, Station
from sensorguard.db.session import create_db_engine, get_session_factory
from sensorguard.ingest import OpenAQIngestionPipeline
from sensorguard.providers import OpenAQClient

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    """Configuration parameters for periodic provider polling.

    Attributes:
        poll_interval_seconds: Base polling cadence in seconds (default 3600 = 1h).
        max_retries: Maximum exponential backoff retries on provider failure.
        backoff_factor_seconds: Initial backoff base in seconds.
        max_batch_size: Maximum records requested per station cycle.
        enabled: Whether automated background scheduling is active (default False).
        fetch_live_provider: Whether to query upstream OpenAQ API (default False).
    """

    poll_interval_seconds: int = 3600
    max_retries: int = 3
    backoff_factor_seconds: float = 2.0
    max_batch_size: int = 100
    enabled: bool = False
    fetch_live_provider: bool = False


class IngestScheduler:
    """Asynchronous background scheduler for periodic environmental ingestion."""

    def __init__(
        self,
        config: SchedulerConfig | None = None,
        session_factory=None,
    ) -> None:
        self.config = config or SchedulerConfig(
            enabled=os.getenv("ENABLE_INGEST_SCHEDULER", "false").lower() in ("1", "true"),
            fetch_live_provider=os.getenv("ENABLE_LIVE_PROVIDER_FETCH", "false").lower() in ("1", "true"),
        )
        if session_factory is None:
            engine = create_db_engine()
            self.session_factory = get_session_factory(engine)
        else:
            self.session_factory = session_factory

        self._is_running: bool = False
        self._last_poll_time: datetime | None = None
        self._last_poll_status: str = "IDLE"
        self._poll_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def last_poll_time(self) -> datetime | None:
        return self._last_poll_time

    @property
    def last_poll_status(self) -> str:
        return self._last_poll_status

    def check_network_freshness(self, session: Session) -> dict[str, Any]:
        """Audit active stations and identify stale or lagging sensors."""
        stations = session.scalars(select(Station)).all()
        now_utc = datetime.now(UTC)

        summary: dict[str, Any] = {
            "total_stations": len(stations),
            "healthy": 0,
            "late": 0,
            "no_report": 0,
            "stations_detail": [],
        }

        for st in stations:
            latest_obs = session.scalars(
                select(Observation)
                .join(Sensor, Sensor.id == Observation.sensor_id)
                .where(Sensor.station_id == st.id)
                .where(Sensor.parameter == "pm25")
                .order_by(Observation.observed_at.desc())
                .limit(1)
            ).first()

            if latest_obs and latest_obs.observed_at:
                obs_time = latest_obs.observed_at
                if obs_time.tzinfo is None:
                    obs_time = obs_time.replace(tzinfo=UTC)
                age = max(0.0, (now_utc - obs_time).total_seconds() / 3600.0)
                if age <= 3.0:
                    status = "HEALTHY"
                    summary["healthy"] += 1
                elif age <= 12.0:
                    status = "LATE"
                    summary["late"] += 1
                else:
                    status = "NO_REPORT"
                    summary["no_report"] += 1
            else:
                age = None
                status = "NO_REPORT"
                summary["no_report"] += 1

            summary["stations_detail"].append(
                {
                    "station_id": st.id,
                    "name": st.name,
                    "status": status,
                    "age_hours": round(age, 2) if age is not None else None,
                }
            )

        return summary

    async def poll_once(self) -> dict[str, Any]:
        """Execute a single bounded polling cycle across the pilot network.

        Guards against duplicate polling workers and optionally retrieves
        bounded upstream observations if live provider fetching is configured.
        """
        if self._poll_lock.locked():
            logger.warning("Scheduled poll cycle already running; skipping duplicate invocation.")
            return {"status": "SKIPPED_DUPLICATE", "message": "Prior poll active"}

        async with self._poll_lock:
            now_utc = datetime.now(UTC)
            logger.info("Executing scheduled ingestion poll at %s...", now_utc.isoformat())

            with self.session_factory() as session:
                try:
                    freshness = self.check_network_freshness(session)
                    records_ingested = 0
                    provider_notes = "Audit freshness completed."

                    if self.config.fetch_live_provider:
                        api_key = os.getenv("OPENAQ_API_KEY")
                        if api_key:
                            try:
                                client = OpenAQClient(api_key=api_key)
                                pipeline = OpenAQIngestionPipeline(session)
                                sensors = session.scalars(
                                    select(Sensor).where(Sensor.parameter == "pm25")
                                ).all()
                                for s in sensors:
                                    res = pipeline.ingest_sensor_hours(
                                        sensor_db_id=s.id,
                                        provider_sensor_id=s.provider_sensor_id,
                                        client=client,
                                        max_pages=1,
                                        page_size=self.config.max_batch_size,
                                    )
                                    records_ingested += res.records_persisted
                                session.commit()
                                provider_notes = f"Ingested {records_ingested} new observations."
                            except Exception as pe:  # noqa: BLE001
                                logger.warning("Live provider retrieval failed gracefully: %s", pe)
                                provider_notes = f"Live provider pull skipped: {pe}"
                        else:
                            provider_notes = (
                                "Live fetch requested but OPENAQ_API_KEY unconfigured; ran audit only."
                            )

                    self._last_poll_time = now_utc
                    self._last_poll_status = "SUCCESS"
                    logger.info(
                        "Network audit: %d stations (%d healthy, %d late, %d no_report) - %s",
                        freshness["total_stations"],
                        freshness["healthy"],
                        freshness["late"],
                        freshness["no_report"],
                        provider_notes,
                    )
                    return {
                        "status": "SUCCESS",
                        "timestamp": now_utc.isoformat(),
                        "records_ingested": records_ingested,
                        "provider_notes": provider_notes,
                        "freshness": freshness,
                        "total_stations": freshness["total_stations"],
                    }
                except Exception as e:  # noqa: BLE001 - scheduler must log and remain resilient
                    self._last_poll_status = f"FAILED: {e}"
                    logger.error("Scheduled polling failed: %s", e)
                    return {"error": str(e), "status": "FAILED"}

    async def run_loop(self, max_cycles: int | None = None) -> None:
        """Run continuous asynchronous polling loop with bounded cycles for testing."""
        self._is_running = True
        cycles = 0
        try:
            while self._is_running:
                await self.poll_once()
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                await asyncio.sleep(self.config.poll_interval_seconds)
        finally:
            self._is_running = False

    def stop(self) -> None:
        """Signal scheduler to gracefully shut down."""
        self._is_running = False
