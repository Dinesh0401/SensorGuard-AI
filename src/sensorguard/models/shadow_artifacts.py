"""Trusted, immutable, JSON-only serialization and SHA-256 verification for shadow models (M8.7).

Eliminates pickle/joblib deserialization and static placeholder hashes on prospective shadow
paths by serializing trained models strictly into deterministic canonical JSON payloads
(explicit linear/conformal parameters + LightGBM native text boosters) and verifying the exact
SHA-256 digest over on-disk artifact bytes before prospective inference.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from sensorguard.calibration import ConformalizedQuantileRegression, SplitConformalCalibrator
from sensorguard.models.forecasters import BaseForecaster, PersistenceForecaster
from sensorguard.models.sage import SAGEConfig, SAGEForecaster
from sensorguard.models.sage_adaptive import AdaptiveSAGEConfig, AdaptiveSAGEForecaster

ARTIFACT_FORMAT_VERSION = "sensorguard.shadow_artifact.v1"
SHADOW_CODE_VERSION = "sensorguard-ai/0.1.0-m87"
FEATURE_SCHEMA_VERSION = "spatiotemporal_pm25_v1"


def to_utc_dt(value: Any) -> datetime | None:
    """Convert timestamp/ISO string to timezone-aware UTC datetime."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    elif isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def compute_feature_schema_hash(feature_columns: list[str]) -> str:
    """Compute canonical SHA-256 short digest identifying ordered feature schema."""
    payload = json.dumps(list(feature_columns), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _serialize_linear_pipeline(pipeline: Any) -> dict[str, Any]:
    """Extract numeric parameters from sklearn SimpleImputer -> StandardScaler -> linear clf."""
    steps = getattr(pipeline, "named_steps", {})
    if not steps and hasattr(pipeline, "steps"):
        steps = dict(pipeline.steps)
    imputer = steps.get("simpleimputer") or steps.get("imputer")
    scaler = steps.get("standardscaler") or steps.get("scaler")
    est = (
        steps.get("logisticregression") or steps.get("clf") or steps.get("ridge") or list(steps.values())[-1]
    )
    out: dict[str, Any] = {
        "imputer_statistics": [float(v) for v in imputer.statistics_],
        "scaler_mean": [float(v) for v in scaler.mean_],
        "scaler_scale": [float(v) for v in scaler.scale_],
        "coef": np.asarray(est.coef_, dtype=float).tolist(),
        "intercept": np.asarray(est.intercept_, dtype=float).tolist(),
    }
    if hasattr(est, "classes_"):
        out["classes"] = [int(c) for c in est.classes_]
    return out


class _SerializedLinearPipeline:
    """Pure-NumPy predictor mirroring SimpleImputer -> StandardScaler -> LogisticRegression/Ridge."""

    def __init__(self, params: dict[str, Any], clip_nonnegative: bool = False) -> None:
        self.stats = np.asarray(params["imputer_statistics"], dtype=float)
        self.mean = np.asarray(params["scaler_mean"], dtype=float)
        self.scale = np.asarray(params["scaler_scale"], dtype=float)
        self.safe_scale = np.where(self.scale == 0.0, 1.0, self.scale)
        self.coef = np.asarray(params["coef"], dtype=float)
        self.intercept = np.asarray(params["intercept"], dtype=float)
        self.clip_nonnegative = clip_nonnegative

    def _transform(self, X: pd.DataFrame) -> np.ndarray:
        arr = np.asarray(X, dtype=float).copy()
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        nan_mask = ~np.isfinite(arr)
        if nan_mask.any():
            col_idx = np.where(nan_mask)[1]
            arr[nan_mask] = self.stats[col_idx]
        return (arr - self.mean) / self.safe_scale

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        z = self._transform(X)
        coef_row = self.coef[0] if self.coef.ndim > 1 else self.coef
        inter = float(self.intercept[0]) if np.ndim(self.intercept) > 0 else float(self.intercept)
        logits = np.clip(z @ coef_row + inter, -50.0, 50.0)
        p1 = 1.0 / (1.0 + np.exp(-logits))
        return np.column_stack([1.0 - p1, p1])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        z = self._transform(X)
        inter = float(np.asarray(self.intercept).reshape(-1)[0])
        preds = z @ self.coef.reshape(-1) + inter
        if self.clip_nonnegative:
            return np.clip(preds, 0.0, None)
        return np.asarray(preds, dtype=float)


class _SerializedLightGBMWrapper(BaseForecaster):
    """Safe wrapper over LightGBM Booster loaded strictly from native text model_str."""

    def __init__(
        self,
        model_str: str,
        feature_columns: list[str],
        clip_nonnegative: bool = True,
    ) -> None:
        self.feature_columns = list(feature_columns)
        self.booster = lgb.Booster(model_str=model_str)
        self.clip_nonnegative = clip_nonnegative

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> _SerializedLightGBMWrapper:
        raise RuntimeError("Loaded serialized artifact wrapper is immutable.")

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        arr = X[self.feature_columns] if set(self.feature_columns).issubset(X.columns) else X
        preds = np.asarray(self.booster.predict(arr), dtype=float)
        return np.clip(preds, 0.0, None) if self.clip_nonnegative else preds


class _SerializedQuantileForecaster(BaseForecaster):
    """Safe quantile LightGBM wrapper loaded strictly from serialized quantile model_strs."""

    def __init__(self, feature_columns: list[str], boosters_by_q: dict[float, str]) -> None:
        self.feature_columns = list(feature_columns)
        self.boosters: dict[float, lgb.Booster] = {
            float(q): lgb.Booster(model_str=m_str) for q, m_str in sorted(boosters_by_q.items())
        }

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> _SerializedQuantileForecaster:
        raise RuntimeError("Loaded serialized artifact wrapper is immutable.")

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if 0.50 not in self.boosters:
            raise RuntimeError("Serialized quantile model missing q=0.50 median booster.")
        preds = np.asarray(self.boosters[0.50].predict(X[self.feature_columns]), dtype=float)
        return np.clip(preds, 0.0, None)

    def predict_quantiles(self, X: pd.DataFrame) -> dict[float, np.ndarray]:
        feat_df = X[self.feature_columns]
        return {
            q: np.clip(np.asarray(b.predict(feat_df), dtype=float), 0.0, None)
            for q, b in self.boosters.items()
        }


def _serialize_base_forecaster(base_model: Any, feature_columns: list[str]) -> dict[str, Any]:
    if (
        hasattr(base_model, "model")
        and base_model.model is not None
        and hasattr(base_model.model, "booster_")
    ):
        return {
            "kind": "lightgbm_point",
            "feature_columns": list(feature_columns),
            "params": dict(getattr(base_model, "params", {})),
            "model_str": base_model.model.booster_.model_to_string(),
        }
    if hasattr(base_model, "pipeline") and base_model.pipeline is not None:
        return {
            "kind": "ridge_pipeline",
            "feature_columns": list(feature_columns),
            "alpha": float(getattr(base_model, "alpha", 1.0)),
            "pipeline": _serialize_linear_pipeline(base_model.pipeline),
        }
    raise ValueError(
        f"Unsupported base_model type for trusted JSON serialization: {type(base_model).__name__}"
    )


def _deserialize_base_forecaster(payload: dict[str, Any]) -> Any:
    kind = payload["kind"]
    cols = [str(c) for c in payload["feature_columns"]]
    if kind == "lightgbm_point":
        return _SerializedLightGBMWrapper(
            model_str=str(payload["model_str"]),
            feature_columns=cols,
            clip_nonnegative=True,
        )
    if kind == "ridge_pipeline":
        pipe = _SerializedLinearPipeline(payload["pipeline"], clip_nonnegative=True)
        return pipe
    raise ValueError(f"Unsupported serialized base_model kind: {kind}")


def serialize_shadow_model_artifact(
    model_key: str,
    spec: dict[str, str],
    model_obj: Any,
    feature_columns: list[str],
    nominal_coverage: float,
    model_created_at: datetime,
    training_completed_at: datetime,
    training_data_cutoff: datetime,
    training_data_availability_cutoff: datetime,
    calibration_cutoff: datetime,
    calibration_availability_cutoff: datetime,
    artifact_dir: str | Path,
) -> dict[str, Any]:
    """Serialize one fitted shadow model to an immutable canonical JSON artifact & compute real byte SHA-256."""
    created_utc = to_utc_dt(model_created_at)
    train_done_utc = to_utc_dt(training_completed_at)
    train_data_utc = to_utc_dt(training_data_cutoff)
    train_avail_utc = to_utc_dt(training_data_availability_cutoff)
    cal_data_utc = to_utc_dt(calibration_cutoff)
    cal_avail_utc = to_utc_dt(calibration_availability_cutoff)
    if None in (created_utc, train_done_utc, train_data_utc, train_avail_utc, cal_data_utc, cal_avail_utc):
        raise ValueError("All artifact provenance timestamps must be valid UTC datetimes.")

    # Causal training check: labels must have been available before training completion & model creation
    if train_avail_utc > train_done_utc or cal_avail_utc > train_done_utc:
        raise ValueError(
            "Causal training violation: training/calibration label availability cutoff exceeds training_completed_at."
        )
    if train_avail_utc > created_utc or cal_avail_utc > created_utc or cal_data_utc > created_utc:
        raise ValueError(
            "Causal training violation: training/calibration cutoffs cannot post-date model_created_at."
        )

    if model_key == "persistence":
        p_mod, p_cal = model_obj
        weights_payload: dict[str, Any] = {
            "artifact_kind": "persistence_split",
            "model_class": "PersistenceForecaster+SplitConformal",
            "feature_col": str(p_mod.feature_col),
            "calibrator_alpha": float(p_cal.alpha),
            "calibrator_radius": float(p_cal.radius),
        }
        config_payload = {"feature_col": str(p_mod.feature_col), "alpha": float(p_cal.alpha)}
    elif model_key == "lightgbm_cqr":
        q_mod, cqr = model_obj
        weights_payload = {
            "artifact_kind": "lightgbm_cqr",
            "model_class": "ConformalizedQuantileRegression",
            "feature_columns": [str(c) for c in (q_mod.feature_columns or feature_columns)],
            "boosters": {
                str(float(q)): m.booster_.model_to_string() for q, m in sorted(q_mod.models.items())
            },
            "cqr_alpha": float(cqr.alpha),
            "cqr_q_adjustment": float(cqr.q_adjustment),
        }
        config_payload = {
            "quantiles": [float(q) for q in q_mod.quantiles],
            "params": dict(q_mod.params),
            "alpha": float(cqr.alpha),
        }
    elif model_key == "sage_static":
        sage_m: SAGEForecaster = model_obj
        if not sage_m._is_calibrated:
            raise ValueError("Cannot serialize uncalibrated SAGEForecaster.")
        weights_payload = {
            "artifact_kind": "sage_static",
            "model_class": "SAGEForecaster",
            "feature_columns": [str(c) for c in (sage_m.feature_columns or feature_columns)],
            "propensity_features": [str(c) for c in (sage_m.propensity_features or [])],
            "is_constant_propensity": bool(sage_m._is_constant_propensity),
            "constant_propensity_val": float(sage_m._constant_propensity_val),
            "propensity_pipeline": (
                None
                if sage_m._is_constant_propensity
                else _serialize_linear_pipeline(sage_m.propensity_pipeline)
            ),
            "base_model": _serialize_base_forecaster(
                sage_m.base_model, sage_m.feature_columns or feature_columns
            ),
            "mu_y": float(sage_m.mu_y),
            "sigma_y": float(sage_m.sigma_y),
            "sigma_res": float(sage_m.sigma_res),
            "effective_sample_size": float(sage_m.effective_sample_size),
            "scenario_radii": {str(float(k)): float(v) for k, v in sorted(sage_m.scenario_radii.items())},
            "scenario_bound_shifts": {
                str(float(k)): float(v) for k, v in sorted(sage_m.scenario_bound_shifts.items())
            },
            "scenario_half_widths": {
                str(float(k)): float(v) for k, v in sorted(sage_m.scenario_half_widths.items())
            },
        }
        config_payload = {
            **asdict(sage_m.config),
            "gamma_grid": [float(g) for g in sage_m.config.gamma_grid],
        }
    elif model_key in {"locally_weighted_mar", "sage_adaptive"}:
        adap_m: AdaptiveSAGEForecaster = model_obj
        if not adap_m._is_calibrated or not adap_m._scale_fitted:
            raise ValueError(f"Cannot serialize uncalibrated/unfitted {model_key}.")
        weights_payload = {
            "artifact_kind": "sage_adaptive",
            "model_class": "AdaptiveSAGEForecaster",
            "feature_columns": [str(c) for c in (adap_m.feature_columns or feature_columns)],
            "propensity_features": [str(c) for c in (adap_m.propensity_features or [])],
            "is_constant_propensity": bool(adap_m._is_constant_propensity),
            "constant_propensity_val": float(adap_m._constant_propensity_val),
            "propensity_pipeline": (
                None
                if adap_m._is_constant_propensity
                else _serialize_linear_pipeline(adap_m.propensity_pipeline)
            ),
            "scale_model_str": adap_m.scale_model.booster_.model_to_string(),
            "base_model": _serialize_base_forecaster(
                adap_m.base_model, adap_m.feature_columns or feature_columns
            ),
            "mu_y": float(adap_m.mu_y),
            "sigma_y": float(adap_m.sigma_y),
            "sigma_norm_res": float(adap_m.sigma_norm_res),
            "min_scale": float(adap_m.min_scale),
            "max_scale": float(adap_m.max_scale),
            "effective_sample_size": float(adap_m.effective_sample_size),
            "scenario_norm_quantiles": {
                str(float(k)): float(v) for k, v in sorted(adap_m.scenario_norm_quantiles.items())
            },
            "calibrated_disagreement_p90": float(adap_m.calibrated_disagreement_p90),
            "calibrated_disagreement_p95": float(adap_m.calibrated_disagreement_p95),
            "resolved_disagreement_threshold": float(adap_m.resolved_disagreement_threshold),
            "causal_oof_count": int(adap_m.causal_oof_count),
            "causal_oof_median": float(adap_m.causal_oof_median),
        }
        config_payload = {
            **asdict(adap_m.config),
            "gamma_grid": [float(g) for g in adap_m.config.gamma_grid],
        }
    else:
        raise ValueError(f"Unsupported shadow model_key for artifact serialization: {model_key}")

    artifact_doc = {
        "schema_format": ARTIFACT_FORMAT_VERSION,
        "model_key": model_key,
        "model_id": str(spec["model_id"]),
        "model_version": str(spec["model_version"]),
        "algorithm": str(spec["algorithm"]),
        "code_version": SHADOW_CODE_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema": list(feature_columns),
        "feature_schema_hash": compute_feature_schema_hash(feature_columns),
        "nominal_coverage": float(nominal_coverage),
        "model_created_at": created_utc.isoformat(),
        "training_completed_at": train_done_utc.isoformat(),
        "training_data_cutoff": train_data_utc.isoformat(),
        "training_data_availability_cutoff": train_avail_utc.isoformat(),
        "calibration_cutoff": cal_data_utc.isoformat(),
        "calibration_availability_cutoff": cal_avail_utc.isoformat(),
        "config": config_payload,
        "weights": weights_payload,
    }
    raw_bytes = json.dumps(artifact_doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    artifact_sha256 = hashlib.sha256(raw_bytes).hexdigest().lower()

    out_dir = Path(artifact_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    imm_ref = f"{spec['model_id']}@{spec['model_version']}#{artifact_sha256[:12]}"
    artifact_path = out_dir / f"{model_key}__{spec['model_id']}__{artifact_sha256[:12]}.json"

    if artifact_path.exists():
        existing_bytes = artifact_path.read_bytes()
        existing_sha = hashlib.sha256(existing_bytes).hexdigest().lower()
        if not secrets.compare_digest(existing_sha, artifact_sha256):
            raise FileExistsError(
                f"Refusing to overwrite immutable shadow artifact at '{artifact_path}' (SHA mismatch)."
            )
    else:
        artifact_path.write_bytes(raw_bytes)

    return {
        "model_key": model_key,
        "model_id": str(spec["model_id"]),
        "model_version": str(spec["model_version"]),
        "algorithm": str(spec["algorithm"]),
        "code_version": SHADOW_CODE_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": artifact_doc["feature_schema_hash"],
        "model_created_at": created_utc.isoformat(),
        "training_completed_at": train_done_utc.isoformat(),
        "training_data_cutoff": train_data_utc.isoformat(),
        "training_data_availability_cutoff": train_avail_utc.isoformat(),
        "calibration_cutoff": cal_data_utc.isoformat(),
        "calibration_availability_cutoff": cal_avail_utc.isoformat(),
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha256,
        "artifact_immutable_ref": imm_ref,
        "artifact_bytes": len(raw_bytes),
    }


def verify_and_load_shadow_artifact(
    provenance_entry: dict[str, Any],
    expected_model_key: str,
    expected_spec: dict[str, str],
    expected_feature_columns: list[str],
    expected_nominal_coverage: float,
    evaluation_time: datetime | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Verify SHA-256 over actual file bytes and safely reconstruct predictor using JSON only."""
    if not provenance_entry:
        raise ValueError(f"Missing provenance metadata for shadow model '{expected_model_key}'.")
    if not isinstance(provenance_entry, dict):
        raise TypeError(f"Invalid provenance metadata type for shadow model '{expected_model_key}'.")

    art_path_raw = provenance_entry.get("artifact_path")
    expected_sha = str(provenance_entry.get("artifact_sha256") or "").lower()
    imm_ref = str(provenance_entry.get("artifact_immutable_ref") or "")

    if not art_path_raw or len(expected_sha) != 64:
        raise ValueError(f"Shadow model '{expected_model_key}' missing serialized artifact_path or SHA-256.")
    if "#unpinned" in imm_ref or "#data_" in imm_ref or "#fit_" in imm_ref or not imm_ref:
        raise ValueError(
            f"Shadow model '{expected_model_key}' has unpinned or non-byte reference '{imm_ref}'."
        )

    art_path = Path(str(art_path_raw))
    if not art_path.exists() or not art_path.is_file():
        raise ValueError(f"Serialized shadow artifact missing at '{art_path}' for '{expected_model_key}'.")
    if art_path.suffix.lower() != ".json":
        raise ValueError(
            f"Refusing untrusted non-JSON artifact '{art_path}' (no pickle/joblib allowed on shadow path)."
        )

    raw_bytes = art_path.read_bytes()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if not secrets.compare_digest(actual_sha, expected_sha):
        raise ValueError(
            f"SHA-256 verification failed for '{expected_model_key}': actual {actual_sha} != {expected_sha}."
        )

    expected_ref = f"{expected_spec['model_id']}@{expected_spec['model_version']}#{actual_sha[:12]}"
    if not secrets.compare_digest(imm_ref, expected_ref):
        raise ValueError(
            f"Artifact reference mismatch for '{expected_model_key}': '{imm_ref}' != expected '{expected_ref}'."
        )

    doc = json.loads(raw_bytes.decode("utf-8"))
    if doc.get("schema_format") != ARTIFACT_FORMAT_VERSION:
        raise ValueError(f"Unsupported shadow artifact format '{doc.get('schema_format')}'.")
    if doc.get("model_key") != expected_model_key or doc.get("model_id") != expected_spec["model_id"]:
        raise ValueError(
            f"Artifact identity mismatch: file claims ({doc.get('model_key')}, {doc.get('model_id')}), "
            f"expected ({expected_model_key}, {expected_spec['model_id']})."
        )
    if doc.get("model_version") != expected_spec["model_version"]:
        raise ValueError(f"Artifact version mismatch for '{expected_model_key}'.")
    if doc.get("feature_schema") != list(expected_feature_columns):
        raise ValueError(
            f"Incompatible feature schema in artifact '{expected_model_key}': "
            f"{doc.get('feature_schema')} != {list(expected_feature_columns)}."
        )
    if abs(float(doc.get("nominal_coverage", -1.0)) - float(expected_nominal_coverage)) > 1e-6:
        raise ValueError(f"Incompatible nominal_coverage in artifact '{expected_model_key}'.")

    # Verify causal training & availability timestamps
    created_utc = to_utc_dt(doc.get("model_created_at"))
    train_done_utc = to_utc_dt(doc.get("training_completed_at"))
    train_data_utc = to_utc_dt(doc.get("training_data_cutoff"))
    train_avail_utc = to_utc_dt(doc.get("training_data_availability_cutoff"))
    cal_data_utc = to_utc_dt(doc.get("calibration_cutoff"))
    cal_avail_utc = to_utc_dt(doc.get("calibration_availability_cutoff"))
    if None in (created_utc, train_done_utc, train_data_utc, train_avail_utc, cal_data_utc, cal_avail_utc):
        raise ValueError(f"Incomplete timestamp lineage in artifact '{expected_model_key}'.")
    if train_avail_utc > train_done_utc or cal_avail_utc > train_done_utc:
        raise ValueError(
            f"Artifact '{expected_model_key}' failed causal availability <= training_completed_at."
        )
    if train_avail_utc > created_utc or cal_avail_utc > created_utc or cal_data_utc > created_utc:
        raise ValueError(f"Artifact '{expected_model_key}' failed causal cutoffs <= model_created_at.")
    eval_utc = to_utc_dt(evaluation_time)
    if eval_utc is not None and train_done_utc > eval_utc:
        raise ValueError(
            f"Artifact '{expected_model_key}' training_completed_at {train_done_utc.isoformat()} "
            f"exceeds generation time {eval_utc.isoformat()}."
        )

    weights = doc["weights"]
    cfg = doc["config"]
    kind = weights["artifact_kind"]

    loaded_obj: Any
    if kind == "persistence_split":
        p_mod = PersistenceForecaster(feature_col=str(weights["feature_col"]))
        p_cal = SplitConformalCalibrator(alpha=float(weights["calibrator_alpha"]))
        p_cal.radius = float(weights["calibrator_radius"])
        loaded_obj = (p_mod, p_cal)
    elif kind == "lightgbm_cqr":
        q_mod = _SerializedQuantileForecaster(
            feature_columns=[str(c) for c in weights["feature_columns"]],
            boosters_by_q={float(k): str(v) for k, v in weights["boosters"].items()},
        )
        cqr = ConformalizedQuantileRegression(alpha=float(weights["cqr_alpha"]))
        cqr.q_adjustment = float(weights["cqr_q_adjustment"])
        loaded_obj = (q_mod, cqr)
    elif kind == "sage_static":
        s_cfg = SAGEConfig(
            gamma_grid=tuple(float(g) for g in cfg["gamma_grid"]),
            nominal_coverage=float(cfg["nominal_coverage"]),
            max_disagreement_threshold=float(cfg["max_disagreement_threshold"]),
            min_effective_sample_size=int(cfg["min_effective_sample_size"]),
            base_model_type=str(cfg["base_model_type"]),
            stale_age_hours=float(cfg["stale_age_hours"]),
            caution_age_hours=float(cfg["caution_age_hours"]),
            caution_disagreement_ratio=float(cfg["caution_disagreement_ratio"]),
            max_envelope_width_threshold=float(cfg["max_envelope_width_threshold"]),
        )
        sage = SAGEForecaster(
            config=s_cfg,
            base_model=_deserialize_base_forecaster(weights["base_model"]),
            feature_columns=[str(c) for c in weights["feature_columns"]],
            propensity_features=[str(c) for c in weights["propensity_features"]],
        )
        sage._is_constant_propensity = bool(weights["is_constant_propensity"])
        sage._constant_propensity_val = float(weights["constant_propensity_val"])
        if not sage._is_constant_propensity and weights["propensity_pipeline"] is not None:
            sage.propensity_pipeline = _SerializedLinearPipeline(weights["propensity_pipeline"])
        sage.mu_y = float(weights["mu_y"])
        sage.sigma_y = float(weights["sigma_y"])
        sage.sigma_res = float(weights["sigma_res"])
        sage.effective_sample_size = float(weights["effective_sample_size"])
        sage.scenario_radii = {float(k): float(v) for k, v in weights["scenario_radii"].items()}
        sage.scenario_bound_shifts = {float(k): float(v) for k, v in weights["scenario_bound_shifts"].items()}
        sage.scenario_half_widths = {float(k): float(v) for k, v in weights["scenario_half_widths"].items()}
        sage._is_calibrated = True
        loaded_obj = sage
    elif kind == "sage_adaptive":
        max_dis = cfg["max_disagreement_threshold"]
        if not isinstance(max_dis, str):
            max_dis = float(max_dis)
        a_cfg = AdaptiveSAGEConfig(
            gamma_grid=tuple(float(g) for g in cfg["gamma_grid"]),
            nominal_coverage=float(cfg["nominal_coverage"]),
            max_disagreement_threshold=max_dis,
            min_effective_sample_size=int(cfg["min_effective_sample_size"]),
            base_model_type=str(cfg["base_model_type"]),
            stale_age_hours=float(cfg["stale_age_hours"]),
            caution_age_hours=float(cfg["caution_age_hours"]),
            caution_disagreement_ratio=float(cfg["caution_disagreement_ratio"]),
            max_envelope_width_threshold=float(cfg["max_envelope_width_threshold"]),
            propensity_risk_weight=float(cfg["propensity_risk_weight"]),
            min_local_scale=float(cfg["min_local_scale"]),
            n_oof_folds=int(cfg["n_oof_folds"]),
            horizon_hours=float(cfg["horizon_hours"]),
            embargo_hours=float(cfg["embargo_hours"]),
            require_timestamps=bool(cfg["require_timestamps"]),
            timestamp_col=str(cfg["timestamp_col"]),
            random_state=int(cfg["random_state"]),
        )
        adap = AdaptiveSAGEForecaster(
            config=a_cfg,
            feature_columns=[str(c) for c in weights["feature_columns"]],
        )
        adap.propensity_features = [str(c) for c in weights["propensity_features"]]
        adap._is_constant_propensity = bool(weights["is_constant_propensity"])
        adap._constant_propensity_val = float(weights["constant_propensity_val"])
        if not adap._is_constant_propensity and weights["propensity_pipeline"] is not None:
            adap.propensity_pipeline = _SerializedLinearPipeline(weights["propensity_pipeline"])
        adap.scale_model = _SerializedLightGBMWrapper(
            model_str=str(weights["scale_model_str"]),
            feature_columns=[str(c) for c in weights["feature_columns"]],
            clip_nonnegative=False,
        )
        adap._scale_fitted = True
        adap.base_model = _deserialize_base_forecaster(weights["base_model"])
        adap.mu_y = float(weights["mu_y"])
        adap.sigma_y = float(weights["sigma_y"])
        adap.sigma_norm_res = float(weights["sigma_norm_res"])
        adap.min_scale = float(weights["min_scale"])
        adap.max_scale = float(weights["max_scale"])
        adap.effective_sample_size = float(weights["effective_sample_size"])
        adap.scenario_norm_quantiles = {
            float(k): float(v) for k, v in weights["scenario_norm_quantiles"].items()
        }
        adap.calibrated_disagreement_p90 = float(weights["calibrated_disagreement_p90"])
        adap.calibrated_disagreement_p95 = float(weights["calibrated_disagreement_p95"])
        adap.resolved_disagreement_threshold = float(weights["resolved_disagreement_threshold"])
        adap.causal_oof_count = int(weights["causal_oof_count"])
        adap.causal_oof_median = float(weights["causal_oof_median"])
        adap._is_calibrated = True
        loaded_obj = adap
    else:
        raise ValueError(f"Unsupported serialized shadow artifact kind: {kind}")

    verified_meta = {
        "model_key": expected_model_key,
        "model_id": str(doc["model_id"]),
        "model_version": str(doc["model_version"]),
        "algorithm": str(doc["algorithm"]),
        "code_version": str(doc["code_version"]),
        "feature_schema_version": str(doc["feature_schema_version"]),
        "feature_schema_hash": str(doc["feature_schema_hash"]),
        "model_created_at": created_utc,
        "training_completed_at": train_done_utc,
        "training_data_cutoff": train_data_utc,
        "training_data_availability_cutoff": train_avail_utc,
        "calibration_cutoff": cal_data_utc,
        "calibration_availability_cutoff": cal_avail_utc,
        "artifact_path": str(art_path),
        "artifact_sha256": actual_sha,
        "artifact_immutable_ref": expected_ref,
    }
    return loaded_obj, verified_meta
