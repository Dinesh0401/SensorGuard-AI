"""Research benchmark and calibration safety tests."""

import numpy as np
import pytest

from sensorguard.calibration import conformal_radius, policy
from sensorguard.experiment import ExperimentConfig, run_experiment
from sensorguard.simulation import OutageConfig


def test_conformal_uses_finite_sample_order_statistic():
    assert conformal_radius(np.array([0.0, 0.0, 0.0, 0.0]), np.array([1.0, 2.0, 3.0, 4.0]), 0.25) == 4
    assert np.isinf(conformal_radius(np.array([0.0, 0.0]), np.array([1.0, 2.0]), 0.1))
    with pytest.raises(ValueError):
        conformal_radius(np.array([0.0]), np.array([1.0]))


def test_policy_never_publishes_very_stale_forecast():
    status, _ = policy(np.array([1.0, 5.0, 13.0]))
    assert list(status) == ["publish", "caution", "abstain"]


@pytest.mark.parametrize("mechanism", ["mcar", "mar", "mnar", "burst"])
def test_research_benchmark_runs_and_reports_observed_labels(mechanism):
    config = ExperimentConfig(days=24, stations=3, outage=OutageConfig(mechanism=mechanism, missing_rate=0.2))
    result = run_experiment(config)
    assert result["data_mode"] == "synthetic"
    assert result["partition"]["train_observed"] > 0
    for output in result["models"].values():
        assert 0 <= output["metrics"]["coverage_all_synthetic_truth"] <= 1
        assert output["metrics"]["n_test"] > 0
