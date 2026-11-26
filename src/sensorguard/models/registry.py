"""Explicit serving and shadow model version registry for SensorGuard AI (Phase 8.0).

Maintains an auditable separation between:
- The active primary serving artifact (`sage_pilot_q1_2024`, Static SAGEForecaster v1.0.0)
- Research shadow candidates (`sage_adaptive_q1_2024`, AdaptiveSAGEForecaster) and baselines
  (`locally_weighted_mar`, `lightgbm_cqr`, `persistence`) evaluated in shadow mode.
Prevents Static SAGE from ever being labeled or promoted as Adaptive SAGE without meeting
prespecified verification criteria.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sensorguard.models.manifest import ModelManifest

DEFAULT_REGISTRY_PATH = Path("artifacts/models/model_registry.json")
DEFAULT_PRIMARY_MANIFEST = Path("artifacts/models/manifest.json")

ALLOWED_DATA_MODES = (
    "archived_historical",
    "simulated_outage",
    "shadow_live",
    "validated_live",
)


@dataclass(frozen=True)
class ModelRegistryEntry:
    """Versioned model governance entry distinguishing active serving vs shadow candidates."""

    model_id: str
    version: str
    model_class: str
    algorithm: str
    serving_role: str  # "primary_serving" | "shadow_research" | "shadow_baseline"
    promoted_to_primary: bool
    dataset_hash: str
    nominal_coverage: float
    artifact_path: str | None
    sha256: str | None
    model_created_at: str | None
    artifact_immutable_ref: str | None
    promotion_status_note: str


def build_default_registry(manifest: ModelManifest | None = None) -> dict[str, Any]:
    """Construct canonical registry anchored to the active primary manifest."""
    if manifest is None and DEFAULT_PRIMARY_MANIFEST.exists():
        manifest = ModelManifest.load(DEFAULT_PRIMARY_MANIFEST)

    primary_sha = (
        manifest.sha256 if manifest else "e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115"
    )
    primary_id = manifest.model_id if manifest else "sage_pilot_q1_2024"
    primary_ver = manifest.version if manifest else "1.0.0"
    primary_created_at = manifest.created_at if manifest else "2026-11-25T04:08:50.393371+00:00"
    dataset_hash = manifest.dataset_hash if manifest else "33805c42097fc86e"

    entries = [
        ModelRegistryEntry(
            model_id=primary_id,
            version=primary_ver,
            model_class="SAGEForecaster",
            algorithm="SAGE-STATIC-LIGHTGBM",
            serving_role="primary_serving",
            promoted_to_primary=True,
            dataset_hash=dataset_hash,
            nominal_coverage=0.90,
            artifact_path="artifacts/models/sage_pilot_q1_2024.joblib",
            sha256=primary_sha,
            model_created_at=primary_created_at,
            artifact_immutable_ref=f"{primary_id}@{primary_ver}#{primary_sha[:12]}",
            promotion_status_note=(
                "Active FastAPI serving artifact (Static SAGEForecaster). Uses globally "
                "calibrated sensitivity bounds; D_sensitivity is constant (~3.32 ug/m^3) across "
                "inputs and abstentions are driven by age_hours > 12h freshness rule."
            ),
        ),
        ModelRegistryEntry(
            model_id="sage_adaptive_q1_2024",
            version="2.0.0-shadow-m77",
            model_class="AdaptiveSAGEForecaster",
            algorithm="ADAPTIVE-SAGE-LIGHTGBM",
            serving_role="shadow_research",
            promoted_to_primary=False,
            dataset_hash=dataset_hash,
            nominal_coverage=0.90,
            artifact_path=None,
            sha256=None,
            model_created_at="2026-11-25T16:35:05+00:00",
            artifact_immutable_ref=None,
            promotion_status_note=(
                "Shadow research benchmark summary ONLY (NOT served by primary API; no serialized artifact; "
                "prospective shadow runs serialize immutable per-suite JSON artifacts with byte SHA-256). "
                "M7.7 causal audit (SG-M77-ADAPTIVE-20261125-163505) showed natural Spearman rho=+0.1749 "
                "(<0.20 target) and +0.47 Winkler penalty vs locally_weighted_mar."
            ),
        ),
        ModelRegistryEntry(
            model_id="locally_weighted_mar_q1_2024",
            version="1.0.0-shadow",
            model_class="AdaptiveSAGEForecaster(gamma=0.0)",
            algorithm="LOCALLY-WEIGHTED-MAR",
            serving_role="shadow_baseline",
            promoted_to_primary=False,
            dataset_hash=dataset_hash,
            nominal_coverage=0.90,
            artifact_path=None,
            sha256=None,
            model_created_at="2026-11-25T16:35:05+00:00",
            artifact_immutable_ref=None,
            promotion_status_note=(
                "Shadow research benchmark summary ONLY (unserialized Q1 2024 baseline; prospective runs "
                "serialize immutable per-model JSON artifacts). Achieved best LightGBM-family Winkler in natural (8.72)."
            ),
        ),
        ModelRegistryEntry(
            model_id="lightgbm_cqr_q1_2024",
            version="1.0.0-shadow",
            model_class="ConformalizedQuantileRegression",
            algorithm="LIGHTGBM-CQR",
            serving_role="shadow_baseline",
            promoted_to_primary=False,
            dataset_hash=dataset_hash,
            nominal_coverage=0.90,
            artifact_path=None,
            sha256=None,
            model_created_at="2026-11-25T16:35:05+00:00",
            artifact_immutable_ref=None,
            promotion_status_note="Shadow research benchmark summary ONLY (Romano et al. 2019; prospective runs require serialized JSON artifacts).",
        ),
        ModelRegistryEntry(
            model_id="persistence_split_q1_2024",
            version="1.0.0-shadow",
            model_class="PersistenceForecaster+SplitConformal",
            algorithm="PERSISTENCE-SPLIT",
            serving_role="shadow_baseline",
            promoted_to_primary=False,
            dataset_hash=dataset_hash,
            nominal_coverage=0.90,
            artifact_path=None,
            sha256=None,
            model_created_at="2026-11-25T16:35:05+00:00",
            artifact_immutable_ref=None,
            promotion_status_note="Shadow persistence benchmark summary ONLY (natural Winkler 3.16; prospective runs require serialized JSON artifacts).",
        ),
    ]

    return {
        "schema_version": "1.1",
        "serving_policy": (
            "Primary API exclusively serves Static SAGEForecaster (sage_pilot_q1_2024 v1.0.0). "
            "AdaptiveSAGEForecaster and baselines run strictly in offline/shadow evaluation until "
            "prospective matched-outcome criteria are satisfied and operator promotion is approved."
        ),
        "primary_model_id": primary_id,
        "primary_model_class": "SAGEForecaster",
        "primary_model_created_at": primary_created_at,
        "primary_artifact_immutable_ref": f"{primary_id}@{primary_ver}#{primary_sha[:12]}",
        "adaptive_sage_promoted": False,
        "allowed_data_modes": list(ALLOWED_DATA_MODES),
        "models": [asdict(e) for e in entries],
    }


def load_model_registry(path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    """Load persistent model registry JSON or build canonical default if absent."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return build_default_registry()
