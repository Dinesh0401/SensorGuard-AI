"""Bounded Stage 1 OpenAQ station & PM2.5 availability pre-flight runner.

Strictly defaults to dry-run (0 network calls). Requires `--execute-once` plus
kill-switch and Stage 1 operator authorization, hard-caps actual HTTP attempts
at 16 total (including retries and failures), integrates with the durable
PostgreSQL/SQLite quota tracker, and NEVER enables `PilotControlState.opt_in_enabled`
for recurring Stage 2 polling.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from sensorguard.db.schema import PilotControlState, PilotIngestAuditRun, Sensor, Station
from sensorguard.pilot_monitor import DEFAULT_CONTROL_NAME, DurablePilotQuotaTracker
from sensorguard.providers import OpenAQClient, QuotaExhaustedError, parse_openaq_datetime

STAGE1_CONTROL_NAME = "openaq_stage1_preflight"
STAGE1_HARD_MAX_ATTEMPTS = 16
STAGE1_ARCHIVED_LOCATION_IDS: tuple[int, ...] = (154, 157, 141, 153, 698, 804, 1795, 2312)
DEFAULT_PREFLIGHT_ARTIFACT_DIR = Path("artifacts/preflight")
_PREFLIGHT_LOCK = threading.Lock()


@dataclass(frozen=True)
class Stage1PreflightConfig:
    """Fail-closed configuration for bounded one-time Stage 1 station availability check."""

    execute_once: bool = False
    max_attempts: int = STAGE1_HARD_MAX_ATTEMPTS
    max_daily_quota: int = 48
    recent_hours: float = 48.0
    location_ids: tuple[int, ...] = STAGE1_ARCHIVED_LOCATION_IDS
    authorized_by: str | None = None
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


def redact_secrets(text: str | None, extra_secrets: tuple[str | None, ...] = ()) -> str:
    """Remove API keys and URL passwords from any logged or persisted message."""
    if not text:
        return ""
    cleaned = str(text)
    cleaned = re.sub(r"(?i)(x-api-key|api_key|token|password|secret)=([^\s,;'\"]+)", r"\1=[REDACTED]", cleaned)
    cleaned = re.sub(r"://([^:@/\s]+):([^@/\s]+)@", r"://\1:[REDACTED]@", cleaned)
    for sec in extra_secrets:
        if sec and len(sec) >= 4:
            cleaned = cleaned.replace(sec, "[REDACTED]")
    return cleaned


def authorize_stage1_preflight_db(
    session: Session,
    authorized_by: str,
    reason: str = "Explicit one-time Stage 1 station availability check authorization",
    now_utc: datetime | None = None,
) -> PilotControlState:
    """Record explicit Stage 1 approval without ever touching recurring polling's opt_in_enabled flag."""
    actor = str(authorized_by or "").strip()
    if actor in {"", "system_default"}:
        raise ValueError("Non-default operator identity required to authorize Stage 1 pre-flight check.")
    now = _to_utc(now_utc) or datetime.now(UTC)

    row = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == STAGE1_CONTROL_NAME)
    ).first()
    if row is None:
        row = PilotControlState(
            control_name=STAGE1_CONTROL_NAME,
            kill_switch_active=False,
            opt_in_enabled=False,
            authorized_at=now,
            reason=reason,
            updated_by=actor,
            updated_at=now,
        )
        session.add(row)
    else:
        row.kill_switch_active = False
        row.opt_in_enabled = False
        row.authorized_at = now
        row.reason = reason
        row.updated_by = actor
        row.updated_at = now
    session.commit()
    session.refresh(row)
    return row


def _check_stage1_authorization(
    session: Session,
    config: Stage1PreflightConfig,
) -> tuple[bool, str, str]:
    """Enforce env kill switch, Stage 1 kill switch, and operator authorization (no Stage 2 unlock)."""
    env_kill = os.getenv("SENSORGUARD_POLLING_KILL_SWITCH", "false").lower() in {"1", "true", "yes"}
    if env_kill:
        return False, "BLOCKED_KILL_SWITCH", "Environment SENSORGUARD_POLLING_KILL_SWITCH is active."

    # Guardrail: recurring Stage 2 control state must never be auto-enabled by Stage 1
    live_ctrl = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
    ).first()
    if live_ctrl is not None and live_ctrl.opt_in_enabled:
        return (
            False,
            "BLOCKED_RECURRING_OPT_IN_UNEXPECTED",
            "PilotControlState 'openaq_live_polling' has opt_in_enabled=True; keep recurring polling locked for Stage 1.",
        )

    s1_ctrl = session.scalars(
        select(PilotControlState).where(PilotControlState.control_name == STAGE1_CONTROL_NAME)
    ).first()
    if s1_ctrl is not None:
        if s1_ctrl.kill_switch_active:
            return False, "BLOCKED_KILL_SWITCH", "Database Stage 1 kill switch is active."
        if s1_ctrl.authorized_at is not None and s1_ctrl.updated_by not in {None, "", "system_default"}:
            return True, "AUTHORIZED_DB", str(s1_ctrl.updated_by)

    env_approved = os.getenv("SENSORGUARD_STAGE1_PREFLIGHT_APPROVED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    actor = str(config.authorized_by or os.getenv("SENSORGUARD_STAGE1_AUTHORIZED_BY") or "").strip()
    if env_approved and actor not in {"", "system_default"}:
        return True, "AUTHORIZED_OPERATOR_ENV", actor

    return (
        False,
        "BLOCKED_NOT_AUTHORIZED",
        (
            "Stage 1 operator authorization missing. Provide --authorized-by <operator> with "
            "SENSORGUARD_STAGE1_PREFLIGHT_APPROVED=true (or record openaq_stage1_preflight approval in DB)."
        ),
    )


def _load_archived_station_targets(
    session: Session,
    location_ids: tuple[int, ...],
) -> list[dict[str, Any]]:
    """Load archived UK station & PM2.5 sensor inventory from DB preserving configured priority order."""
    order_rank = {loc_id: idx for idx, loc_id in enumerate(location_ids)}
    stations = list(
        session.scalars(
            select(Station).where(Station.provider_location_id.in_(list(location_ids)))
        ).all()
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
        dt_last = meta.get("datetimeLast", {}).get("utc") if isinstance(meta.get("datetimeLast"), dict) else None
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


def _extract_location_attribution(loc_obj: dict[str, Any], fallback_provider: str, fallback_licenses: list[str]) -> dict[str, Any]:
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


def _write_preflight_artifact(payload: dict[str, Any], config: Stage1PreflightConfig, ts_now: datetime) -> str | None:
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


def run_stage1_preflight(
    session_factory: sessionmaker[Session],
    config: Stage1PreflightConfig | None = None,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Execute dry-run or strictly capped one-time Stage 1 OpenAQ availability check."""
    cfg = config or Stage1PreflightConfig()
    now = _to_utc(now_utc) or datetime.now(UTC)
    hard_cap = min(STAGE1_HARD_MAX_ATTEMPTS, int(cfg.max_attempts))
    secret_env_key = os.getenv("OPENAQ_API_KEY")
    db_env_url = os.getenv("DATABASE_URL")
    secrets_tuple = (api_key, secret_env_key, db_env_url)

    with session_factory() as session:
        targets = _load_archived_station_targets(session, cfg.location_ids)
        recurring_ctrl = session.scalars(
            select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
        ).first()
        recurring_opt_in_state = bool(recurring_ctrl.opt_in_enabled) if recurring_ctrl is not None else False

        # Default strictly to dry-run (0 network calls) unless --execute-once explicitly supplied
        if not cfg.execute_once:
            try:
                used_q, rem_q = DurablePilotQuotaTracker(max_requests_per_day=cfg.max_daily_quota).get_usage(
                    session, now_utc=now
                )
            except SQLAlchemyError:
                session.rollback()
                used_q, rem_q = 0, cfg.max_daily_quota

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
                "hard_attempt_cap": hard_cap,
                "attempts_consumed": 0,
                "daily_quota_used_today": used_q,
                "daily_quota_remaining": rem_q,
                "daily_quota_cap": cfg.max_daily_quota,
                "budget_exhausted": False,
                "recurring_polling_opt_in_enabled": recurring_opt_in_state,
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
            art_path = _write_preflight_artifact(out, cfg, now)
            out["artifact_path"] = art_path
            return out

        # Authorization & Kill-Switch checks
        try:
            auth_ok, auth_status, auth_msg = _check_stage1_authorization(session, cfg)
        except SQLAlchemyError as db_exc:
            session.rollback()
            safe_err = redact_secrets(str(db_exc), secrets_tuple)
            return {
                "stage": "stage1_preflight",
                "status": "BLOCKED_DB_UNAVAILABLE",
                "dry_run": False,
                "execute_once": True,
                "checked_at_utc": now.isoformat(),
                "hard_attempt_cap": hard_cap,
                "attempts_consumed": 0,
                "budget_exhausted": False,
                "recurring_polling_opt_in_enabled": False,
                "summary": {"total_locations": len(targets), "active": 0, "inactive": 0, "unknown": len(targets)},
                "locations": [],
                "errors": [f"Database check failed cleanly before outbound HTTP requests: {safe_err}"],
                "artifact_path": None,
            }

        if not auth_ok:
            blocked = {
                "stage": "stage1_preflight",
                "status": auth_status,
                "dry_run": False,
                "execute_once": True,
                "checked_at_utc": now.isoformat(),
                "hard_attempt_cap": hard_cap,
                "attempts_consumed": 0,
                "budget_exhausted": False,
                "recurring_polling_opt_in_enabled": recurring_opt_in_state,
                "summary": {"total_locations": len(targets), "active": 0, "inactive": 0, "unknown": len(targets)},
                "locations": [],
                "errors": [redact_secrets(auth_msg, secrets_tuple)],
            }
            blocked["artifact_path"] = _write_preflight_artifact(blocked, cfg, now)
            return blocked

        resolved_key = api_key or secret_env_key
        if not resolved_key and transport is None:
            no_cred = {
                "stage": "stage1_preflight",
                "status": "BLOCKED_NO_CREDENTIALS",
                "dry_run": False,
                "execute_once": True,
                "checked_at_utc": now.isoformat(),
                "hard_attempt_cap": hard_cap,
                "attempts_consumed": 0,
                "budget_exhausted": False,
                "recurring_polling_opt_in_enabled": recurring_opt_in_state,
                "summary": {"total_locations": len(targets), "active": 0, "inactive": 0, "unknown": len(targets)},
                "locations": [],
                "errors": ["OPENAQ_API_KEY missing in environment and no mock transport supplied."],
            }
            no_cred["artifact_path"] = _write_preflight_artifact(no_cred, cfg, now)
            return no_cred

        quota_tracker = DurablePilotQuotaTracker(provider="openaq", max_requests_per_day=cfg.max_daily_quota)
        try:
            # Verify durable DB quota table is reachable and operational BEFORE opening any network client
            quota_tracker.get_usage(session, now_utc=now)
        except SQLAlchemyError as db_exc:
            session.rollback()
            safe_err = redact_secrets(str(db_exc), secrets_tuple)
            db_fail = {
                "stage": "stage1_preflight",
                "status": "BLOCKED_DB_UNAVAILABLE",
                "dry_run": False,
                "execute_once": True,
                "checked_at_utc": now.isoformat(),
                "hard_attempt_cap": hard_cap,
                "attempts_consumed": 0,
                "budget_exhausted": False,
                "recurring_polling_opt_in_enabled": recurring_opt_in_state,
                "summary": {"total_locations": len(targets), "active": 0, "inactive": 0, "unknown": len(targets)},
                "locations": [],
                "errors": [f"Durable quota database unavailable; failed closed with zero HTTP calls: {safe_err}"],
            }
            db_fail["artifact_path"] = _write_preflight_artifact(db_fail, cfg, now)
            return db_fail

        run_attempts = 0
        budget_exhausted = False
        db_failure_msg: str | None = None
        current_loc_attempts: dict[int, int] = {t["provider_location_id"]: 0 for t in targets}
        active_loc_context: list[int] = [0]

        def _stage1_attempt_guard(_method: str, _endpoint: str, _attempt_idx: int) -> None:
            nonlocal run_attempts, budget_exhausted, db_failure_msg
            with _PREFLIGHT_LOCK:
                if run_attempts >= hard_cap:
                    budget_exhausted = True
                    raise QuotaExhaustedError(f"Stage 1 hard attempt ceiling reached ({hard_cap}/{hard_cap}).")
                try:
                    with session_factory() as q_sess:
                        ok = quota_tracker.try_consume(q_sess, count=1, now_utc=now)
                except SQLAlchemyError as exc:
                    db_failure_msg = redact_secrets(str(exc), secrets_tuple)
                    raise QuotaExhaustedError(
                        f"Durable quota commit failed before outbound HTTP call: {db_failure_msg}"
                    ) from exc
                if not ok:
                    budget_exhausted = True
                    raise QuotaExhaustedError(
                        f"Durable daily OpenAQ quota exhausted (cap={cfg.max_daily_quota}/day)."
                    )
                run_attempts += 1
                loc_key = active_loc_context[0]
                if loc_key in current_loc_attempts:
                    current_loc_attempts[loc_key] += 1

        client = OpenAQClient(
            api_key=resolved_key or "mock-stage1-key",
            transport=transport,
            max_retries=1,
            base_backoff_seconds=0.01 if transport is not None else 0.3,
            rate_limit_per_second=50.0 if transport is not None else 2.0,
            attempt_guard=_stage1_attempt_guard,
        )

        # Phase 1: Location metadata probe across the 8 locations (respecting global hard_cap <= 16)
        loc_reports: dict[int, dict[str, Any]] = {}
        phase1_candidates: list[tuple[int, dict[str, Any]]] = []
        recent_cutoff = now - timedelta(hours=float(cfg.recent_hours))
        window_from = recent_cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        window_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        top_errors: list[str] = []

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

            if budget_exhausted or db_failure_msg is not None or run_attempts >= hard_cap:
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
                    dt_last_obj = loc_obj.get("datetimeLast") if isinstance(loc_obj.get("datetimeLast"), dict) else {}
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

        # Phase 2: Verify >= 1 recent hourly measurement where budget allows (prioritize newest metadata first)
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

            if budget_exhausted or db_failure_msg is not None or run_attempts >= hard_cap:
                budget_exhausted = True
                entry["errors"].append("stopped before hourly measurement check: budget limit reached")
                continue

            # Keep retry bounded if 1 attempt left so we do not waste budget needlessly
            orig_retries = client.max_retries
            client.max_retries = 1 if (hard_cap - run_attempts) >= 2 else 0
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
                            parsed_times.append(_to_utc(parse_openaq_datetime(str(ts_raw))) or recent_cutoff)
                        except ValueError:
                            continue
                if parsed_times:
                    newest_obs = max(parsed_times)
                    entry["last_observed_at"] = newest_obs.isoformat()
                    entry["status"] = "active" if newest_obs >= recent_cutoff else "inactive"
                else:
                    entry["status"] = "inactive"
                    entry["errors"].append(f"no recent hourly PM2.5 measurements within {cfg.recent_hours:.0f}h")
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

        # Stamp per-location attempts & build summary
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
        elif budget_exhausted:
            final_status = "QUOTA_EXHAUSTED"
        elif top_errors:
            final_status = "COMPLETED_WITH_ERRORS"
        else:
            final_status = "COMPLETED"

        try:
            used_q, rem_q = quota_tracker.get_usage(session, now_utc=now)
        except SQLAlchemyError:
            session.rollback()
            used_q, rem_q = run_attempts, max(0, cfg.max_daily_quota - run_attempts)

        # Verify recurring polling opt-in was never enabled
        try:
            post_live_ctrl = session.scalars(
                select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
            ).first()
            recurring_opt_in_after = bool(post_live_ctrl.opt_in_enabled) if post_live_ctrl is not None else False
            session.add(
                PilotIngestAuditRun(
                    provider="openaq",
                    worker_id=f"stage1-preflight-{auth_status.lower()}",
                    started_at=now,
                    finished_at=datetime.now(UTC),
                    status=final_status,
                    sensors_polled=len(phase1_candidates),
                    requests_consumed=run_attempts,
                    http_attempts=run_attempts,
                    records_fetched=0,
                    records_inserted=0,
                    records_revised=0,
                    records_deduplicated=0,
                    records_invalid=0,
                    recovered_from_expired_lease=False,
                    error_summary="; ".join(top_errors)[:1000] if top_errors else None,
                )
            )
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            recurring_opt_in_after = recurring_opt_in_state

        result = {
            "stage": "stage1_preflight",
            "status": final_status,
            "dry_run": False,
            "execute_once": True,
            "authorization_source": auth_msg,
            "checked_at_utc": now.isoformat(),
            "hard_attempt_cap": hard_cap,
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
