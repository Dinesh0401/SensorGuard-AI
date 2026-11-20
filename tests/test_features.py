"""No-target-leakage and historical feature stability tests."""

from sensorguard.features import build_features
from sensorguard.simulation import OutageConfig, generate_network, inject_outages


def test_features_ignore_unobserved_targets():
    raw = generate_network(days=12, stations=2)
    observed, _ = inject_outages(raw, OutageConfig(mechanism="mnar", missing_rate=0.35))
    a = build_features(observed)
    assert "pm25" not in a and "true_pm25" not in a
    assert (a["age_hours"] >= 1).all()
    modified = observed.copy()
    # Changing the last target must not change features for that same target time.
    last = modified["station_id"] == "synthetic-01"
    index = modified[last].index[-1]
    modified.loc[index, "pm25"] = 99999
    modified.loc[index, "reported"] = True
    b = build_features(modified)
    target = a.iloc[-1]  # last station-02 target remains invariant
    assert target["last_pm25"] == b.iloc[-1]["last_pm25"]
    own_target = a[a["station_id"] == "synthetic-01"].iloc[-1]
    new_own_target = b[b["station_id"] == "synthetic-01"].iloc[-1]
    assert own_target["last_pm25"] == new_own_target["last_pm25"]
