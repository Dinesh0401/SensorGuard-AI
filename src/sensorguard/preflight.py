"""Bounded Stage 1 OpenAQ station & PM2.5 availability pre-flight runner.

Strictly defaults to dry-run (0 network calls). Executing real network checks requires:
1. `--execute-once` flag
2. Environment kill switch disabled (`SENSORGUARD_POLLING_KILL_SWITCH != true`)
3. Stage 1 database kill switch disabled (`openaq_stage1_preflight`)
4. An unused, single-use DB `Stage1PreflightAuthorization` approval (`status='AUTHORIZED'`)
   with explicit `approval_id` atomically transitioned `AUTHORIZED -> CLAIMED_IN_PROGRESS ->
   COMPLETED | QUOTA_EXHAUSTED | COMPLETED_WITH_ERRORS | FAILED_INCOMPLETE`.
5. Recurring `PilotControlState('openaq_live_polling').opt_in_enabled` remains `False`.

Each outbound HTTP attempt is atomically debited against BOTH the single-use approval ledger
AND `DurablePilotQuotaTracker` before leaving the host. Total authorized requests cannot be reset
by restarting the CLI/worker; incomplete/crashed approvals require an explicit audited operator
recovery approval creating a new `approval_id`.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from sensorguard.db.schema import (
    PilotControlState,
    PilotIngestAuditRun,
    Sensor,
    Stage1PreflightAuthorization,
    Station,
)
from sensorguard.pilot_monitor import DEFAULT_CONTROL_NAME, DurablePilotQuotaTracker
from sensorguard.providers import OpenAQClient, QuotaExhaustedError, parse_openaq_datetime

STAGE1_CONTROL_NAME = "openaq_stage1_preflight"
STAGE1_HARD_MAX_ATTEMPTS = 16
STAGE1_ARCHIVED_LOCATION_IDS: tuple[int, ...] = (154, 157, 141, 153, 698, 804, 1795, 2312)
DEFAULT_PREFLIGHT_ARTIFACT_DIR = Path("artifacts/preflight")
RECOVERABLE_APPROVAL_STATES = frozenset({"CLAIMED_IN_PROGRESS", "FAILED_INCOMPLETE", "QUOTA_EXHAUSTED"})
_PREFLIGHT_LOCK = threading.Lock()


@dataclass(frozen=True)
class Stage1PreflightConfig:
    """Fail-closed configuration for genuinely single-use bounded Stage 1 station check."""

    execute_once: bool = False
    approval_id: str | None = None
    max_attempts: int = STAGE1_HARD_MAX_ATTEMPTS
    max_daily_quota: int = 48
    recent_hours: float = 48.0
    location_ids: tuple[int, ...] = STAGE1_ARCHIVED_LOCATION_IDS
    authorized_by: str | None = None
    worker_id: str | None = None
    output_path: Path | None = None
    write_artifact: bool = True

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_attempts) <= STAGE1_HARD_MAX_ATTEMPTS:
            raise ValueError(
                f"Stage 1 max_attempts must be between 1 and {STAGE1_HARD_MAX_ATTEMPTS} (got {self.max_attempts})."
            )
        if float(self.recent_hours) <= 0.0:
            raise ValueError(f"recent_hours must be strictly positive (got {self.recent_hours}).")


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-pid{os.getpid()}-{uuid.uuid4().hex[:8]}"


def redact_secrets(text: str | None, extra_secrets: tuple[str | None, ...] = ()) -> str:
    """Remove API keys and URL passwords from any logged or persisted message."""
    if not text:
        return ""
    cleaned = str(text)
    cleaned = re.sub(
        r"(?i)(x-api-key|api_key|token|password|secret)=([^\s,;'\"]+)", r"\1=[REDACTED]", cleaned
    )
    cleaned = re.sub(r"://([^:@/\s]+):([^@/\s]+)@", r"://\1:[REDACTED]@", cleaned)
    for sec in extra_secrets:
        if sec and len(sec) >= 4:
            cleaned = cleaned.replace(sec, "[REDACTED]")
    return cleaned


def _ensure_stage1_control_gate(session: Session, actor: str, reason: str, now: datetime) -> None:
    """Record Stage 1 gate active in PilotControlState without EVER enabling recurring polling."""
    ctrl = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == STAGE1_CONTROL_NAME)
    ).first()
    if ctrl is None:
        session.add(
            PilotControlState(
                control_name=STAGE1_CONTROL_NAME,
                kill_switch_active=False,
                opt_in_enabled=False,
                authorized_at=now,
                reason=reason,
                updated_by=actor,
                updated_at=now,
            )
        )
    else:
        ctrl.kill_switch_active = False
        ctrl.opt_in_enabled = False
        ctrl.authorized_at = now
        ctrl.reason = reason
        ctrl.updated_by = actor
        ctrl.updated_at = now


def authorize_stage1_preflight_db(
    session: Session,
    authorized_by: str,
    reason: str = "Explicit single-use Stage 1 station availability check authorization",
    max_attempts: int = STAGE1_HARD_MAX_ATTEMPTS,
    now_utc: datetime | None = None,
) -> Stage1PreflightAuthorization:
    """Mint a brand-new single-use Stage 1 approval token while keeping recurring polling locked."""
    actor = str(authorized_by or "").strip()
    if actor in {"", "system_default"}:
        raise ValueError("Non-default operator identity required to authorize Stage 1 pre-flight check.")
    if not 1 <= int(max_attempts) <= STAGE1_HARD_MAX_ATTEMPTS:
        raise ValueError(
            f"Stage 1 max_attempts must be between 1 and {STAGE1_HARD_MAX_ATTEMPTS} (got {max_attempts})."
        )
    env_kill = os.getenv("SENSORGUARD_POLLING_KILL_SWITCH", "false").lower() in {"1", "true", "yes"}
    if env_kill:
        raise ValueError("Cannot mint Stage 1 authorization while SENSORGUARD_POLLING_KILL_SWITCH is active.")

    live_ctrl = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
    ).first()
    if live_ctrl is not None and live_ctrl.opt_in_enabled:
        raise ValueError(
            "Recurring PilotControlState 'openaq_live_polling' has opt_in_enabled=True; keep recurring polling locked."
        )

    now = _to_utc(now_utc) or datetime.now(UTC)
    approval_id = f"s1_{uuid.uuid4().hex[:16]}"
    token = Stage1PreflightAuthorization(
        approval_id=approval_id,
        status="AUTHORIZED",
        execution_id=None,
        authorized_by=actor,
        authorized_at=now,
        reason=reason,
        max_attempts=int(max_attempts),
        attempts_consumed=0,
        recovered_from_approval_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(token)
    _ensure_stage1_control_gate(session, actor=actor, reason=reason, now=now)
    session.commit()
    session.refresh(token)
    return token


def authorize_stage1_recovery_db(
    session: Session,
    prior_approval_id: str,
    authorized_by: str,
    reason: str,
    max_attempts: int | None = None,
    allow_budget_reset: bool = False,
    now_utc: datetime | None = None,
) -> Stage1PreflightAuthorization:
    """Explicitly recover an incomplete/exhausted Stage 1 run by superseding old row and minting new approval."""
    actor = str(authorized_by or "").strip()
    if actor in {"", "system_default"}:
        raise ValueError("Non-default operator identity required for Stage 1 recovery approval.")
    clean_reason = str(reason or "").strip()
    if len(clean_reason) < 8:
        raise ValueError("Detailed operator audit reason (>= 8 chars) required for Stage 1 recovery.")

    prior = session.scalars(
        select(Stage1PreflightAuthorization).where(
            Stage1PreflightAuthorization.approval_id == prior_approval_id
        )
    ).first()
    if prior is None:
        raise ValueError(f"Prior Stage 1 approval_id '{prior_approval_id}' not found.")
    if prior.status not in RECOVERABLE_APPROVAL_STATES:
        raise ValueError(
            f"Approval '{prior_approval_id}' has status '{prior.status}', which cannot be silently recovered."
        )

    remaining_cap = max(0, int(prior.max_attempts) - int(prior.attempts_consumed))
    if not allow_budget_reset:
        if remaining_cap <= 0:
            raise ValueError(
                f"Prior approval '{prior_approval_id}' already consumed {prior.attempts_consumed}/"
                f"{prior.max_attempts} authorized requests; pass allow_budget_reset=True only under separate budget approval."
            )
        target_cap = min(remaining_cap, int(max_attempts or remaining_cap), STAGE1_HARD_MAX_ATTEMPTS)
    else:
        target_cap = int(max_attempts or STAGE1_HARD_MAX_ATTEMPTS)
        if not 1 <= target_cap <= STAGE1_HARD_MAX_ATTEMPTS:
            raise ValueError(
                f"Recovery max_attempts must be between 1 and {STAGE1_HARD_MAX_ATTEMPTS} (got {target_cap})."
            )

    now = _to_utc(now_utc) or datetime.now(UTC)
    upd = (
        update(Stage1PreflightAuthorization)
        .where(
            Stage1PreflightAuthorization.approval_id == prior_approval_id,
            Stage1PreflightAuthorization.status.in_(list(RECOVERABLE_APPROVAL_STATES)),
        )
        .values(
            status="SUPERSEDED_RECOVERED",
            finished_at=prior.finished_at or now,
            error_summary=(f"{prior.error_summary or ''} | Superseded by {actor}: {clean_reason}").strip(
                " |"
            ),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    res = session.execute(upd)
    if int(res.rowcount or 0) != 1:
        session.rollback()
        raise ValueError(f"Concurrent state change prevented recovery of '{prior_approval_id}'.")

    new_approval_id = f"s1_{uuid.uuid4().hex[:16]}"
    token = Stage1PreflightAuthorization(
        approval_id=new_approval_id,
        status="AUTHORIZED",
        execution_id=None,
        authorized_by=actor,
        authorized_at=now,
        reason=f"RECOVERY from {prior_approval_id}: {clean_reason}",
        max_attempts=target_cap,
        attempts_consumed=0,
        recovered_from_approval_id=prior_approval_id,
        created_at=now,
        updated_at=now,
    )
    session.add(token)
    _ensure_stage1_control_gate(session, actor=actor, reason=token.reason or clean_reason, now=now)
    session.commit()
    session.refresh(token)
    return token


def mark_stage1_incomplete_or_aborted(
    session: Session,
    approval_id: str,
    execution_id: str,
    error_summary: str,
    now_utc: datetime | None = None,
) -> bool:
    """Persist FAILED_INCOMPLETE state on a claimed Stage 1 approval cleanly without resetting budget."""
    now = _to_utc(now_utc) or datetime.now(UTC)
    upd = (
        update(Stage1PreflightAuthorization)
        .where(
            Stage1PreflightAuthorization.approval_id == approval_id,
            Stage1PreflightAuthorization.execution_id == execution_id,
            Stage1PreflightAuthorization.status == "CLAIMED_IN_PROGRESS",
        )
        .values(
            status="FAILED_INCOMPLETE",
            finished_at=now,
            error_summary=error_summary[:1000],
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    res = session.execute(upd)
    session.commit()
    return int(res.rowcount or 0) == 1


def _claim_single_use_approval(
    session: Session,
    config: Stage1PreflightConfig,
    worker_id: str,
    now: datetime,
) -> tuple[bool, str, str, Stage1PreflightAuthorization | None, str | None]:
    """Enforce kill switches & atomically transition AUTHORIZED -> CLAIMED_IN_PROGRESS once only."""
    env_kill = os.getenv("SENSORGUARD_POLLING_KILL_SWITCH", "false").lower() in {"1", "true", "yes"}
    if env_kill:
        return (
            False,
            "BLOCKED_KILL_SWITCH",
            "Environment SENSORGUARD_POLLING_KILL_SWITCH is active.",
            None,
            None,
        )

    live_ctrl = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
    ).first()
    if live_ctrl is not None and live_ctrl.opt_in_enabled:
        return (
            False,
            "BLOCKED_RECURRING_OPT_IN_UNEXPECTED",
            "PilotControlState 'openaq_live_polling' has opt_in_enabled=True; keep recurring polling locked.",
            None,
            None,
        )

    s1_ctrl = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == STAGE1_CONTROL_NAME)
    ).first()
    if s1_ctrl is not None and s1_ctrl.kill_switch_active:
        return False, "BLOCKED_KILL_SWITCH", "Database Stage 1 kill switch is active.", None, None

    req_approval_id = str(config.approval_id or os.getenv("SENSORGUARD_STAGE1_APPROVAL_ID") or "").strip()
    if req_approval_id:
        candidate = session.scalars(
            select(Stage1PreflightAuthorization).where(
                Stage1PreflightAuthorization.approval_id == req_approval_id
            )
        ).first()
        if candidate is None:
            return (
                False,
                "BLOCKED_NO_SINGLE_USE_APPROVAL",
                f"Stage 1 approval_id '{req_approval_id}' does not exist in database.",
                None,
                None,
            )
        if candidate.status != "AUTHORIZED":
            return (
                False,
                "BLOCKED_APPROVAL_ALREADY_USED",
                (
                    f"Single-use Stage 1 approval '{req_approval_id}' already in status '{candidate.status}' "
                    f"(attempts_consumed={candidate.attempts_consumed}/{candidate.max_attempts}); "
                    "cannot reuse without an audited recovery/new operator authorization."
                ),
                None,
                None,
            )
        target_id = candidate.approval_id
    else:
        unused = list(
            session.scalars(
                select(Stage1PreflightAuthorization)
                .where(Stage1PreflightAuthorization.status == "AUTHORIZED")
                .order_by(Stage1PreflightAuthorization.id.desc())
            ).all()
        )
        if not unused:
            return (
                False,
                "BLOCKED_NO_SINGLE_USE_APPROVAL",
                (
                    "No unused single-use Stage 1 approval exists in database. "
                    "Operator must authorize via 'authorize-stage1' and pass --approval-id."
                ),
                None,
                None,
            )
        if len(unused) > 1:
            return (
                False,
                "BLOCKED_AMBIGUOUS_APPROVALS",
                "Multiple unused Stage 1 approvals exist; specify --approval-id explicitly.",
                None,
                None,
            )
        target_id = unused[0].approval_id

    exec_id = f"exec_{uuid.uuid4().hex[:16]}"
    claim_stmt = (
        update(Stage1PreflightAuthorization)
        .where(
            Stage1PreflightAuthorization.approval_id == target_id,
            Stage1PreflightAuthorization.status == "AUTHORIZED",
            Stage1PreflightAuthorization.attempts_consumed < Stage1PreflightAuthorization.max_attempts,
        )
        .values(
            status="CLAIMED_IN_PROGRESS",
            execution_id=exec_id,
            claimed_at=now,
            claimed_by_worker=worker_id,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    try:
        res = session.execute(claim_stmt)
        session.commit()
    except IntegrityError:
        session.rollback()
        return (
            False,
            "BLOCKED_CONCURRENT_CLAIM",
            f"Concurrent execution conflict on single-use Stage 1 approval '{target_id}'.",
            None,
            None,
        )
    if int(res.rowcount or 0) != 1:
        session.rollback()
        return (
            False,
            "BLOCKED_APPROVAL_ALREADY_USED",
            f"Atomic claim failed for '{target_id}': already claimed or consumed.",
            None,
            None,
        )

    claimed = session.scalars(
        select(Stage1PreflightAuthorization).where(Stage1PreflightAuthorization.approval_id == target_id)
    ).one()
    return True, f"CLAIMED:{claimed.authorized_by}", exec_id, claimed, target_id


def _load_archived_station_targets(
    session: Session,
    location_ids: tuple[int, ...],
) -> list[dict[str, Any]]:
    """Load archived UK station & PM2.5 sensor inventory from DB preserving configured priority order."""
    order_rank = {loc_id: idx for idx, loc_id in enumerate(location_ids)}
    stations = list(
        session.scalars(select(Station).where(Station.provider_location_id.in_(list(location_ids)))).all()
    )
    stations.sort(key=lambda s: order_rank.get(int(s.provider_location_id), 999))

    targets: list[dict[str, Any]] = []
    seen_locs: set[int] = set()
    for st in stations:
        loc_id = int(st.provider_location_id)
        seen_locs.add(loc_id)
        sensor = session.scalars(
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
        dt_last = (
            meta.get("datetimeLast", {}).get("utc") if isinstance(meta.get("datetimeLast"), dict) else None
        )
        targets.append(
            {
                "station_id": int(st.id),
                "station_name": st.name,
                "provider_location_id": loc_id,
                "configured_pm25_sensor_id": int(sensor.provider_sensor_id) if sensor else None,
                "stored_provider_name": str(provider_obj.get("name") or st.provider),
                "stored_licenses": licenses,
                "stored_metadata_last_utc": dt_last,
            }
        )

    for loc_id in location_ids:
        if loc_id not in seen_locs:
            targets.append(
                {
                    "station_id": None,
                    "station_name": f"OpenAQ Location #{loc_id}",
                    "provider_location_id": int(loc_id),
                    "configured_pm25_sensor_id": None,
                    "stored_provider_name": "openaq",
                    "stored_licenses": [],
                    "stored_metadata_last_utc": None,
                }
            )
    return targets


def _extract_location_attribution(
    loc_obj: dict[str, Any], fallback_provider: str, fallback_licenses: list[str]
) -> dict[str, Any]:
    provider_obj = loc_obj.get("provider") if isinstance(loc_obj.get("provider"), dict) else {}
    owner_obj = loc_obj.get("owner") if isinstance(loc_obj.get("owner"), dict) else {}
    licenses_raw = loc_obj.get("licenses") if isinstance(loc_obj.get("licenses"), list) else []
    licenses = [
        str(lic.get("name") or lic.get("attribution") or "UNKNOWN")
        for lic in licenses_raw
        if isinstance(lic, dict)
    ]
    return {
        "upstream_provider": str(provider_obj.get("name") or fallback_provider or "openaq"),
        "owner": str(owner_obj.get("name") or ""),
        "licenses": licenses or fallback_licenses,
    }


def _find_pm25_sensor_id(loc_obj: dict[str, Any], fallback_sensor_id: int | None) -> int | None:
    for item in loc_obj.get("sensors") or []:
        if not isinstance(item, dict):
            continue
        param = item.get("parameter") if isinstance(item.get("parameter"), dict) else {}
        pname = str(param.get("name") or "").strip().lower().replace(".", "")
        if pname == "pm25" and item.get("id") is not None:
            return int(item["id"])
    return fallback_sensor_id


def _write_preflight_artifact(
    payload: dict[str, Any], config: Stage1PreflightConfig, ts_now: datetime
) -> str | None:
    if not config.write_artifact and config.output_path is None:
        return None
    target = (
        Path(config.output_path)
        if config.output_path is not None
        else DEFAULT_PREFLIGHT_ARTIFACT_DIR / f"stage1_preflight_{ts_now.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, default=str)
    target.write_text(rendered + "\n", encoding="utf-8")
    return str(target)


def _blocked_response(
    status_code: str,
    error_msg: str,
    now: datetime,
    hard_cap: int,
    targets: list[dict[str, Any]],
    recurring_opt_in_state: bool,
    config: Stage1PreflightConfig,
    approval_id: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "stage": "stage1_preflight",
        "status": status_code,
        "dry_run": False,
        "execute_once": True,
        "approval_id": approval_id,
        "execution_id": None,
        "checked_at_utc": now.isoformat(),
        "hard_attempt_cap": hard_cap,
        "attempts_consumed": 0,
        "budget_exhausted": False,
        "recurring_polling_opt_in_enabled": recurring_opt_in_state,
        "summary": {"total_locations": len(targets), "active": 0, "inactive": 0, "unknown": len(targets)},
        "locations": [],
        "errors": [error_msg],
    }
    out["artifact_path"] = _write_preflight_artifact(out, config, now)
    return out


def run_stage1_preflight(
    session_factory: sessionmaker[Session],
    config: Stage1PreflightConfig | None = None,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Execute dry-run or genuinely single-use DB-claimed Stage 1 OpenAQ availability check."""
    cfg = config or Stage1PreflightConfig()
    now = _to_utc(now_utc) or datetime.now(UTC)
    requested_cap = min(STAGE1_HARD_MAX_ATTEMPTS, int(cfg.max_attempts))
    worker_id = cfg.worker_id or _default_worker_id()
    secret_env_key = os.getenv("OPENAQ_API_KEY")
    db_env_url = os.getenv("DATABASE_URL")
    secrets_tuple = (api_key, secret_env_key, db_env_url)

    with session_factory() as session:
        targets = _load_archived_station_targets(session, cfg.location_ids)
        recurring_ctrl = session.scalars(
            select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
        ).first()
        recurring_opt_in_state = bool(recurring_ctrl.opt_in_enabled) if recurring_ctrl is not None else False

        # Default strictly to dry-run (0 network calls, does NOT consume single-use approval)
        if not cfg.execute_once:
            try:
                used_q, rem_q = DurablePilotQuotaTracker(max_requests_per_day=cfg.max_daily_quota).get_usage(
                    session, now_utc=now
                )
                unused_approvals = [
                    row.approval_id
                    for row in session.scalars(
                        select(Stage1PreflightAuthorization).where(
                            Stage1PreflightAuthorization.status == "AUTHORIZED"
                        )
                    ).all()
                ]
            except SQLAlchemyError:
                session.rollback()
                used_q, rem_q = 0, cfg.max_daily_quota
                unused_approvals = []

            dry_rows = [
                {
                    "provider_location_id": t["provider_location_id"],
                    "station_name": t["station_name"],
                    "status": "unknown",
                    "pm25_sensor_id": t["configured_pm25_sensor_id"],
                    "last_observed_at": None,
                    "metadata_last_utc": t["stored_metadata_last_utc"],
                    "source_attribution": {
                        "upstream_provider": t["stored_provider_name"],
                        "licenses": t["stored_licenses"],
                    },
                    "attempts_consumed": 0,
                    "errors": ["dry_run: zero external HTTP calls executed"],
                }
                for t in targets
            ]
            out = {
                "stage": "stage1_preflight",
                "status": "DRY_RUN_NO_NETWORK",
                "dry_run": True,
                "execute_once": False,
                "checked_at_utc": now.isoformat(),
                "hard_attempt_cap": requested_cap,
                "attempts_consumed": 0,
                "daily_quota_used_today": used_q,
                "daily_quota_remaining": rem_q,
                "daily_quota_cap": cfg.max_daily_quota,
                "budget_exhausted": False,
                "recurring_polling_opt_in_enabled": recurring_opt_in_state,
                "pending_single_use_approvals": unused_approvals,
                "summary": {
                    "total_locations": len(dry_rows),
                    "active": 0,
                    "inactive": 0,
                    "unknown": len(dry_rows),
                },
                "locations": dry_rows,
                "errors": [],
                "config": {
                    **asdict(cfg),
                    "location_ids": list(cfg.location_ids),
                    "output_path": str(cfg.output_path) if cfg.output_path else None,
                },
            }
            out["artifact_path"] = _write_preflight_artifact(out, cfg, now)
            return out

        resolved_key = api_key or secret_env_key
        if not resolved_key and transport is None:
            return _blocked_response(
                "BLOCKED_NO_CREDENTIALS",
                "OPENAQ_API_KEY missing in environment and no mock transport supplied.",
                now,
                requested_cap,
                targets,
                recurring_opt_in_state,
                cfg,
                approval_id=cfg.approval_id,
            )

        # Verify durable DB tables are healthy BEFORE claiming or executing network calls
        quota_tracker = DurablePilotQuotaTracker(provider="openaq", max_requests_per_day=cfg.max_daily_quota)
        try:
            quota_tracker.get_usage(session, now_utc=now)
            claim_ok, auth_status, claim_info, claimed_row, approval_id = _claim_single_use_approval(
                session, cfg, worker_id=worker_id, now=now
            )
        except SQLAlchemyError as db_exc:
            session.rollback()
            safe_err = redact_secrets(str(db_exc), secrets_tuple)
            return _blocked_response(
                "BLOCKED_DB_UNAVAILABLE",
                f"Durable database check/claim failed closed with zero HTTP calls: {safe_err}",
                now,
                requested_cap,
                targets,
                recurring_opt_in_state,
                cfg,
                approval_id=cfg.approval_id,
            )

        if not claim_ok or claimed_row is None or approval_id is None:
            return _blocked_response(
                auth_status,
                redact_secrets(claim_info, secrets_tuple),
                now,
                requested_cap,
                targets,
                recurring_opt_in_state,
                cfg,
                approval_id=cfg.approval_id,
            )

        execution_id = claim_info
        effective_cap = min(
            requested_cap,
            max(0, int(claimed_row.max_attempts) - int(claimed_row.attempts_consumed)),
        )
        run_attempts = 0
        budget_exhausted = False
        db_failure_msg: str | None = None
        current_loc_attempts: dict[int, int] = {t["provider_location_id"]: 0 for t in targets}
        active_loc_context: list[int] = [0]

        def _stage1_attempt_guard(_method: str, _endpoint: str, _attempt_idx: int) -> None:
            nonlocal run_attempts, budget_exhausted, db_failure_msg
            with _PREFLIGHT_LOCK:
                if run_attempts >= effective_cap:
                    budget_exhausted = True
                    raise QuotaExhaustedError(
                        f"Stage 1 single-use approval budget exhausted ({effective_cap}/{claimed_row.max_attempts})."
                    )
                attempt_ts = datetime.now(UTC)
                try:
                    with session_factory() as q_sess:
                        # 1. Atomically consume 1 attempt against the claimed single-use DB approval token
                        tok_upd = (
                            update(Stage1PreflightAuthorization)
                            .where(
                                Stage1PreflightAuthorization.approval_id == approval_id,
                                Stage1PreflightAuthorization.execution_id == execution_id,
                                Stage1PreflightAuthorization.status == "CLAIMED_IN_PROGRESS",
                                Stage1PreflightAuthorization.attempts_consumed + 1
                                <= Stage1PreflightAuthorization.max_attempts,
                            )
                            .values(
                                attempts_consumed=Stage1PreflightAuthorization.attempts_consumed + 1,
                                updated_at=attempt_ts,
                            )
                            .execution_options(synchronize_session=False)
                        )
                        tok_res = q_sess.execute(tok_upd)
                        if int(tok_res.rowcount or 0) != 1:
                            q_sess.rollback()
                            budget_exhausted = True
                            raise QuotaExhaustedError(
                                f"Single-use Stage 1 approval '{approval_id}' exhausted or no longer claimed."
                            )

                        # 2. Atomically charge global daily durable provider quota in the same transaction
                        q_row = quota_tracker._ensure_day_row(q_sess, now.strftime("%Y-%m-%d"), now)
                        daily_cap = min(int(q_row.max_requests_per_day), quota_tracker.max_requests_per_day)
                        q_upd = (
                            update(type(q_row))
                            .where(
                                type(q_row).id == q_row.id,
                                type(q_row).requests_used + 1 <= daily_cap,
                            )
                            .values(
                                requests_used=type(q_row).requests_used + 1,
                                updated_at=attempt_ts,
                            )
                            .execution_options(synchronize_session=False)
                        )
                        q_res = q_sess.execute(q_upd)
                        if int(q_res.rowcount or 0) != 1:
                            q_sess.rollback()
                            budget_exhausted = True
                            raise QuotaExhaustedError(
                                f"Durable daily OpenAQ quota exhausted (cap={cfg.max_daily_quota}/day)."
                            )
                        q_sess.commit()
                except QuotaExhaustedError:
                    raise
                except SQLAlchemyError as exc:
                    db_failure_msg = redact_secrets(str(exc), secrets_tuple)
                    raise QuotaExhaustedError(
                        f"Durable quota/approval DB failure before outbound HTTP call: {db_failure_msg}"
                    ) from exc

                run_attempts += 1
                loc_key = active_loc_context[0]
                if loc_key in current_loc_attempts:
                    current_loc_attempts[loc_key] += 1

        try:
            client = OpenAQClient(
                api_key=resolved_key or "mock-stage1-key",
                transport=transport,
                max_retries=1,
                base_backoff_seconds=0.01 if transport is not None else 0.3,
                rate_limit_per_second=50.0 if transport is not None else 2.0,
                attempt_guard=_stage1_attempt_guard,
            )

            loc_reports: dict[int, dict[str, Any]] = {}
            phase1_candidates: list[tuple[int, dict[str, Any]]] = []
            recent_cutoff = now - timedelta(hours=float(cfg.recent_hours))
            window_from = recent_cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            window_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            top_errors: list[str] = []

            # Phase 1: Location metadata check
            for t in targets:
                loc_id = int(t["provider_location_id"])
                active_loc_context[0] = loc_id
                entry: dict[str, Any] = {
                    "provider_location_id": loc_id,
                    "station_name": t["station_name"],
                    "status": "unknown",
                    "pm25_sensor_id": t["configured_pm25_sensor_id"],
                    "last_observed_at": None,
                    "metadata_last_utc": t["stored_metadata_last_utc"],
                    "source_attribution": {
                        "upstream_provider": t["stored_provider_name"],
                        "licenses": t["stored_licenses"],
                    },
                    "attempts_consumed": 0,
                    "errors": [],
                }
                loc_reports[loc_id] = entry

                if budget_exhausted or db_failure_msg is not None or run_attempts >= effective_cap:
                    budget_exhausted = True
                    entry["errors"].append("stopped cleanly before location probe: Stage 1 budget exhausted")
                    continue

                try:
                    resp = client.get_location(loc_id)
                    results = resp.payload.get("results", [])
                    loc_obj = results[0] if isinstance(results, list) and results else None
                    if not isinstance(loc_obj, dict):
                        entry["status"] = "inactive"
                        entry["errors"].append("location metadata returned empty results")
                    else:
                        entry["station_name"] = str(loc_obj.get("name") or entry["station_name"])
                        entry["source_attribution"] = _extract_location_attribution(
                            loc_obj, t["stored_provider_name"], t["stored_licenses"]
                        )
                        sensor_id = _find_pm25_sensor_id(loc_obj, t["configured_pm25_sensor_id"])
                        entry["pm25_sensor_id"] = sensor_id
                        dt_last_obj = (
                            loc_obj.get("datetimeLast")
                            if isinstance(loc_obj.get("datetimeLast"), dict)
                            else {}
                        )
                        meta_last_raw = dt_last_obj.get("utc")
                        if meta_last_raw is not None:
                            entry["metadata_last_utc"] = str(meta_last_raw)
                        if sensor_id is None:
                            entry["status"] = "inactive"
                            entry["errors"].append("no PM2.5 sensor registered on location")
                        else:
                            meta_dt = _to_utc(parse_openaq_datetime(meta_last_raw)) if meta_last_raw else None
                            phase1_candidates.append((loc_id, {"sensor_id": sensor_id, "meta_dt": meta_dt}))
                except QuotaExhaustedError as q_exc:
                    safe_msg = redact_secrets(str(q_exc), secrets_tuple)
                    entry["errors"].append(safe_msg)
                    top_errors.append(safe_msg)
                    break
                except (httpx.HTTPError, TypeError, ValueError) as exc:
                    safe_msg = redact_secrets(str(exc), secrets_tuple)
                    entry["errors"].append(safe_msg)
                    top_errors.append(f"loc {loc_id}: {safe_msg}")

            # Phase 2: Recent hourly measurement probe prioritized by newest metadata timestamp
            phase1_candidates.sort(
                key=lambda item: (
                    item[1]["meta_dt"] is None,
                    -(item[1]["meta_dt"].timestamp()) if item[1]["meta_dt"] is not None else 0.0,
                )
            )
            for loc_id, info in phase1_candidates:
                entry = loc_reports[loc_id]
                active_loc_context[0] = loc_id
                sensor_id = int(info["sensor_id"])

                if budget_exhausted or db_failure_msg is not None or run_attempts >= effective_cap:
                    budget_exhausted = True
                    entry["errors"].append("stopped before hourly measurement check: budget limit reached")
                    continue

                orig_retries = client.max_retries
                client.max_retries = 1 if (effective_cap - run_attempts) >= 2 else 0
                try:
                    hr_resp = client.get_sensor_hours_raw(
                        sensor_id=sensor_id,
                        datetime_from=window_from,
                        datetime_to=window_to,
                        limit=5,
                        page=1,
                    )
                    rows = hr_resp.payload.get("results", [])
                    parsed_times: list[datetime] = []
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        period = row.get("period") if isinstance(row.get("period"), dict) else {}
                        dt_to = period.get("datetimeTo") if isinstance(period.get("datetimeTo"), dict) else {}
                        dt_single = row.get("datetime") if isinstance(row.get("datetime"), dict) else {}
                        ts_raw = dt_to.get("utc") or dt_single.get("utc")
                        if ts_raw:
                            try:
                                parsed_times.append(
                                    _to_utc(parse_openaq_datetime(str(ts_raw))) or recent_cutoff
                                )
                            except ValueError:
                                continue
                    if parsed_times:
                        newest_obs = max(parsed_times)
                        entry["last_observed_at"] = newest_obs.isoformat()
                        entry["status"] = "active" if newest_obs >= recent_cutoff else "inactive"
                    else:
                        entry["status"] = "inactive"
                        entry["errors"].append(
                            f"no recent hourly PM2.5 measurements within {cfg.recent_hours:.0f}h"
                        )
                except QuotaExhaustedError as q_exc:
                    safe_msg = redact_secrets(str(q_exc), secrets_tuple)
                    entry["errors"].append(safe_msg)
                    top_errors.append(safe_msg)
                    break
                except (httpx.HTTPError, TypeError, ValueError) as exc:
                    safe_msg = redact_secrets(str(exc), secrets_tuple)
                    entry["errors"].append(safe_msg)
                    top_errors.append(f"sensor {sensor_id}: {safe_msg}")
                finally:
                    client.max_retries = orig_retries
        except Exception as unexpected_exc:
            safe_exc = redact_secrets(str(unexpected_exc), secrets_tuple)
            try:
                with session_factory() as err_s:
                    mark_stage1_incomplete_or_aborted(err_s, approval_id, execution_id, safe_exc)
            except SQLAlchemyError:
                pass
            raise

        ordered_locations: list[dict[str, Any]] = []
        counts = {"active": 0, "inactive": 0, "unknown": 0}
        for t in targets:
            loc_id = int(t["provider_location_id"])
            item = loc_reports[loc_id]
            item["attempts_consumed"] = int(current_loc_attempts.get(loc_id, 0))
            counts[item["status"]] = counts.get(item["status"], 0) + 1
            ordered_locations.append(item)

        if db_failure_msg:
            final_status = "ABORTED_DB_FAILURE"
            token_final_status = "FAILED_INCOMPLETE"
        elif budget_exhausted or (
            run_attempts >= effective_cap and any(v["status"] == "unknown" for v in loc_reports.values())
        ):
            budget_exhausted = True
            final_status = "QUOTA_EXHAUSTED"
            token_final_status = "QUOTA_EXHAUSTED"
        elif top_errors:
            final_status = "COMPLETED_WITH_ERRORS"
            token_final_status = "COMPLETED_WITH_ERRORS"
        else:
            final_status = "COMPLETED"
            token_final_status = "COMPLETED"

        # Finalize single-use approval transition (CLAIMED_IN_PROGRESS -> terminal state) & audit run
        finished_utc = datetime.now(UTC)
        err_summary = "; ".join(top_errors)[:1000] if top_errors else db_failure_msg
        try:
            with session_factory() as fin_s:
                used_q, rem_q = quota_tracker.get_usage(fin_s, now_utc=now)
                fin_s.execute(
                    update(Stage1PreflightAuthorization)
                    .where(
                        Stage1PreflightAuthorization.approval_id == approval_id,
                        Stage1PreflightAuthorization.execution_id == execution_id,
                        Stage1PreflightAuthorization.status == "CLAIMED_IN_PROGRESS",
                    )
                    .values(
                        status=token_final_status,
                        finished_at=finished_utc,
                        error_summary=err_summary,
                        updated_at=finished_utc,
                    )
                    .execution_options(synchronize_session=False)
                )
                post_live_ctrl = fin_s.scalars(
                    select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
                ).first()
                recurring_opt_in_after = (
                    bool(post_live_ctrl.opt_in_enabled) if post_live_ctrl is not None else False
                )
                fin_s.add(
                    PilotIngestAuditRun(
                        provider="openaq",
                        worker_id=f"stage1-{worker_id[:96]}",
                        started_at=now,
                        finished_at=finished_utc,
                        status=final_status,
                        sensors_polled=len(phase1_candidates),
                        requests_consumed=run_attempts,
                        http_attempts=run_attempts,
                        records_fetched=0,
                        records_inserted=0,
                        records_revised=0,
                        records_deduplicated=0,
                        records_invalid=0,
                        recovered_from_expired_lease=bool(claimed_row.recovered_from_approval_id),
                        error_summary=err_summary,
                    )
                )
                fin_s.commit()
        except SQLAlchemyError:
            used_q, rem_q = run_attempts, max(0, cfg.max_daily_quota - run_attempts)
            recurring_opt_in_after = recurring_opt_in_state

        result = {
            "stage": "stage1_preflight",
            "status": final_status,
            "dry_run": False,
            "execute_once": True,
            "approval_id": approval_id,
            "execution_id": execution_id,
            "worker_id": worker_id,
            "authorized_by": claimed_row.authorized_by,
            "recovered_from_approval_id": claimed_row.recovered_from_approval_id,
            "checked_at_utc": now.isoformat(),
            "hard_attempt_cap": effective_cap,
            "attempts_consumed": run_attempts,
            "daily_quota_used_today": used_q,
            "daily_quota_remaining": rem_q,
            "daily_quota_cap": cfg.max_daily_quota,
            "budget_exhausted": budget_exhausted,
            "recurring_polling_opt_in_enabled": recurring_opt_in_after,
            "summary": {
                "total_locations": len(ordered_locations),
                "active": counts["active"],
                "inactive": counts["inactive"],
                "unknown": counts["unknown"],
            },
            "locations": ordered_locations,
            "errors": top_errors,
        }
        result["artifact_path"] = _write_preflight_artifact(result, cfg, now)
        return result
