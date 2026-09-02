from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest


BASE_URL = os.getenv("TEMPORALRCA_TEST_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="TEMPORALRCA_TEST_URL is not configured")


def _batch(events, *, batch_id=None, backfill=False):
    return {
        "schema_version": "1.0", "batch_id": str(batch_id or uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(), "first_sequence": 1,
        "last_sequence": max(1, len(events)), "backfill": backfill, "events": events,
    }


def _post_batch(client, credential, body):
    encoded = gzip.compress(json.dumps(body, separators=(",", ":")).encode())
    return client.post("/api/v1/telemetry/batches", content=encoded, headers={
        "authorization": f"Bearer {credential}", "content-encoding": "gzip",
        "content-type": "application/json",
    })


def test_registration_inventory_dedupe_query_and_export_end_to_end():
    now = datetime.now(timezone.utc)
    installation = f"pytest-{uuid.uuid4()}"
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        registration = client.post("/api/v1/agents/register", json={
            "enrollment_token": os.getenv("TEMPORALRCA_ENROLLMENT_TOKEN", "development-enrollment-token"),
            "installation_id": installation, "host_external_id": installation,
            "host_name": installation, "agent_version": "test",
        })
        assert registration.status_code == 201, registration.text
        identity = registration.json()
        auth = {"authorization": f"Bearer {identity['credential']}"}
        process_external = f"boot:{uuid.uuid4()}:42:100"
        inventory = client.put("/api/v1/agents/me/inventory", headers=auth, json={
            "observed_at": now.isoformat(), "lease_seconds": 30,
            "services": [{"external_id": "api", "name": "api"}], "containers": [],
            "processes": [{"external_id": process_external, "boot_id": str(uuid.uuid4()),
                "pid": 42, "start_time_ticks": 100, "name": "worker", "service_external_id": "api"}],
            "dependencies": [{"external_id": "db", "name": "db", "kind": "postgresql", "service_external_ids": ["api"]}],
        })
        assert inventory.status_code == 200, inventory.text
        resources = inventory.json()
        assert set(resources) >= {"service_ids", "process_ids", "container_ids", "dependency_ids"}

        event_id, experiment_id = uuid.uuid4(), uuid.uuid4()
        metric = {
            "schema_version": "1.0", "event_id": str(event_id), "timestamp": now.isoformat(),
            "observed_timestamp": (now + timedelta(seconds=1)).isoformat(), "sequence": 1,
            "host_id": identity["host_id"], "service_id": resources["service_ids"]["api"],
            "experiment_id": str(experiment_id), "source_type": "application",
            "signal_type": "metric", "name": "jobs.duration", "value": 2.5,
            "unit": "seconds", "attributes": {"queue": "default"},
        }
        too_many = _post_batch(client, identity["credential"], _batch([metric] * 501))
        assert too_many.status_code == 413
        oversize = client.post("/api/v1/telemetry/batches", content=b"x" * (2 * 1024 * 1024 + 1), headers=auth)
        assert oversize.status_code == 413
        invalid = {**metric, "event_id": str(uuid.uuid4()), "sequence": 2, "value": None}
        spoofed = {**metric, "event_id": str(uuid.uuid4()), "sequence": 3, "host_id": str(uuid.uuid4())}
        original = _batch([metric, invalid, spoofed])
        accepted = _post_batch(client, identity["credential"], original)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["accepted_event_ids"] == [str(event_id)]
        assert len(accepted.json()["rejected"]) == 2
        replay = _post_batch(client, identity["credential"], original)
        assert replay.status_code == 200 and replay.json()["duplicate_batch"] is True

        duplicate = _post_batch(client, identity["credential"], _batch([metric]))
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate_event_ids"] == [str(event_id)]

        series = client.get("/api/v1/metrics/series", params={"metric": "jobs.duration", "host_id": identity["host_id"]})
        assert series.status_code == 200 and len(series.json()) == 1
        query = client.get("/api/v1/metrics/query", params={
            "series_id": series.json()[0]["id"], "start": (now - timedelta(seconds=1)).isoformat(),
            "end": (now + timedelta(seconds=2)).isoformat(), "max_points": 100,
        })
        assert query.status_code == 200
        points = query.json()["series"][0]["points"]
        assert points == [{"timestamp": points[0]["timestamp"], "min": 2.5, "max": 2.5, "average": 2.5, "last": 2.5, "count": 1}]

        truth = client.post("/api/v1/ground-truth/events", headers={
            "authorization": f"Bearer {os.getenv('TEMPORALRCA_GROUND_TRUTH_TOKEN', 'development-ground-truth-token')}"
        }, json={
            "event_id": str(uuid.uuid4()), "experiment_id": str(experiment_id),
            "timestamp": now.isoformat(), "observed_timestamp": now.isoformat(),
            "name": "cpu-pressure", "event_type": "start", "host_id": identity["host_id"],
            "experiment_name": "integration", "configuration": {"load": 1},
        })
        assert truth.status_code == 201, truth.text
        exported = client.get(f"/api/v1/experiments/{experiment_id}/export")
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            names = set(archive.namelist())
            assert {"metrics.parquet", "logs.parquet", "inventory_history.parquet", "lifecycle_ground_truth.parquet", "manifest.json"} <= names
            manifest = json.loads(archive.read("manifest.json"))
            for filename, digest in manifest["files"].items():
                assert hashlib.sha256(archive.read(filename)).hexdigest() == digest


def test_concurrent_different_batches_accept_an_event_once():
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        installation = f"pytest-concurrent-{uuid.uuid4()}"
        response = client.post("/api/v1/agents/register", json={
            "enrollment_token": os.getenv("TEMPORALRCA_ENROLLMENT_TOKEN", "development-enrollment-token"),
            "installation_id": installation, "host_external_id": installation, "host_name": installation,
        })
        identity = response.json()
    now = datetime.now(timezone.utc).isoformat()
    event = {"event_id": str(uuid.uuid4()), "timestamp": now, "observed_timestamp": now,
        "sequence": 1, "host_id": identity["host_id"], "source_type": "system",
        "signal_type": "metric", "name": "host.load", "value": 1}
    def deliver(_):
        with httpx.Client(base_url=BASE_URL, timeout=30) as client:
            return _post_batch(client, identity["credential"], _batch([event])).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(deliver, range(2)))
    assert sum(len(x["accepted_event_ids"]) for x in results) == 1
    assert sum(len(x["duplicate_event_ids"]) for x in results) == 1
