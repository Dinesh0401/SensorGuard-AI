"""SensorGuard CLI: synthetic benchmark, OpenAQ audit, ingestion, and historical replay."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sensorguard.audit import audit_candidate_location
from sensorguard.db.session import get_session_factory, init_db
from sensorguard.experiment import ExperimentConfig, run_experiment
from sensorguard.ingest import IngestionPipeline
from sensorguard.preflight import (
    STAGE1_HARD_MAX_ATTEMPTS,
    Stage1PreflightConfig,
    authorize_stage1_preflight_db,
    authorize_stage1_recovery_db,
    run_stage1_preflight,
)
from sensorguard.providers import OpenAQClient
from sensorguard.replay import PointInTimeReplayEngine
from sensorguard.simulation import OutageConfig


def _run_demo(args: argparse.Namespace) -> None:
    result = run_experiment(ExperimentConfig(outage=OutageConfig(mechanism=args.mechanism, seed=args.seed)))
    rendered = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _run_audit(args: argparse.Namespace) -> None:
    client = OpenAQClient()
    report = audit_candidate_location(
        location_id=args.location_id,
        client=client,
        datetime_from=args.datetime_from,
        datetime_to=args.datetime_to,
        max_pages=args.max_pages,
    )
    report_dict = asdict(report)
    rendered = json.dumps(report_dict, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _run_ingest(args: argparse.Namespace) -> None:
    init_db()
    session_factory = get_session_factory()
    client = OpenAQClient()

    with session_factory() as session:
        pipeline = IngestionPipeline(session)
        # Fetch location details and register station/sensors
        resp = client.get_location(args.location_id)
        results = resp.payload.get("results", [])
        if not results:
            print(f"Error: Location {args.location_id} not found", file=sys.stderr)
            return
        loc_data = results[0] if isinstance(results, list) else results
        station, sensors = pipeline.upsert_station_and_sensors(loc_data)
        print(f"Registered Station '{station.name}' (ID: {station.id}) with {len(sensors)} sensor(s)")

        total_fetched = 0
        total_inserted = 0
        for sensor in sensors:
            if sensor.parameter == "pm25":
                res = pipeline.ingest_sensor_hours(
                    sensor_db_id=sensor.id,
                    provider_sensor_id=sensor.provider_sensor_id,
                    client=client,
                    datetime_from=args.datetime_from,
                    datetime_to=args.datetime_to,
                    max_pages=args.max_pages,
                )
                total_fetched += res.records_fetched
                total_inserted += res.records_inserted
                print(
                    f"Sensor {sensor.provider_sensor_id} ({sensor.parameter}): "
                    f"fetched={res.records_fetched}, inserted={res.records_inserted}, "
                    f"deduped={res.records_deduplicated}, invalid={res.records_invalid}"
                )
        print(f"Ingestion complete: total_fetched={total_fetched}, total_inserted={total_inserted}")


def _run_replay(args: argparse.Namespace) -> None:
    init_db()
    session_factory = get_session_factory()
    cutoff = datetime.fromisoformat(args.cutoff)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)

    with session_factory() as session:
        engine = PointInTimeReplayEngine(session)
        df, summary = engine.query_as_of(
            sensor_ids=args.sensor_ids,
            decision_cutoff=cutoff,
            mode=args.mode,
            delay_hours=args.delay_hours,
        )
        print(f"Replay mode: {summary.mode} (simulated_availability={summary.is_simulated_availability})")
        print(f"Decision cutoff: {summary.decision_cutoff}")
        print(
            f"Visible records: {summary.total_visible_records} across {summary.sensors_reporting} sensor(s)"
        )
        if not df.empty:
            print(df[["sensor_id", "observed_at", "value", "unit", "quality_flag"]].tail(10).to_string())


def _run_preflight_stations(args: argparse.Namespace) -> None:
    if not 1 <= int(args.max_attempts) <= STAGE1_HARD_MAX_ATTEMPTS:
        print(
            json.dumps(
                {
                    "status": "INVALID_ARGUMENTS",
                    "error": f"--max-attempts must be between 1 and {STAGE1_HARD_MAX_ATTEMPTS}.",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        sys.exit(2)

    cfg = Stage1PreflightConfig(
        execute_once=bool(args.execute_once),
        approval_id=args.approval_id,
        max_attempts=int(args.max_attempts),
        max_daily_quota=int(args.daily_cap),
        recent_hours=float(args.recent_hours),
        authorized_by=args.authorized_by,
        output_path=args.output,
        write_artifact=not bool(args.no_artifact),
    )
    report = run_stage1_preflight(get_session_factory(), config=cfg)
    print(json.dumps(report, indent=2, default=str))
    if str(report.get("status", "")).startswith(("BLOCKED_", "ABORTED_")):
        sys.exit(2)


def _run_authorize_stage1(args: argparse.Namespace) -> None:
    if not args.confirm_single_use:
        print(
            json.dumps(
                {
                    "status": "INVALID_ARGUMENTS",
                    "error": "--confirm-single-use is required to issue a single-use Stage 1 approval token.",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            if args.recover_from_approval_id:
                row = authorize_stage1_recovery_db(
                    session,
                    prior_approval_id=args.recover_from_approval_id,
                    authorized_by=args.authorized_by,
                    reason=args.reason,
                    max_attempts=args.max_attempts,
                    allow_budget_reset=bool(args.allow_budget_reset),
                )
            else:
                row = authorize_stage1_preflight_db(
                    session,
                    authorized_by=args.authorized_by,
                    reason=args.reason or "Explicit single-use Stage 1 station availability check authorization",
                    max_attempts=args.max_attempts or STAGE1_HARD_MAX_ATTEMPTS,
                )
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(json.dumps({"status": "AUTHORIZATION_REJECTED", "error": str(exc)}, indent=2), file=sys.stderr)
            sys.exit(2)
        payload = {
            "status": row.status,
            "approval_id": row.approval_id,
            "authorized_by": row.authorized_by,
            "reason": row.reason,
            "max_attempts": row.max_attempts,
            "remaining_attempts": int(row.max_attempts) - int(row.attempts_consumed),
            "recovered_from_approval_id": row.recovered_from_approval_id,
            "single_use": True,
            "next_command": (
                f"python -m sensorguard.cli preflight-stations --execute-once "
                f"--approval-id {row.approval_id} --authorized-by {row.authorized_by}"
            ),
        }
    print(json.dumps(payload, indent=2, default=str))


def _load_local_env() -> None:
    """Load key-value pairs from local .env if present and not already set."""
    env_file = Path(".env")
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    import os

                    os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            pass


def main() -> None:
    """CLI router for SensorGuard."""
    _load_local_env()
    parser = argparse.ArgumentParser(description="SensorGuard AI MLOps CLI")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # run-demo
    p_demo = subparsers.add_parser("run-demo", help="Run synthetic outage experiment")
    p_demo.add_argument("--mechanism", choices=["mcar", "mar", "mnar", "burst"], default="mnar")
    p_demo.add_argument("--seed", type=int, default=17)
    p_demo.add_argument("--output", type=Path)
    p_demo.set_defaults(func=_run_demo)

    # audit
    p_audit = subparsers.add_parser("audit", help="Audit OpenAQ candidate location coverage")
    p_audit.add_argument("--location-id", type=int, required=True, help="OpenAQ location ID")
    p_audit.add_argument("--datetime-from", type=str, default=None)
    p_audit.add_argument("--datetime-to", type=str, default=None)
    p_audit.add_argument("--max-pages", type=int, default=10)
    p_audit.add_argument("--output", type=Path)
    p_audit.set_defaults(func=_run_audit)

    # ingest
    p_ingest = subparsers.add_parser("ingest", help="Ingest OpenAQ location and sensor hourly data")
    p_ingest.add_argument("--location-id", type=int, required=True, help="OpenAQ location ID")
    p_ingest.add_argument("--datetime-from", type=str, default=None)
    p_ingest.add_argument("--datetime-to", type=str, default=None)
    p_ingest.add_argument("--max-pages", type=int, default=5)
    p_ingest.set_defaults(func=_run_ingest)

    # replay
    p_replay = subparsers.add_parser("replay", help="Point-in-time historical replay query")
    p_replay.add_argument("--sensor-ids", type=int, nargs="+", required=True)
    p_replay.add_argument("--cutoff", type=str, required=True, help="ISO cutoff timestamp")
    p_replay.add_argument("--mode", choices=["prospective", "retrospective"], default="prospective")
    p_replay.add_argument("--delay-hours", type=float, default=1.0)
    p_replay.set_defaults(func=_run_replay)

    # preflight-stations (Stage 1 strictly bounded one-time availability check)
    p_preflight = subparsers.add_parser(
        "preflight-stations",
        help="Stage 1 one-time bounded availability check for the 8 archived UK OpenAQ locations (dry-run default)",
    )
    p_preflight.add_argument(
        "--execute-once",
        action="store_true",
        help="Actually issue bounded Stage 1 OpenAQ HTTP requests (default is 0-network dry-run)",
    )
    p_preflight.add_argument(
        "--approval-id",
        type=str,
        default=None,
        help="Single-use DB authorization token minted via authorize-stage1 (required for --execute-once)",
    )
    p_preflight.add_argument(
        "--max-attempts",
        type=int,
        default=STAGE1_HARD_MAX_ATTEMPTS,
        help="Hard cap on total HTTP attempts including retries (1..16, default 16)",
    )
    p_preflight.add_argument("--daily-cap", type=int, default=48, help="Daily OpenAQ quota cap (default 48)")
    p_preflight.add_argument(
        "--recent-hours",
        type=float,
        default=48.0,
        help="Recency window in hours for active PM2.5 confirmation (default 48)",
    )
    p_preflight.add_argument(
        "--authorized-by",
        type=str,
        default=None,
        help="Non-default operator identity authorizing Stage 1 execution",
    )
    p_preflight.add_argument("--output", type=Path, default=None, help="Optional JSON artifact output path")
    p_preflight.add_argument(
        "--no-artifact",
        action="store_true",
        help="Do not write JSON report artifact to disk",
    )
    p_preflight.set_defaults(func=_run_preflight_stations)

    # authorize-stage1 (mint single-use DB approval token; never makes HTTP calls or enables Stage 2)
    p_auth = subparsers.add_parser(
        "authorize-stage1",
        help="Issue an explicit single-use DB approval token for one Stage 1 preflight execution",
    )
    p_auth.add_argument("--authorized-by", type=str, required=True, help="Non-default operator identity")
    p_auth.add_argument(
        "--reason",
        type=str,
        default="Explicit single-use Stage 1 station availability check authorization",
        help="Audit reason recorded in stage1_preflight_authorizations",
    )
    p_auth.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Hard HTTP attempt budget for this token (1..16; defaults to remaining budget on recovery or 16)",
    )
    p_auth.add_argument(
        "--confirm-single-use",
        action="store_true",
        help="Required confirmation flag acknowledging single-use approval semantics",
    )
    p_auth.add_argument(
        "--recover-from-approval-id",
        type=str,
        default=None,
        help="Optional prior FAILED_INCOMPLETE / CLAIMED_IN_PROGRESS approval ID to supersede via audited recovery",
    )
    p_auth.add_argument(
        "--allow-budget-reset",
        action="store_true",
        help="Explicitly allow resetting request budget during recovery (default carries over remaining attempts only)",
    )
    p_auth.set_defaults(func=_run_authorize_stage1)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
