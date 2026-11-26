"""Durable multi-worker pilot quota guard, renewable fencing leases, kill-switch, and reconciliation (M8.1 / M8.6).

Provides:
- Database-backed UTC daily request budget consumed on EVERY outbound HTTP attempt (including retries, 429, 5xx, timeouts)
- Single-leader renewable leases with monotonic fencing tokens and commit-time ownership fencing
- Unconditional fail-closed environment & operator-authorized database kill switches (no bypass flags)
- Idempotent bounded-window reconciliation preserving append-only observation revisions
- Durable ingestion audit trails and delayed ground-truth matching
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from sensorguard.db.schema import (
    ForecastRecord,
    Observation,
    PilotControlState,
    PilotIngestAuditRun,
    PilotPollLease,
    PilotQuotaState,
    Sensor,
    Station,
)
from sensorguard.ingest import IngestionPipeline
from sensorguard.models.sage_adaptive import compute_wilson_ci
from sensorguard.providers import OpenAQClient, QuotaExhaustedError

logger = logging.getLogger(__name__)

DEFAULT_CONTROL_NAME = "openaq_live_polling"
DEFAULT_LEASE_NAME = "openaq_hourly_poll"
_ATTEMPT_LOCK = threading.Lock()


@dataclass
class BoundedPilotConfig:
    """Strictly bounded, fail-closed configuration for periodic shadow/live pilot polling.

    Operator authorization in PilotControlState (kill_switch_active=False, opt_in_enabled=True,
    authorized_at not None, non-default operator identity) is enforced unconditionally; no
    bypass toggle is permitted.
    """

    opt_in_enabled: bool = False
    allowed_location_ids: tuple[int, ...] = (141, 153, 154, 157, 698, 804, 1795, 2312)
    lookback_hours: int = 48
    max_pages_per_sensor: int = 1
    page_size: int = 100
    max_requests_per_day: int = 48
    max_retries_per_request: int = 1
    stale_threshold_hours: float = 12.0
    caution_threshold_hours: float = 3.0
    lease_name: str = DEFAULT_LEASE_NAME
    lease_ttl_seconds: int = 300
    worker_id: str | None = None
    data_mode: str = "shadow_live"


@dataclass(frozen=True)
class LeaseAcquisition:
    """Result of attempting to acquire a distributed polling leader lease with fencing token."""

    acquired: bool
    recovered_from_expired_lease: bool
    fencing_token: int | None


@dataclass
class PilotQuotaTracker:
    """In-memory UTC daily request budget tracker (mirrors atomic DB attempts when supplied)."""

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


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-pid{os.getpid()}-{uuid.uuid4().hex[:8]}"


class DurablePilotQuotaTracker:
    """Database-backed UTC daily API request budget safe across concurrent workers, retries, and restarts."""

    def __init__(self, provider: str = "openaq", max_requests_per_day: int = 48) -> None:
        self.provider = provider
        self.max_requests_per_day = max_requests_per_day

    def _ensure_day_row(self, session: Session, day_str: str, now_utc: datetime) -> PilotQuotaState:
        row = session.scalars(
            select(PilotQuotaState).where(
                PilotQuotaState.provider == self.provider,
                PilotQuotaState.budget_date_utc == day_str,
            )
        ).first()
        if row is not None:
            return row

        new_row = PilotQuotaState(
            provider=self.provider,
            budget_date_utc=day_str,
            requests_used=0,
            max_requests_per_day=self.max_requests_per_day,
            updated_at=now_utc,
        )
        try:
            with session.begin_nested():
                session.add(new_row)
                session.flush()
            return new_row
        except IntegrityError:
            return session.scalars(
                select(PilotQuotaState).where(
                    PilotQuotaState.provider == self.provider,
                    PilotQuotaState.budget_date_utc == day_str,
                )
            ).one()

    def get_usage(self, session: Session, now_utc: datetime | None = None) -> tuple[int, int]:
        """Return (requests_used, requests_remaining) for current UTC day."""
        now = _utc(now_utc) or datetime.now(UTC)
        day_str = now.strftime("%Y-%m-%d")
        row = session.scalars(
            select(PilotQuotaState).where(
                PilotQuotaState.provider == self.provider,
                PilotQuotaState.budget_date_utc == day_str,
            )
        ).first()
        if row is None:
            return 0, self.max_requests_per_day
        used = int(row.requests_used)
        cap = min(int(row.max_requests_per_day), self.max_requests_per_day)
        return used, max(0, cap - used)

    def try_consume(
        self,
        session: Session,
        count: int = 1,
        now_utc: datetime | None = None,
    ) -> bool:
        """Atomically reserve/consume `count` HTTP attempt units in SQL before wire I/O."""
        if count <= 0:
            return True
        now = _utc(now_utc) or datetime.now(UTC)
        day_str = now.strftime("%Y-%m-%d")
        row = self._ensure_day_row(session, day_str, now)

        effective_cap = min(int(row.max_requests_per_day), self.max_requests_per_day)
        stmt = (
            update(PilotQuotaState)
            .where(
                PilotQuotaState.id == row.id,
                PilotQuotaState.requests_used + count <= effective_cap,
            )
            .values(
                requests_used=PilotQuotaState.requests_used + count,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = session.execute(stmt)
        session.commit()
        session.expire(row)
        return int(result.rowcount or 0) == 1


def acquire_poll_lease(
    session: Session,
    holder_id: str,
    lease_name: str = DEFAULT_LEASE_NAME,
    ttl_seconds: int = 300,
    now_utc: datetime | None = None,
) -> LeaseAcquisition:
    """Atomically acquire single-leader polling lease with monotonically increasing fencing token."""
    now = _utc(now_utc) or datetime.now(UTC)
    expires = now + timedelta(seconds=max(5, ttl_seconds))

    row = session.scalars(select(PilotPollLease).where(PilotPollLease.lease_name == lease_name)).first()
    if row is None:
        try:
            with session.begin_nested():
                session.add(
                    PilotPollLease(
                        lease_name=lease_name,
                        holder_id=holder_id,
                        fencing_token=1,
                        acquired_at=now,
                        heartbeat_at=now,
                        expires_at=expires,
                        updated_at=now,
                    )
                )
                session.flush()
            session.commit()
            return LeaseAcquisition(acquired=True, recovered_from_expired_lease=False, fencing_token=1)
        except IntegrityError:
            row = session.scalars(select(PilotPollLease).where(PilotPollLease.lease_name == lease_name)).one()

    existing_exp = _utc(row.expires_at)
    was_expired_dirty = bool(row.holder_id is not None and existing_exp is not None and existing_exp <= now)

    stmt = (
        update(PilotPollLease)
        .where(
            PilotPollLease.id == row.id,
            PilotPollLease.holder_id.is_(None) | (PilotPollLease.expires_at <= now),
        )
        .values(
            holder_id=holder_id,
            fencing_token=PilotPollLease.fencing_token + 1,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=expires,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    res = session.execute(stmt)
    session.commit()
    session.expire(row)
    acquired = int(res.rowcount or 0) == 1
    if not acquired:
        return LeaseAcquisition(acquired=False, recovered_from_expired_lease=False, fencing_token=None)

    fresh = session.scalars(select(PilotPollLease).where(PilotPollLease.id == row.id)).one()
    return LeaseAcquisition(
        acquired=True,
        recovered_from_expired_lease=was_expired_dirty,
        fencing_token=int(fresh.fencing_token),
    )


def renew_poll_lease(
    session: Session,
    holder_id: str,
    fencing_token: int,
    lease_name: str = DEFAULT_LEASE_NAME,
    ttl_seconds: int = 300,
    now_utc: datetime | None = None,
) -> bool:
    """Renew lease TTL ONLY if holder_id, fencing_token, and unexpired lease still match."""
    now = _utc(now_utc) or datetime.now(UTC)
    new_exp = now + timedelta(seconds=max(5, ttl_seconds))
    stmt = (
        update(PilotPollLease)
        .where(
            PilotPollLease.lease_name == lease_name,
            PilotPollLease.holder_id == holder_id,
            PilotPollLease.fencing_token == fencing_token,
            PilotPollLease.expires_at > now,
        )
        .values(
            heartbeat_at=now,
            expires_at=new_exp,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    res = session.execute(stmt)
    session.commit()
    return int(res.rowcount or 0) == 1


def verify_fencing_token(
    session: Session,
    holder_id: str,
    fencing_token: int,
    lease_name: str = DEFAULT_LEASE_NAME,
    now_utc: datetime | None = None,
) -> bool:
    """Confirm worker still holds an unexpired lease with exact matching fencing token before DB commit."""
    now = _utc(now_utc) or datetime.now(UTC)
    row = session.scalars(
        select(PilotPollLease).where(
            PilotPollLease.lease_name == lease_name,
            PilotPollLease.holder_id == holder_id,
            PilotPollLease.fencing_token == fencing_token,
        )
    ).first()
    if row is None or row.expires_at is None:
        return False
    exp = _utc(row.expires_at)
    return bool(exp is not None and exp > now)


def release_poll_lease(
    session: Session,
    holder_id: str,
    fencing_token: int | None = None,
    lease_name: str = DEFAULT_LEASE_NAME,
    now_utc: datetime | None = None,
) -> bool:
    """Release leader lease held by `holder_id` (and exact `fencing_token` when supplied)."""
    now = _utc(now_utc) or datetime.now(UTC)
    conditions = [
        PilotPollLease.lease_name == lease_name,
        PilotPollLease.holder_id == holder_id,
    ]
    if fencing_token is not None:
        conditions.append(PilotPollLease.fencing_token == fencing_token)
    stmt = (
        update(PilotPollLease)
        .where(*conditions)
        .values(
            holder_id=None,
            expires_at=None,
            heartbeat_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    res = session.execute(stmt)
    session.commit()
    return int(res.rowcount or 0) == 1


def ensure_default_control_state(
    session: Session,
    control_name: str = DEFAULT_CONTROL_NAME,
    now_utc: datetime | None = None,
) -> PilotControlState:
    """Return existing kill-switch state or create a safe fail-closed default row."""
    now = _utc(now_utc) or datetime.now(UTC)
    row = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == control_name)
    ).first()
    if row is not None:
        return row
    row = PilotControlState(
        control_name=control_name,
        kill_switch_active=True,
        opt_in_enabled=False,
        authorized_at=None,
        reason="Default fail-closed lock pending explicit operator authorization.",
        updated_by="system_default",
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
        session.commit()
        return row
    except IntegrityError:
        return session.scalars(
            select(PilotControlState).where(PilotControlState.control_name == control_name)
        ).one()


def set_polling_kill_switch(
    session: Session,
    kill_switch_active: bool,
    opt_in_enabled: bool | None = None,
    reason: str | None = None,
    updated_by: str = "operator",
    control_name: str = DEFAULT_CONTROL_NAME,
    now_utc: datetime | None = None,
) -> PilotControlState:
    """Set durable kill-switch and explicit operator authorization cleanly."""
    clean_actor = str(updated_by or "").strip()
    if not kill_switch_active and clean_actor in {"", "system_default"}:
        raise ValueError("Non-default operator identity required to authorize polling.")

    now = _utc(now_utc) or datetime.now(UTC)
    row = ensure_default_control_state(session, control_name=control_name, now_utc=now)
    row.kill_switch_active = bool(kill_switch_active)
    if opt_in_enabled is not None:
        row.opt_in_enabled = bool(opt_in_enabled)
    elif kill_switch_active:
        row.opt_in_enabled = False

    if not row.kill_switch_active and row.opt_in_enabled:
        row.authorized_at = now
    else:
        row.authorized_at = None

    row.reason = reason or row.reason
    row.updated_by = clean_actor or "operator"
    row.updated_at = now
    session.commit()
    session.refresh(row)
    return row


def inspect_station_provider_metadata(
    session: Session,
    reference_time: datetime | None = None,
    recent_threshold_hours: float = 48.0,
) -> dict[str, Any]:
    """Audit station licenses, provider sensor IDs, and last upstream metadata timestamp (offline-safe)."""
    ref_utc = _utc(reference_time) or datetime.now(UTC)
    stations = list(session.scalars(select(Station).order_by(Station.id.asc())).all())
    station_audits: list[dict[str, Any]] = []
    recently_active_in_meta = 0
    stale_in_meta = 0

    for st in stations:
        pm_sensor = session.scalars(
            select(Sensor).where(Sensor.station_id == st.id, Sensor.parameter == "pm25").limit(1)
        ).first()
        meta: dict[str, Any] = {}
        if st.metadata_json:
            try:
                meta = json.loads(st.metadata_json)
            except ValueError:
                meta = {}

        provider_obj = meta.get("provider") if isinstance(meta.get("provider"), dict) else {}
        licenses_raw = meta.get("licenses") if isinstance(meta.get("licenses"), list) else []
        licenses = [str(lic.get("name", "UNKNOWN")) for lic in licenses_raw if isinstance(lic, dict)]
        dt_last_str = (
            meta.get("datetimeLast", {}).get("utc") if isinstance(meta.get("datetimeLast"), dict) else None
        )
        dt_first_str = (
            meta.get("datetimeFirst", {}).get("utc") if isinstance(meta.get("datetimeFirst"), dict) else None
        )

        fetched_ref = _utc(st.updated_at) or ref_utc
        meta_age_at_fetch: float | None = None
        recently_active = False
        if dt_last_str:
            try:
                dt_last = datetime.fromisoformat(str(dt_last_str))
                meta_age_at_fetch = max(0.0, (fetched_ref - dt_last).total_seconds() / 3600.0)
                if meta_age_at_fetch <= recent_threshold_hours:
                    recently_active = True
            except ValueError:
                meta_age_at_fetch = None

        if recently_active:
            recently_active_in_meta += 1
        else:
            stale_in_meta += 1

        station_audits.append(
            {
                "station_id": st.id,
                "station_name": st.name,
                "provider_location_id": st.provider_location_id,
                "pm25_sensor_id_db": pm_sensor.id if pm_sensor else None,
                "provider_sensor_id": pm_sensor.provider_sensor_id if pm_sensor else None,
                "upstream_provider": provider_obj.get("name", st.provider),
                "licenses": licenses,
                "metadata_first_utc": dt_first_str,
                "metadata_last_utc": dt_last_str,
                "metadata_age_at_fetch_hours": (
                    round(meta_age_at_fetch, 2) if meta_age_at_fetch is not None else None
                ),
                "was_active_at_fetch_snapshot": recently_active,
                "current_live_status": "UNVERIFIED_OFFLINE_MODE",
            }
        )

    return {
        "reference_time": ref_utc.isoformat(),
        "total_stations": len(stations),
        "recently_active_at_snapshot": recently_active_in_meta,
        "stale_at_snapshot": stale_in_meta,
        "stations": station_audits,
    }


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


def _record_audit_run(
    session: Session,
    worker_id: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    fencing_token: int | None = None,
    window_from: datetime | None = None,
    window_to: datetime | None = None,
    sensors_polled: int = 0,
    requests_consumed: int = 0,
    http_attempts: int = 0,
    records_fetched: int = 0,
    records_inserted: int = 0,
    records_revised: int = 0,
    records_deduplicated: int = 0,
    records_invalid: int = 0,
    recovered_from_expired_lease: bool = False,
    error_summary: str | None = None,
) -> PilotIngestAuditRun:
    audit = PilotIngestAuditRun(
        provider="openaq",
        worker_id=worker_id,
        fencing_token=fencing_token,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        window_from=window_from,
        window_to=window_to,
        sensors_polled=sensors_polled,
        requests_consumed=requests_consumed,
        http_attempts=http_attempts,
        records_fetched=records_fetched,
        records_inserted=records_inserted,
        records_revised=records_revised,
        records_deduplicated=records_deduplicated,
        records_invalid=records_invalid,
        recovered_from_expired_lease=recovered_from_expired_lease,
        error_summary=error_summary,
    )
    session.add(audit)
    session.commit()
    return audit


def run_bounded_opt_in_poll(
    session_factory: sessionmaker[Session],
    config: BoundedPilotConfig,
    quota: PilotQuotaTracker | None = None,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
    now_utc: datetime | None = None,
    lease_clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Execute an authorized, per-attempt quota-bounded, renewable-fenced OpenAQ poll."""
    now = _utc(now_utc) or datetime.now(UTC)
    started_at = now
    worker_id = config.worker_id or _default_worker_id()
    db_quota = DurablePilotQuotaTracker(provider="openaq", max_requests_per_day=config.max_requests_per_day)

    def _current_clock() -> datetime:
        if lease_clock is not None:
            return _utc(lease_clock()) or now
        return _utc(now_utc) or datetime.now(UTC)

    def _quota_snapshot(sess: Session) -> tuple[int, int]:
        check_now = _current_clock()
        db_used, db_rem = db_quota.get_usage(sess, check_now)
        if quota is not None:
            used = max(db_used, quota.requests_used)
            rem = min(db_rem, quota.remaining(check_now))
            return used, max(0, rem)
        return db_used, db_rem

    with session_factory() as session:
        # 1. Fail-closed Kill-Switch & Operator Authorization gates (unconditional, no bypass)
        env_kill = os.getenv("SENSORGUARD_POLLING_KILL_SWITCH", "false").lower() in {"1", "true", "yes"}
        ctrl = ensure_default_control_state(session, now_utc=now)
        operator_authorized = bool(
            (not ctrl.kill_switch_active)
            and ctrl.opt_in_enabled
            and ctrl.authorized_at is not None
            and ctrl.updated_by not in {None, "", "system_default"}
        )

        if env_kill or not operator_authorized:
            staleness = audit_station_staleness(
                session,
                reference_time=now,
                caution_hours=config.caution_threshold_hours,
                stale_hours=config.stale_threshold_hours,
            )
            used, rem = _quota_snapshot(session)
            reason = (
                "Environment SENSORGUARD_POLLING_KILL_SWITCH active."
                if env_kill
                else "Durable PilotControlState kill-switch active or missing explicit operator authorization."
            )
            _record_audit_run(
                session,
                worker_id=worker_id,
                started_at=started_at,
                finished_at=now,
                status="SKIPPED_KILL_SWITCH",
                error_summary=reason,
            )
            return {
                "status": "SKIPPED_KILL_SWITCH",
                "polled_at": now.isoformat(),
                "worker_id": worker_id,
                "requests_used_today": used,
                "requests_remaining": rem,
                "http_attempts": 0,
                "records_fetched": 0,
                "records_persisted": 0,
                "records_revised": 0,
                "staleness": staleness,
            }

        if not config.opt_in_enabled:
            staleness = audit_station_staleness(
                session,
                reference_time=now,
                caution_hours=config.caution_threshold_hours,
                stale_hours=config.stale_threshold_hours,
            )
            used, rem = _quota_snapshot(session)
            _record_audit_run(
                session,
                worker_id=worker_id,
                started_at=started_at,
                finished_at=now,
                status="SKIPPED_OPT_OUT",
                error_summary="BoundedPilotConfig.opt_in_enabled is False.",
            )
            return {
                "status": "SKIPPED_OPT_OUT",
                "polled_at": now.isoformat(),
                "worker_id": worker_id,
                "requests_used_today": used,
                "requests_remaining": rem,
                "http_attempts": 0,
                "records_fetched": 0,
                "records_persisted": 0,
                "records_revised": 0,
                "staleness": staleness,
            }

        resolved_key = api_key or os.getenv("OPENAQ_API_KEY")
        if not resolved_key and transport is None:
            staleness = audit_station_staleness(
                session,
                reference_time=now,
                caution_hours=config.caution_threshold_hours,
                stale_hours=config.stale_threshold_hours,
            )
            used, rem = _quota_snapshot(session)
            _record_audit_run(
                session,
                worker_id=worker_id,
                started_at=started_at,
                finished_at=now,
                status="SKIPPED_NO_CREDENTIALS",
                error_summary="OPENAQ_API_KEY missing and no mock transport provided.",
            )
            return {
                "status": "SKIPPED_NO_CREDENTIALS",
                "polled_at": now.isoformat(),
                "worker_id": worker_id,
                "requests_used_today": used,
                "requests_remaining": rem,
                "http_attempts": 0,
                "records_fetched": 0,
                "records_persisted": 0,
                "records_revised": 0,
                "staleness": staleness,
            }

        # 2. Single-leader distributed polling lease with monotonic fencing token
        lease_acq = acquire_poll_lease(
            session,
            holder_id=worker_id,
            lease_name=config.lease_name,
            ttl_seconds=config.lease_ttl_seconds,
            now_utc=_current_clock(),
        )
        if not lease_acq.acquired or lease_acq.fencing_token is None:
            staleness = audit_station_staleness(
                session,
                reference_time=now,
                caution_hours=config.caution_threshold_hours,
                stale_hours=config.stale_threshold_hours,
            )
            used, rem = _quota_snapshot(session)
            _record_audit_run(
                session,
                worker_id=worker_id,
                started_at=started_at,
                finished_at=now,
                status="SKIPPED_NO_LEASE",
                error_summary=f"Lease '{config.lease_name}' held by another active worker.",
            )
            return {
                "status": "SKIPPED_NO_LEASE",
                "polled_at": now.isoformat(),
                "worker_id": worker_id,
                "requests_used_today": used,
                "requests_remaining": rem,
                "http_attempts": 0,
                "records_fetched": 0,
                "records_persisted": 0,
                "records_revised": 0,
                "staleness": staleness,
            }

        my_fencing_token = lease_acq.fencing_token
        recovered_expired = lease_acq.recovered_from_expired_lease
        window_dt_from = now - timedelta(hours=config.lookback_hours)
        window_from = window_dt_from.strftime("%Y-%m-%dT%H:%M:%SZ")
        window_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        fetched_total = 0
        persisted_total = 0
        revised_total = 0
        dedup_total = 0
        invalid_total = 0
        sensors_polled = 0
        exact_http_attempts = 0
        quota_halted = False
        rate_limited_429 = False
        stale_fencing_abort = False
        error_msg: str | None = None

        def _reserve_single_http_attempt(_method: str, _endpoint: str, _attempt_idx: int) -> None:
            nonlocal exact_http_attempts
            attempt_ts = _current_clock()
            with _ATTEMPT_LOCK:
                if quota is not None and quota.remaining(attempt_ts) < 1:
                    raise QuotaExhaustedError("In-memory PilotQuotaTracker daily limit reached.")
                with session_factory() as q_sess:
                    if not db_quota.try_consume(q_sess, count=1, now_utc=attempt_ts):
                        raise QuotaExhaustedError(
                            f"Durable daily OpenAQ quota exhausted (cap={config.max_requests_per_day})."
                        )
                if quota is not None:
                    quota.try_consume(1, attempt_ts)
                exact_http_attempts += 1

        try:
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

            client = OpenAQClient(
                api_key=resolved_key or "mock-test-key",
                transport=transport,
                max_retries=config.max_retries_per_request,
                base_backoff_seconds=0.02 if transport is not None else 0.5,
                attempt_guard=_reserve_single_http_attempt,
            )
            pipeline = IngestionPipeline(session)

            for sensor in sensors:
                # Heartbeat renewal + ownership fencing before every sensor batch
                with session_factory() as l_sess:
                    renewed = renew_poll_lease(
                        l_sess,
                        holder_id=worker_id,
                        fencing_token=my_fencing_token,
                        lease_name=config.lease_name,
                        ttl_seconds=config.lease_ttl_seconds,
                        now_utc=_current_clock(),
                    )
                if not renewed:
                    stale_fencing_abort = True
                    error_msg = (
                        f"Lost lease ownership before polling sensor {sensor.provider_sensor_id} "
                        f"(stale fencing_token={my_fencing_token})."
                    )
                    session.rollback()
                    break

                try:
                    res = pipeline.ingest_sensor_hours(
                        sensor_db_id=sensor.id,
                        provider_sensor_id=sensor.provider_sensor_id,
                        client=client,
                        datetime_from=window_from,
                        datetime_to=window_to,
                        max_pages=config.max_pages_per_sensor,
                        page_size=config.page_size,
                        auto_commit=False,
                    )
                    fetched_total += res.records_fetched
                    persisted_total += res.records_inserted
                    revised_total += res.records_revised
                    dedup_total += res.records_deduplicated
                    invalid_total += res.records_invalid
                    sensors_polled += 1
                except QuotaExhaustedError as q_exc:
                    quota_halted = True
                    error_msg = str(q_exc)
                    logger.warning("Per-attempt quota exhausted: %s", error_msg)
                    break
                except httpx.HTTPStatusError as http_exc:
                    if http_exc.response is not None and http_exc.response.status_code == 429:
                        rate_limited_429 = True
                        error_msg = f"HTTP 429 rate limited on sensor {sensor.provider_sensor_id}"
                        logger.warning(error_msg)
                        break
                    error_msg = str(http_exc)
                    raise

            # Final commit-time fencing gate: refuse stale commit if another worker took over lease!
            if not stale_fencing_abort:
                with session_factory() as fence_sess:
                    fence_valid = verify_fencing_token(
                        fence_sess,
                        holder_id=worker_id,
                        fencing_token=my_fencing_token,
                        lease_name=config.lease_name,
                        now_utc=_current_clock(),
                    )
                if not fence_valid:
                    stale_fencing_abort = True
                    error_msg = (
                        f"Commit aborted by ownership fence: lease expired or preempted "
                        f"(stale fencing_token={my_fencing_token})."
                    )
                    session.rollback()
                    fetched_total = 0
                    persisted_total = 0
                    revised_total = 0
                    dedup_total = 0
                    invalid_total = 0

            if not stale_fencing_abort:
                session.commit()

            final_status = (
                "SKIPPED_STALE_FENCING_TOKEN"
                if stale_fencing_abort
                else (
                    "HTTP_429_BACKOFF"
                    if rate_limited_429
                    else ("QUOTA_EXHAUSTED" if quota_halted else "SUCCESS")
                )
            )
            staleness = audit_station_staleness(
                session,
                reference_time=now,
                caution_hours=config.caution_threshold_hours,
                stale_hours=config.stale_threshold_hours,
            )
            used, rem = _quota_snapshot(session)
            _record_audit_run(
                session,
                worker_id=worker_id,
                fencing_token=my_fencing_token,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                status=final_status,
                window_from=window_dt_from,
                window_to=now,
                sensors_polled=sensors_polled,
                requests_consumed=exact_http_attempts,
                http_attempts=exact_http_attempts,
                records_fetched=fetched_total,
                records_inserted=persisted_total,
                records_revised=revised_total,
                records_deduplicated=dedup_total,
                records_invalid=invalid_total,
                recovered_from_expired_lease=recovered_expired,
                error_summary=error_msg,
            )
            return {
                "status": final_status,
                "polled_at": now.isoformat(),
                "worker_id": worker_id,
                "fencing_token": my_fencing_token,
                "recovered_from_expired_lease": recovered_expired,
                "window_from": window_from,
                "window_to": window_to,
                "sensors_polled": sensors_polled,
                "requests_used_today": used,
                "requests_remaining": rem,
                "http_attempts": exact_http_attempts,
                "records_fetched": fetched_total,
                "records_inserted": persisted_total,
                "records_persisted": persisted_total,
                "records_revised": revised_total,
                "records_deduplicated": dedup_total,
                "records_invalid": invalid_total,
                "error_summary": error_msg,
                "staleness": staleness,
            }
        except Exception as exc:
            session.rollback()
            _record_audit_run(
                session,
                worker_id=worker_id,
                fencing_token=my_fencing_token,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                status="FAILED",
                window_from=window_dt_from,
                window_to=now,
                sensors_polled=sensors_polled,
                requests_consumed=exact_http_attempts,
                http_attempts=exact_http_attempts,
                records_fetched=fetched_total,
                records_inserted=persisted_total,
                records_revised=revised_total,
                records_deduplicated=dedup_total,
                records_invalid=invalid_total,
                recovered_from_expired_lease=recovered_expired,
                error_summary=str(exc),
            )
            raise
        finally:
            if not stale_fencing_abort:
                try:
                    with session_factory() as rel_sess:
                        release_poll_lease(
                            rel_sess,
                            holder_id=worker_id,
                            fencing_token=my_fencing_token,
                            lease_name=config.lease_name,
                            now_utc=_current_clock(),
                        )
                except Exception:
                    logger.exception(
                        "Failed releasing fenced lease '%s' token=%s for worker '%s'",
                        config.lease_name,
                        my_fencing_token,
                        worker_id,
                    )


def reconcile_delayed_ground_truth(
    session: Session,
    run_id: int | None = None,
    delay_threshold_hours: float = 1.5,
) -> dict[str, Any]:
    """Reconcile persisted ForecastRecord rows against arrived Observation truth labels."""
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
