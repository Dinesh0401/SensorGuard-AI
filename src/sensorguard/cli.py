"""Local experiment CLI: python -m sensorguard.cli run-demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sensorguard.experiment import ExperimentConfig, run_experiment
from sensorguard.simulation import OutageConfig


def main() -> None:
    """Run a reproducible synthetic study and optionally save its JSON artifact."""
    parser = argparse.ArgumentParser(description="SensorGuard synthetic benchmark")
    parser.add_argument("command", choices=["run-demo"])
    parser.add_argument("--mechanism", choices=["mcar", "mar", "mnar", "burst"], default="mnar")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_experiment(ExperimentConfig(outage=OutageConfig(mechanism=args.mechanism, seed=args.seed)))
    rendered = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
