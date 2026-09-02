from fastapi.testclient import TestClient

from temporalrca_server.main import create_app


def test_liveness_route():
    response = TestClient(create_app()).get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_and_ground_truth_routes_are_exact():
    app = create_app()
    assert str(app.url_path_for("readiness")) == "/health/ready"
    assert str(app.url_path_for("write_ground_truth")) == "/api/v1/ground-truth/events"


def test_ground_truth_write_requires_scoped_token_without_touching_database():
    body = {
        "event_id": "b6dce7dd-ec6d-42e5-a550-36e7bac90fca",
        "experiment_id": "240d47bf-8d5f-490a-891f-bb71982e6c43",
        "timestamp": "2026-01-01T00:00:00Z",
        "observed_timestamp": "2026-01-01T00:00:01Z",
        "name": "fault.cpu", "event_type": "start",
        "host_id": "cf031e02-d209-426a-a251-46e02f027570",
    }
    response = TestClient(create_app()).post("/api/v1/ground-truth/events", json=body)
    assert response.status_code == 401
