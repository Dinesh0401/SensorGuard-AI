"""Opt-in live pilot quota guard, staleness monitor, and delayed ground-truth reconciliation (M7.5).

Tracks bounded ingestion budgets, distinguishes delayed label arrivals from unobserved
ground truth, and audits rolling calibration strictly over matched published predictions
without ever equating missing labels to covered outcomes.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from sensorguard.db.schema import ForecastRecord, Observation, Sensor, Station
from sensorguard.ingest import IngestionPipeline
from sensorguard.models.sage_adaptive import compute_wilson_ci
from sensorguard.providers import OpenAQClient

logger = logging.getLogger(__name__)


@dataclass
class BoundedPilotConfig:
    """Strictly bounded, opt-in configuration for periodic live pilot polling."""

    opt_in_enabled: bool = False
    allowed_location_ids: tuple[int, ...] = (141, 153, 154, 157, 698, 804, 1795, 2312)
    lookback_hours: int = 48
    max_pages_per_sensor: int = 1
    page_size: int = 100
    max_requests_per_day: int = 48
    stale_threshold_hours: float = 12.0
    caution_threshold_hours: float = 3.0


@dataclass
class PilotQuotaTracker:
    """In-memory UTC daily request budget tracker preventing API quota exhaustion."""

    max_requests_per_day: int = 48
    current_day: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d"))
    requests_used: int = 0

    def _rollover_if_needed(self, now_utc: datetime) -> None:
        day_str = now_utc.strftime("%Y-%m-%d")
        if day_str != self.current_day:
            self.current_day = day_str
            self.requests_used = 0

    def remaining(self, now_utc: datetime | None = None) -> int:
        now = now_utc or datetime.now(UTC)
        self._rollover_if_needed(now)
        return max(0, self.max_requests_per_day - self.requests_used)

    def try_consume(self, count: int = 1, now_utc: datetime | None = None) -> bool:
        now = now_utc or datetime.now(UTC)
        self._rollover_if_needed(now)
        if self.requests_used + count > self.max_requests_per_day:
            return False
        self.requests_used += count
        return True


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def audit_station_staleness(
    session: Session,
    reference_time: datetime | None = None,
    caution_hours: float = 3.0,
    stale_hours: float = 12.0,
) -> dict[str, Any]:
    """Compute per-station freshness and source status relative to an explicit reference time."""
    ref_utc = _utc(reference_time) or datetime.now(UTC)
    stations = list(session.scalars(select(Station).order_by(Station.id.asc())).all())
    per_station: list[dict[str, Any]] = []
    counts = {"HEALTHY": 0, "LATE": 0, "NO_REPORT": 0}

    for st in stations:
        pm_sensor = session.scalars(
            select(Sensor).where(Sensor.station_id == st.id, Sensor.parameter == "pm25").limit(1)
        ).first()
        latest_obs: Observation | None = None
        if pm_sensor is not None:
            latest_obs = session.scalars(
                select(Observation)
                .where(
                    Observation.sensor_id == pm_sensor.id,
                    Observation.quality_flag == "VALID",
                )
                .order_by(desc(Observation.observed_at), desc(Observation.version))
                .limit(1)
            ).first()

        if latest_obs is None:
            status_label = "NO_REPORT"
            age_hours: float | None = None
            obs_ts = None
            fetch_ts = None
        else:
            obs_ts = _utc(latest_obs.observed_at)
            fetch_ts = _utc(latest_obs.fetched_at)
            age_hours = max(0.0, (ref_utc - obs_ts).total_seconds() / 3600.0) if obs_ts else None
            if age_hours is None or age_hours > stale_hours:
                status_label = "NO_REPORT"
            elif age_hours > caution_hours:
                status_label = "LATE"
            else:
                status_label = "HEALTHY"

        counts[status_label] += 1
        per_station.append(
            {
                "station_id": st.id,
                "station_name": st.name,
                "provider_location_id": st.provider_location_id,
                "latest_observed_at": obs_ts.isoformat() if obs_ts else None,
                "latest_fetched_at": fetch_ts.isoformat() if fetch_ts else None,
                "age_hours": round(age_hours, 2) if age_hours is not None else None,
                "source_status": status_label,
            }
        )

    return {
        "reference_time": ref_utc.isoformat(),
        "total_stations": len(stations),
        "status_counts": counts,
        "stations": per_station,
    }


def run_bounded_opt_in_poll(
    session_factory: sessionmaker[Session],
    config: BoundedPilotConfig,
    quota: PilotQuotaTracker,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Execute a strictly opt-in, quota-bounded OpenAQ poll and staleness audit.

    Refuses any external HTTP traffic unless `config.opt_in_enabled` is True and
    either an API key or explicit mock `transport` is provided.
    """
    now = _utc(now_utc) or datetime.now(UTC)

    with session_factory() as session:
        if not config.opt_in_enabled:
            staleness = audit_station_staleness(
                session,
                reference_time=now,
                caution_hours=config.caution_threshold_hours,
                stale_hours=config.stale_threshold_hours,
            )
            return {
                "status": "SKIPPED_OPT_OUT",
                "polled_at": now.isoformat(),
                "requests_used_today": quota.requests_used,
                "requests_remaining": quota.remaining(now),
                "records_fetched": 0,
                "records_persisted": 0,
                "staleness": staleness,
            }

        if not api_key and transport is None:
            staleness = audit_station_staleness(
                session,
                reference_time=now,
                caution_hours=config.caution_threshold_hours,
                stale_hours=config.stale_threshold_hours,
            )
            return {
                "status": "SKIPPED_NO_CREDENTIALS",
                "polled_at": now.isoformat(),
                "requests_used_today": quota.requests_used,
                "requests_remaining": quota.remaining(now),
                "records_fetched": 0,
                "records_persisted": 0,
                "staleness": staleness,
            }

        sensors = list(
            session.scalars(
                select(Sensor)
                .join(Station, Station.id == Sensor.station_id)
                .where(
                    Sensor.parameter == "pm25",
                    Station.provider_location_id.in_(list(config.allowed_location_ids)),
                )
                .order_by(Sensor.id.asc())
            ).all()
        )

        window_from = (now - timedelta(hours=config.lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        window_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        client = OpenAQClient(
            api_key=api_key or "mock-test-key",
            transport=transport,
            max_retries=1,
        )
        pipeline = IngestionPipeline(session)

        fetched_total = 0
        persisted_total = 0
        revised_total = 0
        sensors_polled = 0
        quota_halted = False

        for sensor in sensors:
            if not quota.try_consume(config.max_pages_per_sensor, now):
                quota_halted = True
                logger.warning(
                    "Daily OpenAQ quota exhausted (%d/%d). Halting remaining station polls.",
                    quota.requests_used,
                    quota.max_requests_per_day,
                )
                break
            res = pipeline.ingest_sensor_hours(
                sensor_db_id=sensor.id,
                provider_sensor_id=sensor.provider_sensor_id,
                client=client,
                datetime_from=window_from,
                datetime_to=window_to,
                max_pages=config.max_pages_per_sensor,
                page_size=config.page_size,
            )
            fetched_total += res.records_fetched
            persisted_total += res.records_inserted
            revised_total += res.records_revised
            sensors_polled += 1

        session.commit()
        staleness = audit_station_staleness(
            session,
            reference_time=now,
            caution_hours=config.caution_threshold_hours,
            stale_hours=config.stale_threshold_hours,
        )

        return {
            "status": "QUOTA_EXHAUSTED" if quota_halted else "SUCCESS",
            "polled_at": now.isoformat(),
            "window_from": window_from,
            "window_to": window_to,
            "sensors_polled": sensors_polled,
            "requests_used_today": quota.requests_used,
            "requests_remaining": quota.remaining(now),
            "records_fetched": fetched_total,
            "records_inserted": persisted_total,
            "records_revised": revised_total,
            "staleness": staleness,
        }


def reconcile_delayed_ground_truth(
    session: Session,
    run_id: int | None = None,
    delay_threshold_hours: float = 1.5,
) -> dict[str, Any]:
    """Reconcile persisted ForecastRecord rows against arrived Observation truth labels.

    Explicitly separates arrived ground truth (on-time vs delayed) from still-missing
    ground truth (`unmatched_missing_truth_count`), computing empirical PICP and MPIW
    only across matched predictions and flagging potential MNAR label censoring.
    """
    stmt = select(ForecastRecord).order_by(ForecastRecord.decision_cutoff.asc())
    if run_id is not None:
        stmt = stmt.where(ForecastRecord.run_id == run_id)
    forecasts = list(session.scalars(stmt).all())

    if not forecasts:
        return {
            "total_forecasts": 0,
            "matched_count": 0,
            "unmatched_missing_truth_count": 0,
            "delayed_arrival_count": 0,
            "missing_label_rate": 0.0,
            "published_matched_count": 0,
            "published_picp": None,
            "published_picp_ci95": None,
            "published_mpiw": None,
            "abstained_matched_count": 0,
            "mnar_censoring_warning": False,
        }

    sensor_ids = sorted({f.sensor_id for f in forecasts})
    target_times = sorted({_utc(f.target_time) for f in forecasts if f.target_time is not None})

    # Load latest VALID version per (sensor_id, observed_at)
    obs_rows = list(
        session.scalars(
            select(Observation)
            .where(
                Observation.sensor_id.in_(sensor_ids),
                Observation.observed_at.in_(target_times),
                Observation.quality_flag == "VALID",
            )
            .order_by(Observation.version.desc())
        ).all()
    )
    truth_map: dict[tuple[int, datetime], Observation] = {}
    for obs in obs_rows:
        obs_t = _utc(obs.observed_at)
        if obs_t is not None and (obs.sensor_id, obs_t) not in truth_map and obs.value is not None:
            truth_map[(obs.sensor_id, obs_t)] = obs

    matched_count = 0
    unmatched_count = 0
    delayed_count = 0
    pub_covered = 0
    pub_total = 0
    pub_widths: list[float] = []
    abstained_matched = 0
    arrival_delays: list[float] = []

    for rec in forecasts:
        t_target = _utc(rec.target_time)
        obs = truth_map.get((rec.sensor_id, t_target)) if t_target else None
        if obs is None or obs.value is None or t_target is None:
            unmatched_count += 1
            continue

        matched_count += 1
        arrival_dt = _utc(obs.fetched_at) or t_target
        delay_h = max(0.0, (arrival_dt - t_target).total_seconds() / 3600.0)
        arrival_delays.append(delay_h)
        if delay_h > delay_threshold_hours:
            delayed_count += 1

        y_val = float(obs.value)
        is_pub = (
            rec.status.upper() != "ABSTAIN" and rec.lower_bound is not None and rec.upper_bound is not None
        )
        if is_pub:
            pub_total += 1
            lo = float(rec.lower_bound)  # type: ignore[arg-type]
            hi = float(rec.upper_bound)  # type: ignore[arg-type]
            pub_widths.append(hi - lo)
            if lo <= y_val <= hi:
                pub_covered += 1
        else:
            abstained_matched += 1

    missing_rate = unmatched_count / len(forecasts)
    pub_picp = (pub_covered / pub_total) if pub_total > 0 else None
    pub_ci = compute_wilson_ci(pub_covered, pub_total) if pub_total > 0 else None
    pub_mpiw = (sum(pub_widths) / len(pub_widths)) if pub_widths else None

    return {
        "total_forecasts": len(forecasts),
        "matched_count": matched_count,
        "unmatched_missing_truth_count": unmatched_count,
        "delayed_arrival_count": delayed_count,
        "mean_arrival_delay_hours": (
            round(sum(arrival_delays) / len(arrival_delays), 3) if arrival_delays else None
        ),
        "missing_label_rate": round(missing_rate, 4),
        "published_matched_count": pub_total,
        "published_covered_count": pub_covered,
        "published_picp": round(pub_picp, 4) if pub_picp is not None else None,
        "published_picp_ci95": [round(pub_ci[0], 4), round(pub_ci[1], 4)] if pub_ci else None,
        "published_mpiw": round(pub_mpiw, 4) if pub_mpiw is not None else None,
        "abstained_matched_count": abstained_matched,
        "mnar_censoring_warning": bool(missing_rate > 0.10 or delayed_count > 0),
        "config_note": asdict(BoundedPilotConfig()),
    }
