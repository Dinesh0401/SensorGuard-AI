"""Prospective shadow forecast engine and delayed ground-truth evaluator (Phase 8.2).

Generates forecasts from Persistence, Locally-Weighted MAR, LightGBM CQR, Static SAGE,
and Adaptive SAGE strictly using observations visible at `decision_cutoff` (`observed_at <= cutoff`,
`fetched_at <= cutoff`, `persisted_at <= cutoff`), persists ForecastFeatureSnapshot and
shadow ForecastRun/ForecastRecord rows BEFORE targets arrive, and reconciles coverage,
width, Winkler score, availability, MAE, delayed labels, and quality incidents only once
sufficient arrived observations accumulate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sensorguard.calibration import ConformalizedQuantileRegression, SplitConformalCalibrator, policy
from sensorguard.db.schema import (
    ForecastFeatureSnapshot,
    ForecastRecord,
    ForecastRun,
    Observation,
    Sensor,
)
from sensorguard.models.data import build_spatiotemporal_features, partition_dataset
from sensorguard.models.forecasters import (
    LightGBMPointForecaster,
    LightGBMQuantileForecaster,
    PersistenceForecaster,
)
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
    """Train all 5 shadow models strictly on chronological historical splits."""
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

    return {
        "feature_columns": cols,
        "nominal_coverage": nominal_coverage,
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


def generate_prospective_shadow_forecasts(
    session: Session,
    decision_cutoffs: list[datetime],
    fitted_models: dict[str, Any],
    station_coords: dict[str, tuple[float, float]],
    station_ids: list[int] | None = None,
    dataset_hash: str | None = None,
    data_mode: str = "shadow_live",
) -> list[ForecastRun]:
    """Generate and persist causal shadow predictions BEFORE target timestamps occur.

    For each decision_cutoff t_c, queries PointInTimeReplayEngine(mode='prospective') so
    only observations with observed_at <= t_c, fetched_at <= t_c, persisted_at <= t_c
    are visible. Rebuilds issue-time features for target_time = t_c + 1h without touching
    t_c + 1h, stores ForecastFeatureSnapshot, and inserts ForecastRun(execution_mode='shadow').
    """
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

    sensor_to_station = {s.id: s.station_id for s in sensors}
    station_to_sensor = {s.station_id: s.id for s in sensors}
    replay = PointInTimeReplayEngine(session)
    feature_cols: list[str] = fitted_models["feature_columns"]
    nominal_cov = float(fitted_models["nominal_coverage"])
    model_sha_placeholder = hashlib.sha256(b"shadow-suite-m8").hexdigest()

    # Create 1 shadow ForecastRun per shadow model across the evaluated decision batch
    issued_now = datetime.now(UTC)
    max_cutoff = max(_to_utc(tc) or issued_now for tc in decision_cutoffs)
    runs_by_key: dict[str, ForecastRun] = {}
    for m_key, spec in SHADOW_MODEL_SPECS.items():
        run = ForecastRun(
            model_id=spec["model_id"],
            model_version=spec["model_version"],
            model_sha256=model_sha_placeholder,
            dataset_hash=dataset_hash,
            algorithm=spec["algorithm"],
            execution_mode="shadow",
            data_mode=data_mode,
            nominal_coverage=nominal_cov,
            issued_at=min(max_cutoff, issued_now),
            decision_cutoff=max_cutoff,
            notes="Phase 8.2 causal prospective shadow batch",
        )
        session.add(run)
        runs_by_key[m_key] = run
    session.flush()

    for raw_cutoff in sorted(decision_cutoffs):
        cutoff = _to_utc(raw_cutoff)
        if cutoff is None:
            continue
        target_ts = cutoff + timedelta(hours=1)

        vis_df, _summary = replay.query_as_of(
            sensor_ids=[s.id for s in sensors],
            decision_cutoff=cutoff,
            mode="prospective",
        )
        if vis_df.empty:
            continue

        # Append synthetic future slot placeholders ONLY for feature extraction at target_ts
        # with target=NaN / reported=False so build_spatiotemporal_features pulls lags <= cutoff
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

            snap = ForecastFeatureSnapshot(
                sensor_id=sens_id,
                decision_cutoff=cutoff,
                target_time=target_ts,
                features_json=feat_json,
                feature_hash=feat_hash,
                created_at=cutoff,
            )
            session.add(snap)

            row_df = pd.DataFrame([row])
            age_val = float(row.get("age_hours", 999.0))
            newest_obs = (
                cutoff - timedelta(hours=age_val) if math.isfinite(age_val) and age_val < 1e6 else None
            )

            for m_key in SHADOW_MODEL_SPECS:
                pt, lo, hi, st_label, reason, d_sens, scen = _predict_single_shadow_model(
                    m_key, fitted_models, row_df
                )
                session.add(
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
                        created_at=cutoff,
                    )
                )

    session.commit()
    return list(runs_by_key.values())


def reconcile_shadow_evaluation(
    session: Session,
    min_matched_outcomes: int = 100,
    delay_threshold_hours: float = 1.5,
) -> dict[str, Any]:
    """Post-hoc evaluation across shadow ForecastRuns strictly enforcing sufficient matched outcomes."""
    shadow_runs = list(
        session.scalars(
            select(ForecastRun).where(ForecastRun.execution_mode == "shadow").order_by(ForecastRun.id.asc())
        ).all()
    )
    if not shadow_runs:
        return {
            "status": "NO_SHADOW_RUNS",
            "calibration_claim_eligible": False,
            "min_matched_outcomes_required": min_matched_outcomes,
            "models": {},
        }

    run_ids = [r.id for r in shadow_runs]
    records = list(session.scalars(select(ForecastRecord).where(ForecastRecord.run_id.in_(run_ids))).all())
    if not records:
        return {
            "status": "NO_SHADOW_RECORDS",
            "calibration_claim_eligible": False,
            "min_matched_outcomes_required": min_matched_outcomes,
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

    run_to_algo = {r.id: r.algorithm for r in shadow_runs}
    by_algo: dict[str, list[ForecastRecord]] = {}
    for rec in records:
        algo = run_to_algo.get(rec.run_id, "UNKNOWN")
        by_algo.setdefault(algo, []).append(rec)

    per_model: dict[str, Any] = {}
    min_matched_seen: int | None = None

    for algo, algo_recs in sorted(by_algo.items()):
        matched = 0
        unmatched = 0
        delayed = 0
        pub_n = 0
        pub_cov = 0
        widths: list[float] = []
        winklers: list[float] = []
        abs_errs: list[float] = []
        alpha = 1.0 - float(algo_recs[0].nominal_coverage or 0.90)

        for rec in algo_recs:
            t_target = _to_utc(rec.target_time)
            obs = truth_map.get((rec.sensor_id, t_target)) if t_target else None
            if obs is None or obs.value is None or t_target is None:
                unmatched += 1
                continue

            matched += 1
            y_val = float(obs.value)
            abs_errs.append(abs(y_val - float(rec.point_forecast)))
            fetch_t = _to_utc(obs.fetched_at) or t_target
            if (fetch_t - t_target).total_seconds() / 3600.0 > delay_threshold_hours:
                delayed += 1

            is_pub = (
                rec.status.upper() != "ABSTAIN"
                and rec.lower_bound is not None
                and rec.upper_bound is not None
            )
            if is_pub:
                pub_n += 1
                lo = float(rec.lower_bound)  # type: ignore[arg-type]
                hi = float(rec.upper_bound)  # type: ignore[arg-type]
                w = hi - lo
                widths.append(w)
                if lo <= y_val <= hi:
                    pub_cov += 1
                    winklers.append(w)
                else:
                    miss = (lo - y_val) if y_val < lo else (y_val - hi)
                    winklers.append(w + (2.0 / alpha) * miss)

        min_matched_seen = matched if min_matched_seen is None else min(min_matched_seen, matched)
        eligible = matched >= min_matched_outcomes
        pub_picp = (pub_cov / pub_n) if pub_n > 0 else None
        ci = compute_wilson_ci(pub_cov, pub_n) if pub_n > 0 else None

        per_model[algo] = {
            "total_forecasts": len(algo_recs),
            "matched_count": matched,
            "unmatched_missing_truth_count": unmatched,
            "missing_label_rate": round(unmatched / len(algo_recs), 4),
            "delayed_arrival_count": delayed,
            "published_matched_count": pub_n,
            "publication_availability": round(pub_n / matched, 4) if matched > 0 else None,
            "published_picp": round(pub_picp, 4) if pub_picp is not None else None,
            "published_picp_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
            "published_mpiw": round(sum(widths) / len(widths), 4) if widths else None,
            "published_winkler": round(sum(winklers) / len(winklers), 4) if winklers else None,
            "mae": round(sum(abs_errs) / len(abs_errs), 4) if abs_errs else None,
            "calibration_claim_eligible": eligible,
        }

    overall_eligible = bool(min_matched_seen is not None and min_matched_seen >= min_matched_outcomes)
    return {
        "status": "EVALUATED" if overall_eligible else "INSUFFICIENT_OBSERVED_OUTCOMES",
        "calibration_claim_eligible": overall_eligible,
        "min_matched_outcomes_required": min_matched_outcomes,
        "data_quality_incident_count": incident_count,
        "mnar_governance_note": (
            "Genuine missing labels remain unidentifiable without auxiliary measurements; "
            "never treat simulated outage labels as genuine real-world MNAR evidence."
        ),
        "models": per_model,
    }
