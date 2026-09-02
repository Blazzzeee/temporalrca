#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta


def get(base: str, path: str) -> object:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=15) as response:
        return json.load(response)


def get_bytes(base: str, path: str) -> bytes:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as response:
        return response.read()


def items(value: object, key: str | None = None) -> list[dict]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if key and isinstance(value.get(key), list):
            return value[key]
        return value.get("items", value.get("data", []))
    return []


def verify_parquet_export(bundle: bytes) -> None:
    """Verify the self-contained export and every checksum in its manifest."""
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names, "export is missing manifest.json"
        manifest = json.loads(archive.read("manifest.json"))
        files = manifest.get("files")
        assert isinstance(files, dict) and files, "export manifest has no dataset files"
        for filename, expected in files.items():
            assert filename.endswith(".parquet"), f"manifest contains non-Parquet dataset {filename}"
            assert filename in names, f"export is missing {filename}"
            actual = hashlib.sha256(archive.read(filename)).hexdigest()
            assert actual == expected, f"checksum mismatch for {filename}"


def main(base: str, require_recovery: bool, experiment_id: str | None, min_processes_per_host: int) -> None:
    health = get(base, "/health/ready")
    assert isinstance(health, dict) and health.get("status") in {"ok", "ready"}, health
    topology = get(base, "/api/v1/topology")
    hosts = items(topology, "hosts")
    assert len(hosts) >= 3, f"expected at least 3 hosts, got {len(hosts)}"
    processes = items(topology, "processes")
    assert processes, "process topology is empty"
    for host in hosts:
        host_processes = [
            process for process in processes
            if process.get("host_id") == host.get("id") and process.get("active", True)
        ]
        assert len(host_processes) >= min_processes_per_host, (
            f"{host.get('name', host.get('id'))} has {len(host_processes)} active processes; "
            f"expected at least {min_processes_per_host}"
        )
    catalog = items(get(base, "/api/v1/metrics/catalog"))
    assert catalog, "metric catalog is empty"
    # The catalog describes definitions; source_type is attached to each series.
    sources = {entry.get("source_type") for entry in items(get(base, "/api/v1/metrics/series"))}
    assert {"system", "process", "application", "dependency"} <= sources, sources
    if require_recovery:
        end = datetime.now(UTC)
        start = end - timedelta(days=1)
        params = {"start": start.isoformat(), "end": end.isoformat(), "signal_type": "collector-health", "limit": 2000}
        events = items(get(base, "/api/v1/events?" + urllib.parse.urlencode(params)))
        losses = [event for event in events if event.get("name") in {"telemetry.gap", "spool.eviction", "agent.spool.data_loss"}]
        assert not losses, f"unaccounted telemetry loss: {losses[:3]}"
    export_verified = False
    if experiment_id:
        path = "/api/v1/experiments/{}/export".format(urllib.parse.quote(experiment_id, safe=""))
        verify_parquet_export(get_bytes(base, path))
        export_verified = True
    print(json.dumps({"status": "passed", "hosts": len(hosts), "sources": sorted(sources),
                      "export_verified": export_verified}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--require-recovery", action="store_true")
    parser.add_argument("--experiment-id", help="also download and checksum the experiment Parquet export")
    parser.add_argument("--min-processes-per-host", type=int, default=1,
                        help="minimum active process records per host (default: 1)")
    args = parser.parse_args()
    if args.min_processes_per_host < 1:
        parser.error("--min-processes-per-host must be at least 1")
    main(args.url, args.require_recovery, args.experiment_id, args.min_processes_per_host)
