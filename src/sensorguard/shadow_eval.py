"""Genuine prospective shadow forecast engine & causal delayed evaluation (M8.2 / M8.6).

Enforces strict separation between:
- `prospective_shadow`: forecasts generated and persisted strictly BEFORE target_time AND BEFORE any
  observation at target_time exists in DB, with immutable wall-clock `generated_at`/`persisted_at`.
- `retrospective_replay`: point-in-time historical evaluations whose targets may already exist; still
  records honest actual `generated_at`/`persisted_at` (never backdates creation timestamps to cutoff)
  and is quarantined from genuine prospective metrics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sensorguard.calibration import ConformalizedQuantileRegression, SplitConformalCalibrator, policy
from sensorguard.db.schema import (
    ForecastFeatureSnapshot,
    ForecastRecord,
    ForecastRun,
    Observation,
    PilotIngestAuditRun,
    Sensor,
)
from sensorguard.models.data import build_spatiotemporal_features, partition_dataset
from sensorguard.models.forecasters import (
    LightGBMPointForecaster,
    LightGBMQuantileForecaster,
    PersistenceForecaster,
)
from sensorguard.models.manifest import ModelManifest
from sensorguard.models.sage import SAGEConfig, SAGEForecaster
from sensorguard.models.sage_adaptive import (
    AdaptiveSAGEConfig,
    AdaptiveSAGEForecaster,
    compute_wilson_ci,
)
from sensorguard.replay import PointInTimeReplayEngine

logger = logging.getLogger(__name__)

SHADOW_MODEL_SPECS: dict[str, dict[str, str]] = {
    "persistence": {
        "model_id": "persistence_split_shadow",
        "model_version": "1.0.0-shadow",
        "algorithm": "PERSISTENCE-SPLIT",
    },
    "locally_weighted_mar": {
        "model_id": "locally_weighted_mar_shadow",
        "model_version": "1.0.0-shadow",
        "algorithm": "LOCALLY-WEIGHTED-MAR",
    },
    "lightgbm_cqr": {
        "model_id": "lightgbm_cqr_shadow",
        "model_version": "1.0.0-shadow",
        "algorithm": "LIGHTGBM-CQR",
    },
    "sage_static": {
        "model_id": "sage_pilot_q1_2024",
        "model_version": "1.0.0",
        "algorithm": "SAGE-STATIC-LIGHTGBM",
    },
    "sage_adaptive": {
        "model_id": "sage_adaptive_q1_2024",
        "model_version": "2.0.0-shadow-m77",
        "algorithm": "ADAPTIVE-SAGE-LIGHTGBM",
    },
}


def _to_utc(dt: Any) -> datetime | None:
    if dt is None or (isinstance(dt, float) and math.isnan(dt)):
        return None
    if hasattr(dt, "to_pydatetime"):
        dt = dt.to_pydatetime()
    elif isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def fit_shadow_suite(
    featured_df: pd.DataFrame,
    station_coords: dict[str, tuple[float, float]],
    nominal_coverage: float = 0.90,
) -> dict[str, Any]:
    """Train all 5 shadow models strictly on chronological historical splits with artifact lineage."""
    splits = partition_dataset(featured_df, station_coords, train_pct=0.70, cal_pct=0.30)
    train_df, cal_df = splits.train, splits.calibrate
    cols = splits.feature_columns
    y_tr = train_df["target"].to_numpy()
    y_cal = cal_df["target"].to_numpy()
    alpha = 1.0 - nominal_coverage

    persist = PersistenceForecaster().fit(train_df, y_tr)
    persist_cal = SplitConformalCalibrator(alpha=alpha).fit(y_cal, persist.predict(cal_df))

    lgb_q = LightGBMQuantileForecaster(feature_columns=cols, quantiles=(0.05, 0.50, 0.95)).fit(train_df, y_tr)
    cal_q = lgb_q.predict_quantiles(cal_df)
    cqr = ConformalizedQuantileRegression(alpha=alpha).fit(y_cal, cal_q[0.05], cal_q[0.95])

    sage_static = SAGEForecaster(
        config=SAGEConfig(nominal_coverage=nominal_coverage, max_disagreement_threshold=8.0),
        feature_columns=cols,
    ).fit(train_df, y_tr, cal_df, y_cal)

    cal_start = cal_df["timestamp"].min()
    unfiltered_tr = featured_df[featured_df["timestamp"] < cal_start].copy()
    if len(unfiltered_tr) < 20 or len(pd.to_datetime(unfiltered_tr["timestamp"]).unique()) < 4:
        unfiltered_tr = train_df.copy()
    y_unf = unfiltered_tr["target"].to_numpy()

    mar_local = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            gamma_grid=(0.0,),
            nominal_coverage=nominal_coverage,
            max_disagreement_threshold=9999.0,
            propensity_risk_weight=0.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_tr, y_unf, cal_df, y_cal)

    sage_adaptive = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            nominal_coverage=nominal_coverage,
            max_disagreement_threshold="cal_p95",
            propensity_risk_weight=1.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_tr, y_unf, cal_df, y_cal)

    lgb_pt = LightGBMPointForecaster(feature_columns=cols).fit(train_df, y_tr)

    fit_finished_at = datetime.now(UTC).isoformat()
    train_max_ts = str(_to_utc(cal_df["timestamp"].max()) or fit_finished_at)
    provenance: dict[str, dict[str, str]] = {}
    for m_key, spec in SHADOW_MODEL_SPECS.items():
        fp = hashlib.sha256(
            f"{spec['model_id']}:{spec['model_version']}:{train_max_ts}:{len(train_df)}:{len(cal_df)}".encode()
        ).hexdigest()[:12]
        provenance[m_key] = {
            "model_created_at": fit_finished_at,
            "artifact_immutable_ref": f"{spec['model_id']}@{spec['model_version']}#fit_{fp}",
        }

    # Anchor sage_static metadata to audited artifact manifest when available
    try:
        manifest_p = Path("artifacts/models/manifest.json")
        if manifest_p.exists():
            m = ModelManifest.load(manifest_p)
            provenance["sage_static"] = {
                "model_created_at": m.created_at,
                "artifact_immutable_ref": f"{m.model_id}@{m.version}#{m.sha256[:12]}",
            }
    except (OSError, ValueError, KeyError) as exc:
        logger.debug("Skipping optional serving manifest lookup during shadow fit: %s", exc)

    return {
        "feature_columns": cols,
        "nominal_coverage": nominal_coverage,
        "model_provenance": provenance,
        "persistence": (persist, persist_cal),
        "locally_weighted_mar": mar_local,
        "lightgbm_cqr": (lgb_q, cqr),
        "lightgbm_point": lgb_pt,
        "sage_static": sage_static,
        "sage_adaptive": sage_adaptive,
    }


def _predict_single_shadow_model(
    model_key: str,
    models: dict[str, Any],
    X_row: pd.DataFrame,
) -> tuple[float, float, float, str, str, float, dict[str, dict[str, float]] | None]:
    """Return (point, lower, upper, status, reason, d_sens, scenario_bounds) for 1-row feature df."""
    ages = X_row["age_hours"].to_numpy(dtype=float)
    base_statuses, base_reasons = policy(ages)

    if model_key == "persistence":
        p_mod, p_cal = models["persistence"]
        pt = float(p_mod.predict(X_row)[0])
        lo, hi = p_cal.predict_interval(np.array([pt]))
        return pt, float(lo[0]), float(hi[0]), str(base_statuses[0]).upper(), str(base_reasons[0]), 0.0, None

    if model_key == "lightgbm_cqr":
        q_mod, cqr = models["lightgbm_cqr"]
        qs = q_mod.predict_quantiles(X_row)
        lo, hi = cqr.predict_interval(qs[0.05], qs[0.95])
        pt = float(qs[0.50][0])
        return pt, float(lo[0]), float(hi[0]), str(base_statuses[0]).upper(), str(base_reasons[0]), 0.0, None

    guarded = models[model_key].predict_guarded(X_row)
    scen = {
        str(g): {"lower": round(float(lo[0]), 4), "upper": round(float(hi[0]), 4)}
        for g, (lo, hi) in guarded.gamma_intervals.items()
    }
    return (
        float(guarded.point_forecast[0]),
        float(guarded.lower_envelope[0]),
        float(guarded.upper_envelope[0]),
        str(guarded.status[0]).upper(),
        str(guarded.reason[0]),
        float(guarded.scenario_disagreement[0]),
        scen,
    )


def _generate_shadow_batch(
    session: Session,
    decision_cutoffs: list[datetime],
    fitted_models: dict[str, Any],
    station_coords: dict[str, tuple[float, float]],
    evaluation_mode: str,
    station_ids: list[int] | None = None,
    dataset_hash: str | None = None,
    data_mode: str = "shadow_live",
    idempotency_behavior: str = "return",
) -> list[ForecastRun]:
    """Core generator enforcing honest generation/persistence timestamps & DB idempotency."""
    if evaluation_mode not in {"prospective_shadow", "retrospective_replay"}:
        raise ValueError(f"Unsupported evaluation_mode: {evaluation_mode}")
    if not decision_cutoffs:
        raise ValueError("decision_cutoffs cannot be empty.")

    norm_cutoffs = sorted({_to_utc(tc) for tc in decision_cutoffs if _to_utc(tc) is not None})
    if not norm_cutoffs:
        raise ValueError("No valid UTC decision_cutoffs provided.")

    sensors = list(
        session.scalars(
            select(Sensor)
            .where(Sensor.parameter == "pm25")
            .order_by(Sensor.station_id.asc(), Sensor.id.asc())
        ).all()
    )
    if station_ids is not None:
        allow_set = set(station_ids)
        sensors = [s for s in sensors if s.station_id in allow_set]
    if not sensors:
        raise ValueError("No active PM2.5 sensors found for shadow forecasting.")

    sensor_ids = [s.id for s in sensors]
    actual_generated_at = datetime.now(UTC)

    # STRICT M8.6 PROSPECTIVE INVARIANT: target_time MUST be strictly in the future and absent from DB
    target_times = [c + timedelta(hours=1) for c in norm_cutoffs]
    if evaluation_mode == "prospective_shadow":
        min_target = min(target_times)
        if actual_generated_at >= min_target:
            raise ValueError(
                f"Backdated prospective violation: actual generation time {actual_generated_at.isoformat()} "
                f">= target_time {min_target.isoformat()}. Use generate_retrospective_replay_forecasts()."
            )
        existing_truth = session.scalars(
            select(Observation.id)
            .where(
                Observation.sensor_id.in_(sensor_ids),
                Observation.observed_at.in_(target_times),
            )
            .limit(1)
        ).first()
        if existing_truth is not None:
            raise ValueError(
                "Backdated prospective violation: target observations already exist in database. "
                "Retrospective evaluations must use evaluation_mode='retrospective_replay'."
            )

    sensor_to_station = {s.id: s.station_id for s in sensors}
    station_to_sensor = {s.station_id: s.id for s in sensors}
    replay = PointInTimeReplayEngine(session)
    feature_cols: list[str] = fitted_models["feature_columns"]
    nominal_cov = float(fitted_models["nominal_coverage"])
    model_sha_placeholder = hashlib.sha256(b"shadow-suite-m86").hexdigest()
    prov_map = fitted_models.get("model_provenance", {})

    batch_cutoffs_key = (
        "|".join(c.isoformat() for c in norm_cutoffs) + f"|stations={sorted(station_to_sensor)}"
    )
    max_cutoff = max(norm_cutoffs)
    runs_by_key: dict[str, ForecastRun] = {}
    new_keys_to_insert: list[str] = []

    for m_key, spec in SHADOW_MODEL_SPECS.items():
        m_prov = prov_map.get(m_key, {})
        imm_ref = str(
            m_prov.get("artifact_immutable_ref") or f"{spec['model_id']}@{spec['model_version']}#unpinned"
        )
        idem_raw = (
            f"shadow:{evaluation_mode}:{data_mode}:{spec['model_id']}:{spec['model_version']}:"
            f"{imm_ref}:{batch_cutoffs_key}"
        )
        idem_key = hashlib.sha256(idem_raw.encode("utf-8")).hexdigest()

        existing_run = session.scalars(
            select(ForecastRun).where(ForecastRun.idempotency_key == idem_key)
        ).first()
        if existing_run is not None:
            if idempotency_behavior == "return":
                runs_by_key[m_key] = existing_run
                continue
            raise ValueError(f"Duplicate ForecastRun rejected by idempotency_key={idem_key}")

        new_keys_to_insert.append(m_key)
        run = ForecastRun(
            model_id=spec["model_id"],
            model_version=spec["model_version"],
            model_sha256=model_sha_placeholder,
            dataset_hash=dataset_hash,
            algorithm=spec["algorithm"],
            execution_mode="shadow",
            evaluation_mode=evaluation_mode,
            data_mode=data_mode,
            model_created_at=m_prov.get("model_created_at"),
            artifact_immutable_ref=imm_ref,
            idempotency_key=idem_key,
            nominal_coverage=nominal_cov,
            issued_at=actual_generated_at,
            generated_at=actual_generated_at,
            persisted_at=actual_generated_at,
            decision_cutoff=max_cutoff,
            notes=f"Milestone 8.6 {evaluation_mode} batch",
        )
        session.add(run)
        runs_by_key[m_key] = run

    if not new_keys_to_insert:
        return list(runs_by_key.values())

    session.flush()

    pending_snapshots: list[ForecastFeatureSnapshot] = []
    pending_records: list[ForecastRecord] = []

    for cutoff in norm_cutoffs:
        target_ts = cutoff + timedelta(hours=1)

        vis_df, _summary = replay.query_as_of(
            sensor_ids=sensor_ids,
            decision_cutoff=cutoff,
            mode="prospective",
        )
        if vis_df.empty:
            continue

        vis_df["station_id"] = vis_df["sensor_id"].map(sensor_to_station).astype(str)
        vis_df = vis_df.rename(columns={"observed_at": "timestamp", "value": "pm25"})
        vis_df["timestamp"] = pd.to_datetime(vis_df["timestamp"], utc=True)
        vis_df["reported"] = True
        vis_df = vis_df[vis_df["timestamp"] <= cutoff]

        placeholders = pd.DataFrame(
            [
                {
                    "station_id": str(s.station_id),
                    "timestamp": pd.Timestamp(target_ts),
                    "pm25": np.nan,
                    "reported": False,
                }
                for s in sensors
            ]
        )
        combined_in = pd.concat(
            [vis_df[["station_id", "timestamp", "pm25", "reported"]], placeholders],
            ignore_index=True,
        )
        featured = build_spatiotemporal_features(combined_in, station_coords)
        target_slice = featured[featured["timestamp"] == pd.Timestamp(target_ts)].copy()

        for _, row in target_slice.iterrows():
            st_id = int(row["station_id"])
            sens_id = station_to_sensor.get(st_id)
            if sens_id is None:
                continue

            feat_payload = {
                col: None if pd.isna(row[col]) else round(float(row[col]), 6)
                for col in feature_cols
                if col in row.index
            }
            feat_json = json.dumps(feat_payload, sort_keys=True)
            feat_hash = hashlib.sha256(feat_json.encode("utf-8")).hexdigest()

            pending_snapshots.append(
                ForecastFeatureSnapshot(
                    sensor_id=sens_id,
                    decision_cutoff=cutoff,
                    target_time=target_ts,
                    features_json=feat_json,
                    feature_hash=feat_hash,
                    created_at=actual_generated_at,
                )
            )

            row_df = pd.DataFrame([row])
            age_val = float(row.get("age_hours", 999.0))
            newest_obs = (
                cutoff - timedelta(hours=age_val) if math.isfinite(age_val) and age_val < 1e6 else None
            )

            for m_key in new_keys_to_insert:
                pt, lo, hi, st_label, reason, d_sens, scen = _predict_single_shadow_model(
                    m_key, fitted_models, row_df
                )
                pending_records.append(
                    ForecastRecord(
                        run_id=runs_by_key[m_key].id,
                        station_id=st_id,
                        sensor_id=sens_id,
                        decision_cutoff=cutoff,
                        target_time=target_ts,
                        point_forecast=round(pt, 4),
                        lower_bound=None if st_label == "ABSTAIN" else round(lo, 4),
                        upper_bound=None if st_label == "ABSTAIN" else round(hi, 4),
                        nominal_coverage=nominal_cov,
                        status=st_label,
                        abstain_reason=reason,
                        d_sensitivity=round(d_sens, 4),
                        newest_source_observed_at=newest_obs,
                        feature_hash=feat_hash,
                        scenario_bounds_json=json.dumps(scen, sort_keys=True) if scen else None,
                        generated_at=actual_generated_at,
                        persisted_at=actual_generated_at,
                        created_at=actual_generated_at,
                    )
                )

    actual_persisted_at = datetime.now(UTC)
    if evaluation_mode == "prospective_shadow":
        min_target = min(target_times)
        if actual_persisted_at >= min_target:
            session.rollback()
            raise ValueError(
                f"Prospective commit aborted: persistence time {actual_persisted_at.isoformat()} >= target_time."
            )

    for m_key in new_keys_to_insert:
        runs_by_key[m_key].persisted_at = actual_persisted_at
    for snap in pending_snapshots:
        snap.created_at = actual_persisted_at
        session.add(snap)
    for rec in pending_records:
        rec.persisted_at = actual_persisted_at
        rec.created_at = actual_persisted_at
        session.add(rec)

    session.commit()
    return list(runs_by_key.values())


def generate_prospective_shadow_forecasts(
    session: Session,
    decision_cutoffs: list[datetime],
    fitted_models: dict[str, Any],
    station_coords: dict[str, tuple[float, float]],
    station_ids: list[int] | None = None,
    dataset_hash: str | None = None,
    data_mode: str = "shadow_live",
    idempotency_behavior: str = "return",
) -> list[ForecastRun]:
    """Generate genuine prospective forecasts strictly BEFORE target_time occurs or exists in DB."""
    return _generate_shadow_batch(
        session=session,
        decision_cutoffs=decision_cutoffs,
        fitted_models=fitted_models,
        station_coords=station_coords,
        evaluation_mode="prospective_shadow",
        station_ids=station_ids,
        dataset_hash=dataset_hash,
        data_mode=data_mode,
        idempotency_behavior=idempotency_behavior,
    )


def generate_retrospective_replay_forecasts(
    session: Session,
    decision_cutoffs: list[datetime],
    fitted_models: dict[str, Any],
    station_coords: dict[str, tuple[float, float]],
    station_ids: list[int] | None = None,
    dataset_hash: str | None = None,
    data_mode: str = "archived_historical",
    idempotency_behavior: str = "return",
) -> list[ForecastRun]:
    """Generate point-in-time historical replay predictions clearly flagged as retrospective_replay."""
    return _generate_shadow_batch(
        session=session,
        decision_cutoffs=decision_cutoffs,
        fitted_models=fitted_models,
        station_coords=station_coords,
        evaluation_mode="retrospective_replay",
        station_ids=station_ids,
        dataset_hash=dataset_hash,
        data_mode=data_mode,
        idempotency_behavior=idempotency_behavior,
    )


def _station_day_block_bootstrap_picp(
    station_ids: list[int],
    target_times: list[datetime],
    covered_flags: list[float],
    n_boot: int = 400,
    seed: int = 42,
) -> list[float] | None:
    """Clustered station-day bootstrap 95% CI for coverage preserving spatial/temporal correlation."""
    if not covered_flags:
        return None
    cov_arr = np.asarray(covered_flags, dtype=float)
    blocks = np.array([f"{st}_{ts.strftime('%Y-%m-%d')}" for st, ts in zip(station_ids, target_times)])
    unique_b, inv = np.unique(blocks, return_inverse=True)
    n_blocks = len(unique_b)
    if n_blocks < 2:
        return None
    b_sums = np.bincount(inv, weights=cov_arr, minlength=n_blocks)
    b_cnts = np.bincount(inv, minlength=n_blocks).astype(float)
    rng = np.random.default_rng(seed)
    idx_mat = rng.integers(0, n_blocks, size=(n_boot, n_blocks))
    den = np.maximum(b_cnts[idx_mat].sum(axis=1), 1.0)
    boot = b_sums[idx_mat].sum(axis=1) / den
    return [round(float(np.percentile(boot, 2.5)), 4), round(float(np.percentile(boot, 97.5)), 4)]


def _compute_subgroup_metrics(rows: list[dict[str, Any]], alpha: float) -> dict[str, Any]:
    if not rows:
        return {"n_matched": 0, "n_published": 0, "picp": None, "mpiw": None, "winkler": None, "mae": None}
    abs_errs = [abs(r["y"] - r["pt"]) for r in rows]
    pub_rows = [r for r in rows if r["is_pub"]]
    if not pub_rows:
        return {
            "n_matched": len(rows),
            "n_published": 0,
            "picp": None,
            "mpiw": None,
            "winkler": None,
            "mae": round(float(sum(abs_errs) / len(abs_errs)), 4),
        }
    cov = [1.0 if r["lo"] <= r["y"] <= r["hi"] else 0.0 for r in pub_rows]
    wids = [r["hi"] - r["lo"] for r in pub_rows]
    winks = [
        w
        if r["lo"] <= r["y"] <= r["hi"]
        else w + (2.0 / alpha) * (r["lo"] - r["y"] if r["y"] < r["lo"] else r["y"] - r["hi"])
        for r, w in zip(pub_rows, wids)
    ]
    return {
        "n_matched": len(rows),
        "n_published": len(pub_rows),
        "picp": round(float(sum(cov) / len(cov)), 4),
        "mpiw": round(float(sum(wids) / len(wids)), 4),
        "winkler": round(float(sum(winks) / len(winks)), 4),
        "mae": round(float(sum(abs_errs) / len(abs_errs)), 4),
    }


def _evaluate_shadow_subset(
    session: Session,
    evaluation_mode: str,
    min_matched_outcomes: int,
    min_unique_hours: int,
    delay_threshold_hours: float,
) -> dict[str, Any]:
    """Evaluate strictly either prospective_shadow OR retrospective_replay runs (never mixed)."""
    shadow_runs = list(
        session.scalars(
            select(ForecastRun)
            .where(
                ForecastRun.execution_mode == "shadow",
                ForecastRun.evaluation_mode == evaluation_mode,
            )
            .order_by(ForecastRun.id.asc())
        ).all()
    )
    if not shadow_runs:
        return {
            "status": f"NO_{evaluation_mode.upper()}_RUNS",
            "evaluation_mode": evaluation_mode,
            "calibration_claim_eligible": False,
            "adaptive_sage_promotion_eligible": False,
            "min_matched_outcomes_required": min_matched_outcomes,
            "min_unique_hours_required": min_unique_hours,
            "truly_prospective_count": 0,
            "backdated_violation_count": 0,
            "models": {},
        }

    runs_by_id = {r.id: r for r in shadow_runs}
    records = list(session.scalars(select(ForecastRecord).where(ForecastRecord.run_id.in_(list(runs_by_id)))).all())
    if not records:
        return {
            "status": f"NO_{evaluation_mode.upper()}_RECORDS",
            "evaluation_mode": evaluation_mode,
            "calibration_claim_eligible": False,
            "adaptive_sage_promotion_eligible": False,
            "min_matched_outcomes_required": min_matched_outcomes,
            "min_unique_hours_required": min_unique_hours,
            "truly_prospective_count": 0,
            "backdated_violation_count": 0,
            "models": {},
        }

    sensor_ids = sorted({r.sensor_id for r in records})
    target_times = sorted({_to_utc(r.target_time) for r in records if r.target_time is not None})

    valid_obs = list(
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
    for obs in valid_obs:
        t_o = _to_utc(obs.observed_at)
        if t_o is not None and (obs.sensor_id, t_o) not in truth_map and obs.value is not None:
            truth_map[(obs.sensor_id, t_o)] = obs

    incident_count = int(
        session.scalar(
            select(func.count(Observation.id)).where(
                Observation.sensor_id.in_(sensor_ids),
                Observation.quality_flag != "VALID",
            )
        )
        or 0
    )
    try:
        provider_incident_count = int(
            session.scalar(
                select(func.count(PilotIngestAuditRun.id)).where(
                    PilotIngestAuditRun.status.in_(
                        ["FAILED", "HTTP_429_BACKOFF", "QUOTA_EXHAUSTED", "SKIPPED_STALE_FENCING_TOKEN"]
                    )
                )
            )
            or 0
        )
    except Exception:
        provider_incident_count = 0

    by_algo: dict[str, list[ForecastRecord]] = {}
    for rec in records:
        algo = runs_by_id[rec.run_id].algorithm
        by_algo.setdefault(algo, []).append(rec)

    per_model: dict[str, Any] = {}
    published_lookup: dict[str, dict[tuple[int, datetime], tuple[float, float, float]]] = {}
    total_truly_prospective = 0
    total_backdated_violations = 0
    min_matched_seen: int | None = None
    min_hours_seen: int | None = None

    for algo, algo_recs in sorted(by_algo.items()):
        matched_rows: list[dict[str, Any]] = []
        unmatched = 0
        delayed = 0
        abstained_matched = 0
        stale_abstain_cnt = 0
        backdated_violations = 0
        prospective_valid_recs = 0
        source_ages: list[float] = []
        pub_stations: list[int] = []
        pub_targets: list[datetime] = []
        pub_cov_flags: list[float] = []
        widths: list[float] = []
        winklers: list[float] = []
        abs_errs: list[float] = []
        d_sens_vals: list[float] = []
        alpha = 1.0 - float(algo_recs[0].nominal_coverage or 0.90)
        published_lookup[algo] = {}

        for rec in algo_recs:
            run_obj = runs_by_id[rec.run_id]
            t_target = _to_utc(rec.target_time)
            t_cutoff = _to_utc(rec.decision_cutoff)
            t_persisted = _to_utc(rec.persisted_at) or _to_utc(rec.created_at)
            t_generated = _to_utc(rec.generated_at) or _to_utc(run_obj.generated_at)
            obs = truth_map.get((rec.sensor_id, t_target)) if t_target else None

            # Genuine prospective check: generation AND persistence occurred BEFORE target_time
            # and BEFORE any arrival of matched target observation
            if evaluation_mode == "prospective_shadow":
                pre_target_ok = bool(
                    t_target is not None
                    and t_generated is not None
                    and t_persisted is not None
                    and t_generated < t_target
                    and t_persisted < t_target
                )
                obs_pers = _to_utc(obs.persisted_at) if obs is not None else None
                pre_obs_ok = (
                    True
                    if obs is None
                    else bool(obs_pers is not None and t_persisted is not None and obs_pers > t_persisted)
                )
                if not (pre_target_ok and pre_obs_ok):
                    backdated_violations += 1
                    total_backdated_violations += 1
                    continue
                prospective_valid_recs += 1
                total_truly_prospective += 1

            src_age = (
                max(0.0, (t_cutoff - _to_utc(rec.newest_source_observed_at)).total_seconds() / 3600.0)
                if t_cutoff and rec.newest_source_observed_at
                else None
            )
            if src_age is not None:
                source_ages.append(src_age)
            if rec.status.upper() == "ABSTAIN" and "stale" in rec.abstain_reason.lower():
                stale_abstain_cnt += 1

            if obs is None or obs.value is None or t_target is None:
                unmatched += 1
                continue

            y_val = float(obs.value)
            abs_errs.append(abs(y_val - float(rec.point_forecast)))
            if rec.d_sensitivity is not None:
                d_sens_vals.append(float(rec.d_sensitivity))
            fetch_t = _to_utc(obs.fetched_at) or t_target
            if (fetch_t - t_target).total_seconds() / 3600.0 > delay_threshold_hours:
                delayed += 1

            is_pub = (
                rec.status.upper() != "ABSTAIN"
                and rec.lower_bound is not None
                and rec.upper_bound is not None
            )
            lo = float(rec.lower_bound) if is_pub else math.nan  # type: ignore[arg-type]
            hi = float(rec.upper_bound) if is_pub else math.nan  # type: ignore[arg-type]

            if is_pub:
                pub_stations.append(rec.station_id)
                pub_targets.append(t_target)
                w = hi - lo
                widths.append(w)
                covered = 1.0 if (lo <= y_val <= hi) else 0.0
                pub_cov_flags.append(covered)
                if covered == 1.0:
                    winklers.append(w)
                else:
                    miss = (lo - y_val) if y_val < lo else (y_val - hi)
                    winklers.append(w + (2.0 / alpha) * miss)
                published_lookup[algo][(rec.sensor_id, t_target)] = (y_val, lo, hi)
            else:
                abstained_matched += 1

            matched_rows.append(
                {
                    "sensor_id": rec.sensor_id,
                    "station_id": rec.station_id,
                    "target_time": t_target,
                    "y": y_val,
                    "pt": float(rec.point_forecast),
                    "lo": lo,
                    "hi": hi,
                    "is_pub": is_pub,
                    "src_age": src_age if src_age is not None else 0.0,
                }
            )

        matched = len(matched_rows)
        pub_n = len(pub_cov_flags)
        pub_cov = int(sum(pub_cov_flags))
        uniq_hours = len({r["target_time"] for r in matched_rows})
        uniq_days = len({r["target_time"].strftime("%Y-%m-%d") for r in matched_rows})
        uniq_stations = len({r["station_id"] for r in matched_rows})

        min_matched_seen = matched if min_matched_seen is None else min(min_matched_seen, matched)
        min_hours_seen = uniq_hours if min_hours_seen is None else min(min_hours_seen, uniq_hours)

        ci_wilson = compute_wilson_ci(pub_cov, pub_n) if pub_n > 0 else None
        ci_block = (
            _station_day_block_bootstrap_picp(pub_stations, pub_targets, pub_cov_flags) if pub_n > 0 else None
        )

        # Subgroups: high pollution (>= max(15.0, p75)) & long outage (source age >= 6.0h)
        y_vals = [r["y"] for r in matched_rows]
        hp_thresh = max(15.0, float(np.percentile(y_vals, 75))) if y_vals else 15.0
        hp_rows = [r for r in matched_rows if r["y"] >= hp_thresh]
        outage_rows = [r for r in matched_rows if r["src_age"] >= 6.0]

        eligible = bool(
            matched >= min_matched_outcomes and uniq_hours >= min_unique_hours and backdated_violations == 0
        )
        total_eval = matched + unmatched

        per_model[algo] = {
            "total_forecasts": len(algo_recs),
            "truly_prospective_count": prospective_valid_recs
            if evaluation_mode == "prospective_shadow"
            else 0,
            "backdated_violation_count": backdated_violations,
            "matched_count": matched,
            "unmatched_missing_truth_count": unmatched,
            "missing_label_rate": round(unmatched / total_eval, 4) if total_eval > 0 else 0.0,
            "delayed_arrival_count": delayed,
            "delayed_arrival_rate": round(delayed / matched, 4) if matched > 0 else 0.0,
            "published_matched_count": pub_n,
            "abstained_matched_count": abstained_matched,
            "publication_availability": round(pub_n / matched, 4) if matched > 0 else None,
            "unique_target_hours": uniq_hours,
            "unique_calendar_days": uniq_days,
            "stations_represented": uniq_stations,
            "published_picp": round(pub_cov / pub_n, 4) if pub_n > 0 else None,
            "published_picp_ci95": [round(ci_wilson[0], 4), round(ci_wilson[1], 4)] if ci_wilson else None,
            "published_picp_block_ci95": ci_block,
            "published_mpiw": round(sum(widths) / len(widths), 4) if widths else None,
            "published_winkler": round(sum(winklers) / len(winklers), 4) if winklers else None,
            "mae": round(sum(abs_errs) / len(abs_errs), 4) if abs_errs else None,
            "d_sens_mean": round(sum(d_sens_vals) / len(d_sens_vals), 4) if d_sens_vals else None,
            "freshness": {
                "mean_source_age_hours": round(sum(source_ages) / len(source_ages), 3)
                if source_ages
                else None,
                "max_source_age_hours": round(max(source_ages), 3) if source_ages else None,
                "stale_abstain_count": stale_abstain_cnt,
            },
            "subgroups": {
                "high_pollution": {
                    "threshold_ug_m3": round(hp_thresh, 2),
                    **_compute_subgroup_metrics(hp_rows, alpha),
                },
                "long_outage": {"min_age_hours": 6.0, **_compute_subgroup_metrics(outage_rows, alpha)},
            },
            "calibration_claim_eligible": eligible,
        }

    # Matched-availability parity comparisons against MAR, Persistence, and CQR
    paired_vs_baselines: dict[str, Any] = {}
    if "ADAPTIVE-SAGE-LIGHTGBM" in published_lookup:
        adap_map = published_lookup["ADAPTIVE-SAGE-LIGHTGBM"]
        for ref_algo in ("LOCALLY-WEIGHTED-MAR", "PERSISTENCE-SPLIT", "LIGHTGBM-CQR", "SAGE-STATIC-LIGHTGBM"):
            if ref_algo not in published_lookup:
                continue
            ref_map = published_lookup[ref_algo]
            common_keys = sorted(set(adap_map) & set(ref_map))
            if not common_keys:
                paired_vs_baselines[ref_algo] = {"n_common_published": 0}
                continue
            d_cov, d_wid, d_wink = [], [], []
            for k in common_keys:
                y, alo, ahi = adap_map[k]
                _, blo, bhi = ref_map[k]
                aw, bw = ahi - alo, bhi - blo
                acov = 1.0 if alo <= y <= ahi else 0.0
                bcov = 1.0 if blo <= y <= bhi else 0.0
                awink = aw if acov == 1.0 else aw + 20.0 * ((alo - y) if y < alo else (y - ahi))
                bwink = bw if bcov == 1.0 else bw + 20.0 * ((blo - y) if y < blo else (y - bhi))
                d_cov.append(acov - bcov)
                d_wid.append(aw - bw)
                d_wink.append(awink - bwink)
            n_c = len(common_keys)
            paired_vs_baselines[ref_algo] = {
                "n_common_published": n_c,
                "adaptive_minus_baseline_picp": round(sum(d_cov) / n_c, 4),
                "adaptive_minus_baseline_mpiw": round(sum(d_wid) / n_c, 4),
                "adaptive_minus_baseline_winkler": round(sum(d_wink) / n_c, 4),
            }

    overall_eligible = bool(
        min_matched_seen is not None
        and min_matched_seen >= min_matched_outcomes
        and min_hours_seen is not None
        and min_hours_seen >= min_unique_hours
        and total_backdated_violations == 0
    )

    # Promotion block: Adaptive SAGE never eligible unless strictly eligible & beating MAR on matched parity
    mar_delta = paired_vs_baselines.get("LOCALLY-WEIGHTED-MAR", {}).get("adaptive_minus_baseline_winkler")
    adaptive_promo_ok = bool(overall_eligible and mar_delta is not None and mar_delta < 0.0)

    return {
        "status": "EVALUATED" if overall_eligible else "INSUFFICIENT_OBSERVED_OUTCOMES",
        "evaluation_mode": evaluation_mode,
        "calibration_claim_eligible": overall_eligible,
        "adaptive_sage_promotion_eligible": adaptive_promo_ok,
        "min_matched_outcomes_required": min_matched_outcomes,
        "min_unique_hours_required": min_unique_hours,
        "truly_prospective_count": total_truly_prospective if evaluation_mode == "prospective_shadow" else 0,
        "backdated_violation_count": total_backdated_violations,
        "data_quality_incident_count": incident_count,
        "provider_incident_count": provider_incident_count,
        "matched_availability_vs_baselines": paired_vs_baselines,
        "mnar_governance_note": (
            "Genuine missing labels remain unidentifiable without auxiliary measurements; "
            "retrospective replays and simulated outages are strictly isolated from prospective evidence."
        ),
        "models": per_model,
    }


def reconcile_shadow_evaluation(
    session: Session,
    min_matched_outcomes: int = 100,
    min_unique_hours: int = 24,
    delay_threshold_hours: float = 1.5,
    evaluation_mode: str = "prospective_shadow",
) -> dict[str, Any]:
    """Evaluate prospective shadow predictions separately from retrospective replays."""
    primary = _evaluate_shadow_subset(
        session=session,
        evaluation_mode=evaluation_mode,
        min_matched_outcomes=min_matched_outcomes,
        min_unique_hours=min_unique_hours,
        delay_threshold_hours=delay_threshold_hours,
    )
    if evaluation_mode == "prospective_shadow":
        retro_count = int(
            session.scalar(
                select(func.count(ForecastRun.id)).where(
                    ForecastRun.execution_mode == "shadow",
                    ForecastRun.evaluation_mode == "retrospective_replay",
                )
            )
            or 0
        )
        primary["retrospective_run_count"] = retro_count
        if retro_count > 0:
            primary["retrospective_summary"] = _evaluate_shadow_subset(
                session=session,
                evaluation_mode="retrospective_replay",
                min_matched_outcomes=min_matched_outcomes,
                min_unique_hours=min_unique_hours,
                delay_threshold_hours=delay_threshold_hours,
            )
    return primary
