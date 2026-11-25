"""Offline training script for SensorGuard AI SAGE pilot forecaster.

Trains the SAGE model offline on Q1 2024 London AURN pilot observations,
computes its SHA-256 cryptographic digest, saves artifacts/models/sage_pilot_q1_2024.joblib,
and writes artifacts/models/manifest.json.

Enforces zero-lookahead, explicit train/calibration splits, and verifiable model provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib

from sensorguard.db.session import create_db_engine, get_session_factory
from sensorguard.models.data import (
    build_spatiotemporal_features,
    compute_dataset_content_hash,
    load_pilot_dataset_from_db,
    partition_dataset,
)
from sensorguard.models.manifest import ModelManifest
from sensorguard.models.sage import SAGEConfig, SAGEForecaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def train_offline_pilot_model(
    output_dir: Path = Path("artifacts/models"),
    nominal_coverage: float = 0.90,
) -> tuple[Path, Path, ModelManifest]:
    """Train SAGE model offline, compute SHA-256 integrity digest, and write manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "sage_pilot_q1_2024.joblib"
    sha_path = output_dir / "sage_pilot_q1_2024.sha256"
    manifest_path = output_dir / "manifest.json"

    engine = create_db_engine()
    session_factory = get_session_factory(engine)

    logger.info("Loading Q1 2024 pilot observations from database...")
    with session_factory() as session:
        df, station_coords = load_pilot_dataset_from_db(session)
        logger.info("Loaded %d observations across %d stations.", len(df), len(station_coords))

    if df.empty or len(df) < 50:
        raise ValueError(f"Insufficient pilot observations in DB ({len(df)} records). Cannot train model.")

    logger.info("Building spatiotemporal features...")
    featured_df = build_spatiotemporal_features(df, station_coords)

    logger.info("Partitioning chronological splits (60% train, 20% cal, 20% test)...")
    splits = partition_dataset(featured_df, station_coords, train_pct=0.60, cal_pct=0.20)
    logger.info(
        "Splits created: %d train, %d cal, %d test records. Features: %d",
        len(splits.train),
        len(splits.calibrate),
        len(splits.test),
        len(splits.feature_columns),
    )

    dataset_hash = compute_dataset_content_hash(splits)
    logger.info("Dataset content hash: %s", dataset_hash)

    logger.info("Configuring SAGEForecaster (nominal_coverage=%.2f)...", nominal_coverage)
    cfg = SAGEConfig(
        gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0),
        nominal_coverage=nominal_coverage,
        max_disagreement_threshold=20.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
        caution_age_hours=3.0,
    )
    model = SAGEForecaster(config=cfg, feature_columns=splits.feature_columns)

    logger.info("Fitting SAGE point, quantile, and sensitivity models...")
    model.fit(
        X=splits.train,
        y=splits.train["target"].to_numpy(),
        X_cal=splits.calibrate,
        y_cal=splits.calibrate["target"].to_numpy(),
    )
    logger.info("Model fitting complete.")

    logger.info("Serializing model artifact to %s...", model_path)
    joblib.dump(model, model_path, compress=3)

    model_bytes = model_path.read_bytes()
    computed_sha256 = hashlib.sha256(model_bytes).hexdigest().lower()
    logger.info("Computed SHA-256 digest: %s (%d bytes)", computed_sha256, len(model_bytes))

    # Save legacy sha256 checksum file
    sha_path.write_text(f"{computed_sha256}  {model_path.name}\n", encoding="utf-8")

    # Create and save ModelManifest
    manifest = ModelManifest(
        model_id="sage_pilot_q1_2024",
        version="1.0.0",
        sha256=computed_sha256,
        algorithm="SAGE-LIGHTGBM",
        created_at=datetime.now(UTC).isoformat(),
        dataset_hash=dataset_hash,
        nominal_coverage=nominal_coverage,
        extra={
            "n_train": len(splits.train),
            "n_cal": len(splits.calibrate),
            "n_test": len(splits.test),
            "n_stations": len(splits.station_coords),
            "feature_columns": splits.feature_columns,
            "gamma_grid": list(cfg.gamma_grid),
            "base_model_type": cfg.base_model_type,
        },
    )
    manifest.save(manifest_path)
    logger.info("Model manifest saved to %s", manifest_path)

    return model_path, manifest_path, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Train SAGE pilot model offline and generate manifest")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/models"),
        help="Directory to save model artifact and manifest",
    )
    parser.add_argument(
        "--nominal-coverage",
        type=float,
        default=0.90,
        help="Target nominal coverage level (default 0.90)",
    )
    args = parser.parse_args()

    try:
        model_path, manifest_path, manifest = train_offline_pilot_model(
            output_dir=args.output_dir,
            nominal_coverage=args.nominal_coverage,
        )
        print("\n================ SAGE OFFLINE TRAINING COMPLETE ================")
        print(f"Model Path:       {model_path}")
        print(f"Manifest Path:    {manifest_path}")
        print(f"Model ID:         {manifest.model_id}")
        print(f"Version:          {manifest.version}")
        print(f"SHA-256 Digest:   {manifest.sha256}")
        print(f"Dataset Hash:     {manifest.dataset_hash}")
        print(f"Algorithm:        {manifest.algorithm}")
        print(f"Coverage Target:  {manifest.nominal_coverage * 100:.1f}%")
        print("================================================================")
        return 0
    except Exception as e:
        logger.exception("Offline training failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
