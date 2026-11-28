"""Run SAGE ablation experiments across sensitivity grids, policies, and feature subsets.

Evaluates 5 pre-registered ablation conditions across 3 random seeds (11, 42, 107):
1. Full SAGE (gamma in [0.0, 0.5, 1.0, 1.5, 2.0], propensity weights, guarded policy).
2. No Sensitivity Grid (gamma = 0.0 only, MAR assumption).
3. No Abstention Policy (always publishes, max disagreement/stale thresholds disabled).
4. No Spatial Features (excludes neighbor inverse-distance weighted PM2.5).
5. No Availability / Age Features (excludes age_hours latency tracking).

Tracks runs in MLflow under 'SensorGuard-SAGE-Ablations' and writes research reports.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from sensorguard.ablation import (
    DEFAULT_SEEDS,
    generate_ablation_markdown,
    run_sage_ablation_suite,
)
from sensorguard.db.session import create_db_engine, get_session_factory
from sensorguard.models.data import (
    build_spatiotemporal_features,
    load_pilot_dataset_from_db,
    partition_dataset,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SAGE Ablation Benchmark Matrix")
    parser.add_argument(
        "--output-json",
        default="docs/audit/sage_ablation_results.json",
        help="Path for JSON ablation results",
    )
    parser.add_argument(
        "--output-md",
        default="docs/SAGE_ABLATION_STUDY.md",
        help="Path for Markdown ablation report",
    )
    parser.add_argument(
        "--nominal-coverage",
        type=float,
        default=0.90,
        help="Nominal interval coverage level",
    )
    args = parser.parse_args()

    engine = create_db_engine()
    session_factory = get_session_factory(engine)

    with session_factory() as session:
        logger.info("Loading observations from SQLite database...")
        df, station_coords = load_pilot_dataset_from_db(session)
        logger.info("Loaded %d valid observations across %d stations.", len(df), len(station_coords))

    logger.info("Constructing spatiotemporal features...")
    featured_df = build_spatiotemporal_features(df, station_coords)

    logger.info("Chronologically splitting dataset...")
    splits = partition_dataset(featured_df, station_coords, train_pct=0.60, cal_pct=0.20)
    logger.info(
        "Splits: %d train, %d cal, %d test.", len(splits.train), len(splits.calibrate), len(splits.test)
    )

    logger.info("Executing SAGE ablation study across seeds %s...", DEFAULT_SEEDS)
    summary = run_sage_ablation_suite(
        splits=splits,
        seeds=DEFAULT_SEEDS,
        nominal_coverage=args.nominal_coverage,
        track_mlflow=True,
    )

    # Save JSON
    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    logger.info("Saved JSON ablation results to %s", out_json)

    # Save Markdown
    md_content = generate_ablation_markdown(summary)
    out_md = Path(args.output_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md_content, encoding="utf-8")
    logger.info("Saved Markdown ablation study to %s", out_md)

    print("\n================ SAGE ABLATION EXECUTION SUMMARY ================")
    print(f"Study ID:          {summary['study_id']}")
    print(f"Dataset Hash:      {summary['dataset_hash']}")
    print(f"Seeds:             {summary['seeds']}")
    print(f"Conditions:        {summary['conditions']}")
    print(f"JSON Output:       {out_json}")
    print(f"Markdown Output:   {out_md}")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
