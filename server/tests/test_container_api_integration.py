from __future__ import annotations

import gzip
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest


BASE_URL = os.getenv("TEMPORALRCA_TEST_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="TEMPORALRCA_TEST_URL is not configured")


def _batch(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "1.0", "batch_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(), "first_sequence": 1,
        "last_sequence": len(events), "backfill": False, "events": events,
    }


def _post_batch(client: httpx.Client, credential: str, body: dict[str, object]) -> httpx.Response:
    encoded = gzip.compress(json.dumps(body, separators=(",", ":")).encode())
    return client.post("/api/v1/telemetry/batches", content=encoded, headers={
        "authorization": f"Bearer {credential}", "content-encoding": "gzip",
        "content-type": "application/json",
    })


def test_container_detail_and_metric_filters():
    now = datetime.now(timezone.utc)
    installation = f"pytest-container-{uuid.uuid4()}"
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        registration = client.post("/api/v1/agents/register", json={
            "enrollment_token": os.getenv("TEMPORALRCA_ENROLLMENT_TOKEN", "development-enrollment-token"),
            "installation_id": installation, "host_external_id": installation,
            "host_name": installation, "agent_version": "test",
        })
        assert registration.status_code == 201, registration.text
        identity = registration.json()
        auth = {"authorization": f"Bearer {identity['credential']}"}
        container_one, container_two = f"container-a-{uuid.uuid4()}", f"container-b-{uuid.uuid4()}"
        process_external = f"boot:{uuid.uuid4()}:42:100"
        inventory = client.put("/api/v1/agents/me/inventory", headers=auth, json={
            "observed_at": now.isoformat(), "lease_seconds": 60,
            "services": [{"external_id": "worker", "name": "worker"}],
            "containers": [
                {"external_id": container_one, "name": "worker-a", "runtime": "docker", "service_external_id": "worker"},
                {"external_id": container_two, "name": "worker-b", "runtime": "docker", "service_external_id": "worker"},
            ],
            "processes": [{"external_id": process_external, "boot_id": str(uuid.uuid4()), "pid": 42,
                           "start_time_ticks": 100, "name": "worker", "service_external_id": "worker",
                           "container_external_id": container_one}],
            "dependencies": [],
        })
        assert inventory.status_code == 200, inventory.text
        resources = inventory.json()
        container_one_id = resources["container_ids"][container_one]
        container_two_id = resources["container_ids"][container_two]

        detail = client.get(f"/api/v1/containers/{container_one_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == container_one_id
        assert [row["external_id"] for row in detail.json()["processes"]] == [process_external]

        events = []
        for sequence, container_id in enumerate((container_one_id, container_two_id), start=1):
            events.append({
                "schema_version": "1.0", "event_id": str(uuid.uuid4()),
                "timestamp": now.isoformat(), "observed_timestamp": now.isoformat(),
                "sequence": sequence, "host_id": identity["host_id"], "container_id": container_id,
                "source_type": "application", "signal_type": "metric", "name": "container.queue.depth",
                "value": float(sequence), "unit": "1", "attributes": {},
            })
        accepted = _post_batch(client, identity["credential"], _batch(events))
        assert accepted.status_code == 200, accepted.text

        all_series = client.get("/api/v1/metrics/series", params={"metric": "container.queue.depth", "host_id": identity["host_id"]})
        assert all_series.status_code == 200, all_series.text
        assert {row["container_id"] for row in all_series.json()} >= {container_one_id, container_two_id}

        filtered_series = client.get("/api/v1/metrics/series", params={"metric": "container.queue.depth", "container_id": container_one_id})
        assert filtered_series.status_code == 200, filtered_series.text
        assert {row["container_id"] for row in filtered_series.json()} == {container_one_id}

        query = client.get("/api/v1/metrics/query", params={
            "metric": "container.queue.depth", "container_id": container_one_id,
            "start": (now - timedelta(seconds=1)).isoformat(), "end": (now + timedelta(seconds=2)).isoformat(),
            "max_points": 100,
        })
        assert query.status_code == 200, query.text
        assert len(query.json()["series"]) == 1
        assert query.json()["series"][0]["points"][0]["last"] == 1.0
