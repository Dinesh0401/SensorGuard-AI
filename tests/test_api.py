"""API discloses its synthetic nature and validates requests."""

from fastapi.testclient import TestClient

from sensorguard.api import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "synthetic_only"


def test_demo():
    response = client.get("/v1/demo/experiment?mechanism=mnar&seed=7")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "synthetic"
    assert "no MNAR coverage guarantee" in response.json()["calibration_claim"]


def test_invalid_mechanism():
    assert client.get("/v1/demo/experiment?mechanism=unknown").status_code == 422
