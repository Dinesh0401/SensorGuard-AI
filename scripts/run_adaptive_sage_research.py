"""Run rigorous M7.4/M7.7 Adaptive Input-Dependent SAGE comparison & 8-fold LOSO evaluation.

Evaluates strictly causal timestamp-split adaptive SAGE policies against static SAGE,
Persistence, Split Conformal, CQR, Rolling Conformal, ACI, Locally-Weighted MAR (gamma=0),
and Observation-Gated Masked ACI across chronological test regimes (including extended 72h
multi-station outage), 3 random outage seeds ([11, 42, 107]), paired identical masks /
matched abstention budgets with time-block bootstrap 95% CIs, and 8-fold Leave-One-Station-Out CV.
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


def _per_sample_arrays(
    y_true: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    alpha: float = 0.10,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return per-sample binary coverage, interval width, and Winkler score."""
    cov = ((y_true >= lower) & (y_true <= upper)).astype(float)
    width = upper - lower
    penalty = (2.0 / alpha) * np.where(
        y_true < lower,
        lower - y_true,
        np.where(y_true > upper, y_true - upper, 0.0),
    )
    winkler = width + penalty
    return cov, width, winkler


def _block_bootstrap_delta_ci(
    df_meta: pd.DataFrame,
    y_true: np.ndarray,
    lo_a: np.ndarray,
    hi_a: np.ndarray,
    pub_a: np.ndarray,
    lo_b: np.ndarray,
    hi_b: np.ndarray,
    pub_b: np.ndarray,
    mask: np.ndarray,
    alpha: float = 0.10,
    n_boot: int = 800,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute paired (Model A - Model B) deltas with station-day block bootstrap 95% CIs."""
    valid = (
        mask
        & np.isfinite(y_true)
        & np.isfinite(lo_a)
        & np.isfinite(hi_a)
        & np.isfinite(lo_b)
        & np.isfinite(hi_b)
    )
    n_pairs = int(valid.sum())
    if n_pairs == 0:
        return {"n_pairs": 0}

    cov_a, wid_a, wink_a = _per_sample_arrays(y_true[valid], lo_a[valid], hi_a[valid], alpha=alpha)
    cov_b, wid_b, wink_b = _per_sample_arrays(y_true[valid], lo_b[valid], hi_b[valid], alpha=alpha)
    d_cov = cov_a - cov_b
    d_wid = wid_a - wid_b
    d_wink = wink_a - wink_b

    # Station-day blocks preserve autocorrelation & cross-station clustering
    sub_meta = df_meta.iloc[valid]
    ts_day = pd.to_datetime(sub_meta["timestamp"], utc=True).dt.strftime("%Y-%m-%d")
    block_keys = (sub_meta["station_id"].astype(str) + "_" + ts_day).to_numpy()
    unique_blocks, inverse = np.unique(block_keys, return_inverse=True)
    n_blocks = len(unique_blocks)

    block_sums_cov = np.bincount(inverse, weights=d_cov, minlength=n_blocks)
    block_sums_wid = np.bincount(inverse, weights=d_wid, minlength=n_blocks)
    block_sums_wink = np.bincount(inverse, weights=d_wink, minlength=n_blocks)
    block_counts = np.bincount(inverse, minlength=n_blocks).astype(float)

    rng = np.random.default_rng(seed)
    idx_mat = rng.integers(0, n_blocks, size=(n_boot, n_blocks))
    den = np.maximum(block_counts[idx_mat].sum(axis=1), 1.0)
    boot_cov = block_sums_cov[idx_mat].sum(axis=1) / den
    boot_wid = block_sums_wid[idx_mat].sum(axis=1) / den
    boot_wink = block_sums_wink[idx_mat].sum(axis=1) / den

    return {
        "n_pairs": n_pairs,
        "n_station_day_blocks": int(n_blocks),
        "a_picp": round(float(np.mean(cov_a)), 4),
        "b_picp": round(float(np.mean(cov_b)), 4),
        "a_mpiw": round(float(np.mean(wid_a)), 4),
        "b_mpiw": round(float(np.mean(wid_b)), 4),
        "a_winkler": round(float(np.mean(wink_a)), 4),
        "b_winkler": round(float(np.mean(wink_b)), 4),
        "delta_picp": round(float(np.mean(d_cov)), 4),
        "delta_picp_ci95": [
            round(float(np.quantile(boot_cov, 0.025)), 4),
            round(float(np.quantile(boot_cov, 0.975)), 4),
        ],
        "delta_mpiw": round(float(np.mean(d_wid)), 4),
        "delta_mpiw_ci95": [
            round(float(np.quantile(boot_wid, 0.025)), 4),
            round(float(np.quantile(boot_wid, 0.975)), 4),
        ],
        "delta_winkler": round(float(np.mean(d_wink)), 4),
        "delta_winkler_ci95": [
            round(float(np.quantile(boot_wink, 0.025)), 4),
            round(float(np.quantile(boot_wink, 0.975)), 4),
        ],
    }


def _match_abstention_mask(
    base_pub: np.ndarray,
    target_pub: np.ndarray,
    uncertainty_score: np.ndarray,
    y_true: np.ndarray,
) -> np.ndarray:
    """Abstain the exact additional count from base_pub to match target_pub's published sample count."""
    valid = np.isfinite(y_true)
    target_pub_valid = int((target_pub & valid).sum())
    base_pub_valid = int((base_pub & valid).sum())
    if base_pub_valid <= target_pub_valid:
        return base_pub.copy()
    extra_drop = base_pub_valid - target_pub_valid
    candidate_idx = np.flatnonzero(base_pub & valid)
    ranked_desc = candidate_idx[np.argsort(-uncertainty_score[candidate_idx], kind="mergesort")]
    out_mask = base_pub.copy()
    out_mask[ranked_desc[:extra_drop]] = False
    return out_mask


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

    # 1. Fit shared baselines once cleanly on identical chronological splits
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

    # Locally-Weighted MAR Conformal (gamma=0.0 adaptive baseline isolating causal OOF s(x) without MNAR grid)
    mar_local = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            gamma_grid=(0.0,),
            nominal_coverage=0.90,
            max_disagreement_threshold=9999.0,
            propensity_risk_weight=0.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_train_df, y_tr_unfiltered, cal_df, y_cal)

    # Matched-coverage MAR (alpha calibrated strictly on X_cal to probe equal-coverage sharpness)
    mar_local_80 = AdaptiveSAGEForecaster(
        config=AdaptiveSAGEConfig(
            gamma_grid=(0.0,),
            nominal_coverage=0.80,
            max_disagreement_threshold=9999.0,
            propensity_risk_weight=0.0,
        ),
        feature_columns=cols,
    ).fit(unfiltered_train_df, y_tr_unfiltered, cal_df, y_cal)

    # Adaptive SAGE M7.7 causal timestamp-split candidate under 3 pre-specified policies
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

    scenarios = ["natural", "mcar_20", "mnar_gamma_1.5", "burst_24h", "extended_72h"]
    chron_results: dict[str, dict[str, Any]] = {}
    paired_chronological: dict[str, Any] = {}

    for scen in scenarios:
        seeds = [42] if scen in {"natural", "burst_24h", "extended_72h"} else list(DEFAULT_SEEDS)
        per_seed: dict[str, list[dict[str, Any]]] = {}

        # Track seed=42 representative arrays for exact paired mask & block bootstrap comparison
        rep_seed = 42
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

            # Standard ACI
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

            # Locally-Weighted MAR calibrated at 80% nominal (for sharpness calibration contrast)
            mr80 = mar_local_80.predict_guarded(scen_df)
            per_seed.setdefault("locally_weighted_mar_80", []).append(
                _summarize_metrics(
                    y_eval,
                    mr80.point_forecast,
                    mr80.lower_envelope,
                    mr80.upper_envelope,
                    mr80.status != "abstain",
                    mr80.scenario_disagreement,
                )
            )

            # Adaptive SAGE variants
            adap_res_map: dict[str, Any] = {}
            for label_a, m_obj in [
                ("sage_adaptive_tau8", sage_adap_tau8),
                ("sage_adaptive_cal95", sage_adap_cal95),
                ("sage_adaptive_no_abstain", sage_adap_no_abstain),
            ]:
                ar = m_obj.predict_guarded(scen_df)
                adap_res_map[label_a] = ar
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

            if seed == rep_seed:
                ar_tau8 = adap_res_map["sage_adaptive_tau8"]
                ar_cal95 = adap_res_map["sage_adaptive_cal95"]
                pub_tau8 = ar_tau8.status != "abstain"
                pub_cal95 = ar_cal95.status != "abstain"
                pub_mar = mr.status != "abstain"
                all_valid_mask = np.isfinite(y_eval)

                # Matched abstention budget for baselines (drop top spread equal to cal95 extra abstentions)
                mar_budget_pub = _match_abstention_mask(
                    pub_mar, pub_cal95, mr.upper_envelope - mr.lower_envelope, y_eval
                )
                cqr_budget_pub = _match_abstention_mask(base_pub, pub_cal95, c_hi - c_lo, y_eval)
                aci_budget_pub = _match_abstention_mask(base_pub, pub_cal95, a_hi - a_lo, y_eval)

                paired_chronological[scen] = {
                    # Isolate pure local scale s(x) benefit (gamma=0 MAR vs Static LightGBM Split)
                    "scale_isolation_mar_vs_lgb_split_all_valid": _block_bootstrap_delta_ci(
                        scen_df,
                        y_eval,
                        mr.lower_envelope,
                        mr.upper_envelope,
                        pub_mar,
                        s_lo,
                        s_hi,
                        base_pub,
                        all_valid_mask,
                    ),
                    # Isolate pure MNAR gamma-grid shift under identical published mask (tau8 vs gamma=0 MAR)
                    "grid_isolation_tau8_vs_mar_common_published": _block_bootstrap_delta_ci(
                        scen_df,
                        y_eval,
                        ar_tau8.lower_envelope,
                        ar_tau8.upper_envelope,
                        pub_tau8,
                        mr.lower_envelope,
                        mr.upper_envelope,
                        pub_mar,
                        pub_tau8 & pub_mar,
                    ),
                    # Adaptive cal95 vs Static SAGE (common published mask)
                    "adaptive_cal95_vs_static_sage_common_published": _block_bootstrap_delta_ci(
                        scen_df,
                        y_eval,
                        ar_cal95.lower_envelope,
                        ar_cal95.upper_envelope,
                        pub_cal95,
                        sr.lower_envelope,
                        sr.upper_envelope,
                        sr.status != "abstain",
                        pub_cal95 & (sr.status != "abstain"),
                    ),
                    # Adaptive cal95 vs CQR under matched abstention budget mask intersection
                    "adaptive_cal95_vs_cqr_matched_budget": {
                        "cal95_n_pub": int((pub_cal95 & all_valid_mask).sum()),
                        "cqr_budget_n_pub": int((cqr_budget_pub & all_valid_mask).sum()),
                        "metrics_on_cal95_published": _block_bootstrap_delta_ci(
                            scen_df,
                            y_eval,
                            ar_cal95.lower_envelope,
                            ar_cal95.upper_envelope,
                            pub_cal95,
                            c_lo,
                            c_hi,
                            cqr_budget_pub,
                            pub_cal95,
                        ),
                    },
                    # Adaptive cal95 vs Locally-Weighted MAR under matched abstention budget
                    "adaptive_cal95_vs_mar_matched_budget": {
                        "cal95_n_pub": int((pub_cal95 & all_valid_mask).sum()),
                        "mar_budget_n_pub": int((mar_budget_pub & all_valid_mask).sum()),
                        "metrics_on_common_published": _block_bootstrap_delta_ci(
                            scen_df,
                            y_eval,
                            ar_cal95.lower_envelope,
                            ar_cal95.upper_envelope,
                            pub_cal95,
                            mr.lower_envelope,
                            mr.upper_envelope,
                            mar_budget_pub,
                            pub_cal95 & mar_budget_pub,
                        ),
                    },
                    # Adaptive cal95 vs Persistence & ACI on identical cal95 published subset
                    "adaptive_cal95_vs_persistence_on_cal95_pub": _block_bootstrap_delta_ci(
                        scen_df,
                        y_eval,
                        ar_cal95.lower_envelope,
                        ar_cal95.upper_envelope,
                        pub_cal95,
                        p_lo,
                        p_hi,
                        base_pub,
                        pub_cal95 & base_pub,
                    ),
                    "adaptive_cal95_vs_aci_matched_budget": _block_bootstrap_delta_ci(
                        scen_df,
                        y_eval,
                        ar_cal95.lower_envelope,
                        ar_cal95.upper_envelope,
                        pub_cal95,
                        a_lo,
                        a_hi,
                        aci_budget_pub,
                        pub_cal95 & aci_budget_pub,
                    ),
                }

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
                agg["n_valid"] = seed_list[0]["n_valid"]
                agg["n_published"] = seed_list[0]["n_published"]
            scen_agg[m_key] = agg
        chron_results[scen] = scen_agg

    # 2. 8-Fold Leave-One-Station-Out (LOSO) Paired Station Generalization
    loso_stations = sorted(featured_df["station_id"].unique(), key=int)
    loso_results: dict[str, Any] = {}
    loso_station_deltas_tau8_vs_mar: list[dict[str, float]] = []
    loso_station_deltas_cal95_vs_cqr: list[dict[str, float]] = []

    for held_st in loso_stations:
        other_sts = [s for s in loso_stations if s != held_st]
        other_coords = {k: v for k, v in station_coords.items() if k in other_sts}
        tr_cal_df = featured_df[featured_df["station_id"].isin(other_sts)].copy()
        te_df = (
            featured_df[featured_df["station_id"] == held_st].sort_values("timestamp").reset_index(drop=True)
        )
        sp_o = partition_dataset(tr_cal_df, other_coords, train_pct=0.75, cal_pct=0.25)

        y_t_o = sp_o.train["target"].to_numpy()
        y_c_o = sp_o.calibrate["target"].to_numpy()
        o_cols = sp_o.feature_columns

        # Unfiltered pre-cal training window on training stations
        cal_start_o = sp_o.calibrate["timestamp"].min()
        unf_tr_o = tr_cal_df[tr_cal_df["timestamp"] < cal_start_o].copy()
        y_unf_tr_o = unf_tr_o["target"].to_numpy()

        # Persistence
        p_o = PersistenceForecaster().fit(sp_o.train, y_t_o)
        p_cal_o = SplitConformalCalibrator(alpha=alpha).fit(y_c_o, p_o.predict(sp_o.calibrate))
        p_pred_o = p_o.predict(te_df)
        p_lo_o, p_hi_o = p_cal_o.predict_interval(p_pred_o)

        # LightGBM point + ACI
        lgb_o = LightGBMPointForecaster(feature_columns=o_cols).fit(sp_o.train, y_t_o)
        if hasattr(lgb_o.model, "set_params"):
            lgb_o.model.set_params(n_jobs=1)
        cal_res_o = np.abs(y_c_o - lgb_o.predict(sp_o.calibrate))
        te_pred_o = lgb_o.predict(te_df)
        te_rep_o = te_df["reported"].fillna(False).to_numpy(dtype=bool)
        y_te_o = te_df["target"].to_numpy()

        aci_o = AdaptiveConformalInference(target_alpha=alpha, gamma=0.01)
        a_lo_o = np.zeros(len(te_df))
        a_hi_o = np.zeros(len(te_df))
        for idx_o in range(len(te_df)):
            sc_o = float(np.quantile(cal_res_o, 1.0 - aci_o.current_alpha)) if cal_res_o.size > 0 else 5.0
            a_lo_o[idx_o] = max(0.0, float(te_pred_o[idx_o]) - sc_o)
            a_hi_o[idx_o] = float(te_pred_o[idx_o]) + sc_o
            if te_rep_o[idx_o] and np.isfinite(y_te_o[idx_o]):
                aci_o.step(bool(a_lo_o[idx_o] <= y_te_o[idx_o] <= a_hi_o[idx_o]))

        # CQR
        q_m = LightGBMQuantileForecaster(feature_columns=o_cols, quantiles=(0.05, 0.50, 0.95)).fit(
            sp_o.train, y_t_o
        )
        cq_o = q_m.predict_quantiles(sp_o.calibrate)
        cqr_o = ConformalizedQuantileRegression(alpha=alpha).fit(y_c_o, cq_o[0.05], cq_o[0.95])
        tq_o = q_m.predict_quantiles(te_df)
        c_lo_o, c_hi_o = cqr_o.predict_interval(tq_o[0.05], tq_o[0.95])
        b_stat, _ = policy(te_df["age_hours"].to_numpy())
        b_pub_o = b_stat != "abstain"

        # Static SAGE
        st_sage = SAGEForecaster(
            config=SAGEConfig(nominal_coverage=0.90, max_disagreement_threshold=8.0),
            feature_columns=o_cols,
        ).fit(sp_o.train, y_t_o, sp_o.calibrate, y_c_o)
        sr_o = st_sage.predict_guarded(te_df)

        # Locally-Weighted MAR (gamma=0 adaptive control)
        mar_o = AdaptiveSAGEForecaster(
            config=AdaptiveSAGEConfig(
                gamma_grid=(0.0,),
                nominal_coverage=0.90,
                max_disagreement_threshold=9999.0,
                propensity_risk_weight=0.0,
            ),
            feature_columns=o_cols,
        ).fit(unf_tr_o, y_unf_tr_o, sp_o.calibrate, y_c_o)
        mr_o = mar_o.predict_guarded(te_df)

        # Adaptive SAGE tau=8 and cal_p95
        ad_sage_t8 = AdaptiveSAGEForecaster(
            config=AdaptiveSAGEConfig(nominal_coverage=0.90, max_disagreement_threshold=8.0),
            feature_columns=o_cols,
        ).fit(unf_tr_o, y_unf_tr_o, sp_o.calibrate, y_c_o)
        at8_o = ad_sage_t8.predict_guarded(te_df)

        ad_sage_c95 = AdaptiveSAGEForecaster(
            config=AdaptiveSAGEConfig(nominal_coverage=0.90, max_disagreement_threshold="cal_p95"),
            feature_columns=o_cols,
        ).fit(unf_tr_o, y_unf_tr_o, sp_o.calibrate, y_c_o)
        ar_o = ad_sage_c95.predict_guarded(te_df)

        tau8_vs_mar = _block_bootstrap_delta_ci(
            te_df,
            y_te_o,
            at8_o.lower_envelope,
            at8_o.upper_envelope,
            at8_o.status != "abstain",
            mr_o.lower_envelope,
            mr_o.upper_envelope,
            mr_o.status != "abstain",
            (at8_o.status != "abstain") & (mr_o.status != "abstain"),
        )
        cal95_vs_cqr = _block_bootstrap_delta_ci(
            te_df,
            y_te_o,
            ar_o.lower_envelope,
            ar_o.upper_envelope,
            ar_o.status != "abstain",
            c_lo_o,
            c_hi_o,
            b_pub_o,
            (ar_o.status != "abstain") & b_pub_o,
        )
        loso_station_deltas_tau8_vs_mar.append(tau8_vs_mar)
        loso_station_deltas_cal95_vs_cqr.append(cal95_vs_cqr)

        loso_results[f"station_{held_st}"] = {
            "persistence": _summarize_metrics(y_te_o, p_pred_o, p_lo_o, p_hi_o, b_pub_o),
            "lightgbm_aci": _summarize_metrics(y_te_o, te_pred_o, a_lo_o, a_hi_o, b_pub_o),
            "lightgbm_cqr": _summarize_metrics(y_te_o, tq_o[0.50], c_lo_o, c_hi_o, b_pub_o),
            "sage_static": _summarize_metrics(
                y_te_o,
                sr_o.point_forecast,
                sr_o.lower_envelope,
                sr_o.upper_envelope,
                sr_o.status != "abstain",
                sr_o.scenario_disagreement,
            ),
            "locally_weighted_mar": _summarize_metrics(
                y_te_o,
                mr_o.point_forecast,
                mr_o.lower_envelope,
                mr_o.upper_envelope,
                mr_o.status != "abstain",
                mr_o.scenario_disagreement,
            ),
            "sage_adaptive_tau8": _summarize_metrics(
                y_te_o,
                at8_o.point_forecast,
                at8_o.lower_envelope,
                at8_o.upper_envelope,
                at8_o.status != "abstain",
                at8_o.scenario_disagreement,
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
            "paired_tau8_vs_mar_common_pub": tau8_vs_mar,
            "paired_cal95_vs_cqr_common_pub": cal95_vs_cqr,
        }

    # Aggregate across all 8 held-out stations (mean & range across stations)
    def _agg_loso_metric(model_key: str, field: str) -> float:
        return round(
            float(np.mean([loso_results[f"station_{s}"][model_key][field] for s in loso_stations])), 4
        )

    loso_summary = {
        "n_stations": len(loso_stations),
        "station_mean_metrics": {
            m: {
                "picp": _agg_loso_metric(m, "picp"),
                "published_picp": _agg_loso_metric(m, "published_picp"),
                "mpiw": _agg_loso_metric(m, "mpiw"),
                "published_mpiw": _agg_loso_metric(m, "published_mpiw"),
                "winkler": _agg_loso_metric(m, "winkler"),
                "abstention_rate": _agg_loso_metric(m, "abstention_rate"),
            }
            for m in [
                "persistence",
                "lightgbm_aci",
                "lightgbm_cqr",
                "sage_static",
                "locally_weighted_mar",
                "sage_adaptive_tau8",
                "sage_adaptive_cal95",
            ]
        },
        "mean_paired_tau8_minus_mar_winkler": round(
            float(np.mean([d["delta_winkler"] for d in loso_station_deltas_tau8_vs_mar])), 4
        ),
        "mean_paired_cal95_minus_cqr_winkler": round(
            float(np.mean([d["delta_winkler"] for d in loso_station_deltas_cal95_vs_cqr])), 4
        ),
    }

    summary = {
        "study_id": f"SG-M77-ADAPTIVE-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
        "dataset_hash": data_hash,
        "seeds": list(DEFAULT_SEEDS),
        "causal_split_audit": {
            "horizon_hours": sage_adap_cal95.config.horizon_hours,
            "embargo_hours": sage_adap_cal95.config.embargo_hours,
            "unique_timestamp_boundary_enforced": True,
            "folds": sage_adap_cal95.causal_split_audit_,
        },
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
        "paired_comparisons": paired_chronological,
        "loso_natural": loso_results,
        "loso_summary": loso_summary,
    }
    return summary


def main() -> int:
    summary = run_adaptive_research_suite()
    out_path = Path("docs/audit/adaptive_sage_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info(
        "Saved M7.7 adaptive SAGE research results to %s (study_id=%s)", out_path, summary["study_id"]
    )
    print(json.dumps(summary["adaptive_diagnostics"], indent=2))
    print("\n--- Causal Split Audit ---")
    print(json.dumps(summary["causal_split_audit"], indent=2))
    print("\n--- Natural Scenario (key models) ---")
    for k in [
        "persistence",
        "lightgbm_aci",
        "lightgbm_cqr",
        "sage_static",
        "locally_weighted_mar",
        "sage_adaptive_tau8",
        "sage_adaptive_cal95",
    ]:
        print(k, "=>", json.dumps(summary["chronological_scenarios"]["natural"][k]))
    print("\n--- MNAR (gamma=1.5, 3-seed) ---")
    for k in [
        "persistence",
        "lightgbm_aci",
        "lightgbm_cqr",
        "sage_static",
        "locally_weighted_mar",
        "sage_adaptive_tau8",
        "sage_adaptive_cal95",
    ]:
        print(k, "=>", json.dumps(summary["chronological_scenarios"]["mnar_gamma_1.5"][k]))
    print("\n--- LOSO 8-Station Summary ---")
    print(json.dumps(summary["loso_summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
