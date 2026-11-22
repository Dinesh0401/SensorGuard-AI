"""Outage simulation invariants."""

import numpy as np
import pytest

from sensorguard.simulation import OutageConfig, generate_network, inject_outages


@pytest.mark.parametrize("mechanism", ["mcar", "mar", "mnar", "burst"])
def test_outage_mechanisms_separate_hidden_truth(mechanism):
    frame = generate_network(days=20, stations=3)
    observed, truth = inject_outages(frame, OutageConfig(mechanism=mechanism, missing_rate=0.3))
    assert len(observed) == len(truth)
    assert "true_pm25" not in observed
    assert "pm25" not in truth
    assert observed.loc[~observed["reported"], "pm25"].isna().all()
    assert np.isfinite(truth["true_pm25"]).all()
    assert 0 < (~observed["reported"]).mean() < 0.8


def test_outage_reproducible():
    data = generate_network(days=8)
    a, _ = inject_outages(data, OutageConfig(mechanism="mnar"))
    b, _ = inject_outages(data, OutageConfig(mechanism="mnar"))
    assert a.equals(b)


def test_mnar_can_depend_on_hidden_pollution():
    data = generate_network(days=60, stations=5)
    observed, truth = inject_outages(data, OutageConfig(mechanism="mnar", gamma=4, missing_rate=0.3))
    hidden_mean = truth.loc[~observed["reported"], "true_pm25"].mean()
    reported_mean = truth.loc[observed["reported"], "true_pm25"].mean()
    assert hidden_mean > reported_mean
