"""Run rigorous M7.4 Adaptive Input-Dependent SAGE comparison & 8-fold LOSO evaluation.

Evaluates pre-specified adaptive SAGE policies against static SAGE, Persistence,
Split Conformal, CQR, Rolling Conformal, ACI, Covariate Propensity-Weighted MAR,
Locally-Weighted MAR, and Observation-Gated Masked ACI on identical chronological splits,
3 random outage seeds ([11, 42, 107]), and 8-fold Leave-One-Station-Out CV.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from sensorguard.ablation import DEFAULT_SEEDS, prepare_scenario_test_data
from sensorguard.calibration import (
    AdaptiveConformalInference,
    ConformalizedQuantileRegression,
    RollingConformalCalibrator,
    SplitConformalCalibrator,
    compute_interval_metrics,
    policy,
)
from sensorguard.db.session import create_db_engine, get_session_factory
from sensorguard.models.data import (
    build_spatiotemporal_features,
    compute_dataset_content_hash,
    load_pilot_dataset_from_db,
    partition_dataset,
)
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
    masked_observation_aci_intervals,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _summarize_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    is_published: np.ndarray,
    disagreement: np.ndarray | None = None,
    alpha: float = 0.10,
) -> dict[str, Any]:
    m = compute_interval_metrics(
        y_true=y_true,
        y_pred=y_pred,
        lower=lower,
        upper=upper,
        alpha=alpha,
        is_published=is_published,
    )
    valid = np.isfinite(y_true) & np.isfinite(y_pred) & np.isfinite(lower) & np.isfinite(upper)
    n_valid = int(valid.sum())
    cov_arr = (y_true[valid] >= lower[valid]) & (y_true[valid] <= upper[valid])
    k_all = int(cov_arr.sum())
    ci_all = compute_wilson_ci(k_all, n_valid)

    pub_valid = valid & is_published
    n_pub = int(pub_valid.sum())
    k_pub = (
        int(((y_true[pub_valid] >= lower[pub_valid]) & (y_true[pub_valid] <= upper[pub_valid])).sum())
        if n_pub > 0
        else 0
    )
    ci_pub = compute_wilson_ci(k_pub, n_pub) if n_pub > 0 else (0.0, 1.0)

    out = {
        "picp": m["overall_picp"],
        "published_picp": m["published_picp"],
        "picp_ci95": [round(ci_all[0], 4), round(ci_all[1], 4)],
        "pub_picp_ci95": [round(ci_pub[0], 4), round(ci_pub[1], 4)],
        "mpiw": m["mpiw"],
        "published_mpiw": m["published_mpiw"],
        "winkler": m["winkler_score"],
        "abstention_rate": m["abstention_rate"],
        "mae": m["mae"],
        "n_valid": n_valid,
        "n_published": n_pub,
    }
    if disagreement is not None and valid.any():
        d_val = disagreement[valid]
        err_val = np.abs(y_true[valid] - y_pred[valid])
        rho, pval = spearmanr(d_val, err_val)
        out["d_sens_mean"] = round(float(np.mean(d_val)), 4)
        out["d_sens_std"] = round(float(np.std(d_val)), 4)
        out["d_sens_p95"] = round(float(np.quantile(d_val, 0.95)), 4)
        out["d_sens_abs_err_spearman"] = round(float(rho), 4) if np.isfinite(rho) else 0.0
        out["d_sens_abs_err_pval"] = float(pval) if np.isfinite(pval) else 1.0
    return out


def run_adaptive_research_suite() -> dict[str, Any]:
    engine = create_db_engine()
    session_factory = get_session_factory(engine)
    with session_factory() as session:
        df, station_coords = load_pilot_dataset_from_db(session)

    featured_df = build_spatiotemporal_features(df, station_coords)
    splits = partition_dataset(featured_df, station_coords, train_pct=0.60, cal_pct=0.20)
    data_hash = compute_dataset_content_hash(splits)

    train_df, cal_df, test_df = splits.train, splits.calibrate, splits.test
    cols = splits.feature_columns
    y_tr = train_df["target"].to_numpy()
    y_cal = cal_df["target"].to_numpy()
    alpha = 0.10

    # 1. Fit shared baselines once cleanly on identical splits
    persist = PersistenceForecaster().fit(train_df, y_tr)
    persist_cal = SplitConformalCalibrator(alpha=alpha).fit(y_cal, persist.predict(cal_df))

    lgb_pt = LightGBMPointForecaster(feature_columns=cols).fit(train_df, y_tr)
    if hasattr(lgb_pt.model, "set_params"):
        lgb_pt.model.set_params(n_jobs=1)
    lgb_split = SplitConformalCalibrator(alpha=alpha).fit(y_cal, lgb_pt.predict(cal_df))

    lgb_q = LightGBMQuantileForecaster(feature_columns=cols, quantiles=(0.05, 0.50, 0.95)).fit(train_df, y_tr)
    cal_q = lgb_q.predict_quantiles(cal_df)
    cqr = ConformalizedQuantileRegression(alpha=alpha).fit(y_cal, cal_q[0.05], cal_q[0.95])

    # Static SAGE (M6.1 baseline)
    sage_static = SAGEForecaster(
        config=SAGEConfig(
            gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0),
            nominal_coverage=0.90,
            max_disagreement_threshold=8.0,
        ),
        feature_columns=cols,
    ).fit(train_df, y_tr, cal_df, y_cal)

    # Unfiltered pre-calibration window allows causal propensity P(R=1|X) estimation on missing + reported hours
    cal_start_ts = cal_df["timestamp"].min()
    unfiltered_train_df = featured_df[featured_df["timestamp"] < cal_start_ts].copy()
    y_tr_unfiltered = unfiltered_train_df["target"].to_numpy()

    # Locally-Weighted MAR Conformal (gamma=0.0 adaptive baseline isolating OOF s(x) without MNAR grid)
    mar_local = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            gamma_grid=(0.0,),
            nominal_coverage=0.90,
            max_disagreement_threshold=9999.0,
            propensity_risk_weight=0.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_train_df, y_tr_unfiltered, cal_df, y_cal)

    # Adaptive SAGE M7.4 candidate (fit once, evaluate under 3 pre-specified policies)
    sage_adap_tau8 = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0),
            nominal_coverage=0.90,
            max_disagreement_threshold=8.0,
            propensity_risk_weight=1.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_train_df, y_tr_unfiltered, cal_df, y_cal)

    sage_adap_cal95 = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0),
            nominal_coverage=0.90,
            max_disagreement_threshold="cal_p95",
            propensity_risk_weight=1.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_train_df, y_tr_unfiltered, cal_df, y_cal)

    sage_adap_no_abstain = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0),
            nominal_coverage=0.90,
            max_disagreement_threshold=1e9,
            stale_age_hours=1e9,
            max_envelope_width_threshold=1e9,
            propensity_risk_weight=1.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_train_df, y_tr_unfiltered, cal_df, y_cal)

    train_mean = float(np.nanmean(y_tr))
    train_std = float(np.nanstd(y_tr))
    history_df = pd.concat([train_df, cal_df], ignore_index=True)
    cal_preds_lgb = lgb_pt.predict(cal_df)
    cal_res_abs = np.abs(y_cal - cal_preds_lgb)

    scenarios = ["natural", "mcar_20", "mnar_gamma_1.5", "burst_24h"]
    chron_results: dict[str, dict[str, Any]] = {}

    for scen in scenarios:
        seeds = [42] if scen == "natural" else list(DEFAULT_SEEDS)
        per_seed: dict[str, list[dict[str, Any]]] = {}

        for seed in seeds:
            scen_df, y_eval = prepare_scenario_test_data(
                test_df=test_df,
                scenario=scen,
                seed=seed,
                train_mean=train_mean,
                train_std=train_std,
                station_coords=station_coords,
                history_df=history_df,
            )
            ages = scen_df["age_hours"].to_numpy()
            reported_vec = scen_df["reported"].fillna(False).to_numpy(dtype=bool)
            base_statuses, _ = policy(ages)
            base_pub = base_statuses != "abstain"

            # Persistence
            p_pred = persist.predict(scen_df)
            p_lo, p_hi = persist_cal.predict_interval(p_pred)
            per_seed.setdefault("persistence", []).append(
                _summarize_metrics(y_eval, p_pred, p_lo, p_hi, base_pub)
            )

            # LightGBM Split
            lgb_pred = lgb_pt.predict(scen_df)
            s_lo, s_hi = lgb_split.predict_interval(lgb_pred)
            per_seed.setdefault("lightgbm_split", []).append(
                _summarize_metrics(y_eval, lgb_pred, s_lo, s_hi, base_pub)
            )

            # LightGBM CQR
            t_q = lgb_q.predict_quantiles(scen_df)
            c_lo, c_hi = cqr.predict_interval(t_q[0.05], t_q[0.95])
            per_seed.setdefault("lightgbm_cqr", []).append(
                _summarize_metrics(y_eval, t_q[0.50], c_lo, c_hi, base_pub)
            )

            # Rolling Conformal (168h)
            roll = RollingConformalCalibrator(window_size=168, alpha=alpha)
            for yt_cal, yp_cal in zip(y_cal[-168:], cal_preds_lgb[-168:]):
                roll.update(float(yt_cal), float(yp_cal))
            r_lo = np.zeros(len(scen_df))
            r_hi = np.zeros(len(scen_df))
            for idx_r in range(len(scen_df)):
                lo_r, hi_r = roll.predict_interval(float(lgb_pred[idx_r]))
                r_lo[idx_r], r_hi[idx_r] = lo_r, hi_r
                if reported_vec[idx_r] and np.isfinite(y_eval[idx_r]):
                    roll.update(float(y_eval[idx_r]), float(lgb_pred[idx_r]))
            per_seed.setdefault("lightgbm_rolling", []).append(
                _summarize_metrics(y_eval, lgb_pred, r_lo, r_hi, base_pub)
            )

            # Standard ACI (baseline comparison)
            aci = AdaptiveConformalInference(target_alpha=alpha, gamma=0.01)
            aci_base_q = float(np.quantile(cal_res_abs, 1.0 - alpha)) if cal_res_abs.size > 0 else 5.0
            a_lo = np.zeros(len(scen_df))
            a_hi = np.zeros(len(scen_df))
            for idx_a in range(len(scen_df)):
                scale = (
                    float(np.quantile(cal_res_abs, 1.0 - aci.current_alpha))
                    if cal_res_abs.size > 0
                    else aci_base_q
                )
                lo_a, hi_a = max(0.0, float(lgb_pred[idx_a]) - scale), float(lgb_pred[idx_a]) + scale
                a_lo[idx_a], a_hi[idx_a] = lo_a, hi_a
                if reported_vec[idx_a] and np.isfinite(y_eval[idx_a]):
                    aci.step(bool(lo_a <= y_eval[idx_a] <= hi_a))
            per_seed.setdefault("lightgbm_aci", []).append(
                _summarize_metrics(y_eval, lgb_pred, a_lo, a_hi, base_pub)
            )

            # Missing-Data Observation-Gated Masked ACI
            m_lo, m_hi = masked_observation_aci_intervals(
                y_cal=y_cal,
                pred_cal=cal_preds_lgb,
                y_test=y_eval,
                pred_test=lgb_pred,
                reported_test=reported_vec,
                target_alpha=alpha,
                gamma_step=0.05,
                window=168,
            )
            per_seed.setdefault("masked_observation_aci", []).append(
                _summarize_metrics(y_eval, lgb_pred, m_lo, m_hi, base_pub)
            )

            # Static SAGE
            sr = sage_static.predict_guarded(scen_df)
            per_seed.setdefault("sage_static", []).append(
                _summarize_metrics(
                    y_eval,
                    sr.point_forecast,
                    sr.lower_envelope,
                    sr.upper_envelope,
                    sr.status != "abstain",
                    sr.scenario_disagreement,
                )
            )

            # Locally-Weighted MAR (gamma=0 adaptive control)
            mr = mar_local.predict_guarded(scen_df)
            per_seed.setdefault("locally_weighted_mar", []).append(
                _summarize_metrics(
                    y_eval,
                    mr.point_forecast,
                    mr.lower_envelope,
                    mr.upper_envelope,
                    mr.status != "abstain",
                    mr.scenario_disagreement,
                )
            )

            # Adaptive SAGE variants
            for label_a, m_obj in [
                ("sage_adaptive_tau8", sage_adap_tau8),
                ("sage_adaptive_cal95", sage_adap_cal95),
                ("sage_adaptive_no_abstain", sage_adap_no_abstain),
            ]:
                ar = m_obj.predict_guarded(scen_df)
                per_seed.setdefault(label_a, []).append(
                    _summarize_metrics(
                        y_eval,
                        ar.point_forecast,
                        ar.lower_envelope,
                        ar.upper_envelope,
                        ar.status != "abstain",
                        ar.scenario_disagreement,
                    )
                )

        # Aggregate across seeds
        scen_agg: dict[str, Any] = {}
        for m_key, seed_list in per_seed.items():
            agg: dict[str, Any] = {"n_seeds": len(seed_list)}
            numeric_keys = [
                "picp",
                "published_picp",
                "mpiw",
                "published_mpiw",
                "winkler",
                "abstention_rate",
                "mae",
                "d_sens_mean",
                "d_sens_std",
                "d_sens_p95",
                "d_sens_abs_err_spearman",
            ]
            for nk in numeric_keys:
                vals = [s[nk] for s in seed_list if s.get(nk) is not None]
                if vals:
                    agg[nk] = round(float(np.mean(vals)), 4)
                    agg[f"{nk}_std"] = round(float(np.std(vals)), 4)
            if len(seed_list) == 1:
                agg["picp_ci95"] = seed_list[0]["picp_ci95"]
                agg["pub_picp_ci95"] = seed_list[0]["pub_picp_ci95"]
            scen_agg[m_key] = agg
        chron_results[scen] = scen_agg

    # 2. 8-Fold Leave-One-Station-Out (LOSO) Natural Evaluation
    loso_stations = sorted(featured_df["station_id"].unique(), key=int)
    loso_results: dict[str, Any] = {}

    for held_st in loso_stations:
        other_sts = [s for s in loso_stations if s != held_st]
        other_coords = {k: v for k, v in station_coords.items() if k in other_sts}
        tr_cal_df = featured_df[featured_df["station_id"].isin(other_sts)].copy()
        te_df = featured_df[featured_df["station_id"] == held_st].copy()
        sp_o = partition_dataset(tr_cal_df, other_coords, train_pct=0.75, cal_pct=0.25)

        y_t_o = sp_o.train["target"].to_numpy()
        y_c_o = sp_o.calibrate["target"].to_numpy()
        o_cols = sp_o.feature_columns

        # CQR
        q_m = LightGBMQuantileForecaster(feature_columns=o_cols, quantiles=(0.05, 0.50, 0.95)).fit(
            sp_o.train, y_t_o
        )
        cq_o = q_m.predict_quantiles(sp_o.calibrate)
        cqr_o = ConformalizedQuantileRegression(alpha=alpha).fit(y_c_o, cq_o[0.05], cq_o[0.95])
        tq_o = q_m.predict_quantiles(te_df)
        c_lo_o, c_hi_o = cqr_o.predict_interval(tq_o[0.05], tq_o[0.95])
        b_stat, _ = policy(te_df["age_hours"].to_numpy())

        # Static SAGE
        st_sage = SAGEForecaster(
            config=SAGEConfig(nominal_coverage=0.90, max_disagreement_threshold=8.0),
            feature_columns=o_cols,
        ).fit(sp_o.train, y_t_o, sp_o.calibrate, y_c_o)
        sr_o = st_sage.predict_guarded(te_df)

        # Adaptive SAGE (cal_p95 & fixed tau=8)
        ad_sage_c95 = AdaptiveSAGEForecaster(
            config=AdaptiveSAGEConfig(nominal_coverage=0.90, max_disagreement_threshold="cal_p95"),
            feature_columns=o_cols,
        ).fit(sp_o.train, y_t_o, sp_o.calibrate, y_c_o)
        ar_o = ad_sage_c95.predict_guarded(te_df)

        y_te_o = te_df["target"].to_numpy()
        loso_results[f"station_{held_st}"] = {
            "lightgbm_cqr": _summarize_metrics(y_te_o, tq_o[0.50], c_lo_o, c_hi_o, b_stat != "abstain"),
            "sage_static": _summarize_metrics(
                y_te_o,
                sr_o.point_forecast,
                sr_o.lower_envelope,
                sr_o.upper_envelope,
                sr_o.status != "abstain",
                sr_o.scenario_disagreement,
            ),
            "sage_adaptive_cal95": _summarize_metrics(
                y_te_o,
                ar_o.point_forecast,
                ar_o.lower_envelope,
                ar_o.upper_envelope,
                ar_o.status != "abstain",
                ar_o.scenario_disagreement,
            ),
            "cal_p95_threshold": round(ad_sage_c95.resolved_disagreement_threshold, 4),
        }

    summary = {
        "study_id": f"SG-M74-ADAPTIVE-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
        "dataset_hash": data_hash,
        "seeds": list(DEFAULT_SEEDS),
        "adaptive_diagnostics": {
            "cal_disagreement_p90": round(sage_adap_cal95.calibrated_disagreement_p90, 4),
            "cal_disagreement_p95": round(sage_adap_cal95.calibrated_disagreement_p95, 4),
            "static_constant_disagreement": round(
                float(sage_static.scenario_half_widths[2.0] - sage_static.scenario_half_widths[0.0]), 4
            ),
            "min_scale": round(sage_adap_cal95.min_scale, 4),
            "max_scale": round(sage_adap_cal95.max_scale, 4),
        },
        "chronological_scenarios": chron_results,
        "loso_natural": loso_results,
    }
    return summary


def main() -> int:
    summary = run_adaptive_research_suite()
    out_path = Path("docs/audit/adaptive_sage_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Saved M7.4 adaptive SAGE research results to %s", out_path)
    print(json.dumps(summary["adaptive_diagnostics"], indent=2))
    print("\n--- Natural Scenario ---")
    print(json.dumps(summary["chronological_scenarios"]["natural"], indent=2))
    print("\n--- MNAR (gamma=1.5, 3-seed) ---")
    print(json.dumps(summary["chronological_scenarios"]["mnar_gamma_1.5"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
