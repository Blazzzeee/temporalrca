#!/usr/bin/env python3
"""Bounded, allowlisted and cleanup-first experiment runner."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import yaml

MAX_DURATION = 300
SAFE_NAME = re.compile(r"^[a-zA-Z0-9_.:@-]+$")


@dataclass
class RunningFault:
    process: asyncio.subprocess.Process | None
    cleanup: Callable[[], Awaitable[None]]


class GroundTruth:
    def __init__(self) -> None:
        self.url = os.getenv("TEMPORALRCA_URL", "http://localhost:8000").rstrip("/")
        self.token = os.getenv("TEMPORALRCA_GROUND_TRUTH_TOKEN", "")
        self.path = Path(os.getenv("GROUND_TRUTH_LOG", "ground-truth.jsonl"))

    async def record(self, event: dict[str, Any]) -> None:
        event = {**event, "recorded_at": time.time()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
        if not self.token:
            return
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=10) as client:
            host_ref = str(event.get("host") or "")
            try:
                host_id = str(uuid.UUID(host_ref))
            except ValueError:
                topology = (await client.get(f"{self.url}/api/v1/topology")).raise_for_status().json()
                host = next((item for item in topology.get("hosts", []) if host_ref in {item.get("name"), item.get("external_id")}), None)
                if host is None:
                    raise ValueError(f"host {host_ref!r} was not found in server inventory")
                host_id = str(host["id"])
            stamp = datetime.fromtimestamp(float(event["timestamp"]), UTC).isoformat()
            state = str(event["state"])
            payload = {
                "event_id": str(uuid.uuid4()), "experiment_id": event["experiment_id"],
                "timestamp": stamp, "observed_timestamp": stamp, "name": event["scenario"],
                "event_type": state, "message": event.get("error"), "host_id": host_id,
                "attributes": {"parameters": event.get("parameters", {}), "configured_host": host_ref},
                "experiment_name": event["name"],
                "experiment_status": "running" if state == "started" else "failed" if state == "failed" else "completed",
                "configuration": {"scenario": event["scenario"], "parameters": event.get("parameters", {})},
            }
            response = await client.post(f"{self.url}/api/v1/ground-truth/events", json=payload, headers=headers)
            response.raise_for_status()


async def command(*args: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)


async def terminate(process: asyncio.subprocess.Process | None) -> None:
    if process and process.returncode is None:
        process.send_signal(signal.SIGTERM)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), 10)
        if process.returncode is None:
            process.kill()


def checked_int(params: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = int(params.get(key, default))
    if not low <= value <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return value


async def start_fault(kind: str, params: dict[str, Any], duration: int) -> RunningFault:
    if kind in {"cpu_pressure", "process_contention"}:
        workers = checked_int(params, "workers", 1, 1, 8)
        proc = await command("stress-ng", "--cpu", str(workers), "--timeout", f"{duration}s", "--metrics-brief")
        return RunningFault(proc, lambda: terminate(proc))
    if kind == "memory_pressure":
        workers = checked_int(params, "workers", 1, 1, 4)
        percent = checked_int(params, "percent", 20, 1, 60)
        proc = await command("stress-ng", "--vm", str(workers), "--vm-bytes", f"{percent}%", "--timeout", f"{duration}s")
        return RunningFault(proc, lambda: terminate(proc))
    if kind == "disk_io":
        size_mb = checked_int(params, "size_mb", 128, 8, 1024)
        # Use a per-run path so an experiment cannot overwrite another run's
        # file (or another user-created fault file).
        fault_path = Path("/tmp") / f"temporalrca-fault-{uuid.uuid4().hex}.bin"
        proc = await command("fio", "--name=temporalrca", f"--filename={fault_path}", "--rw=randrw", "--direct=1", f"--size={size_mb}M", f"--runtime={duration}", "--time_based=1")

        async def cleanup_disk() -> None:
            await terminate(proc)
            fault_path.unlink(missing_ok=True)

        return RunningFault(proc, cleanup_disk)
    if kind == "worker_termination":
        unit = str(params.get("unit", "temporalrca-worker@1.service"))
        if not re.fullmatch(r"temporalrca-worker@[1-3]\.service", unit):
            raise ValueError("unit must be temporalrca-worker@1..3.service")
        stopped = await command("systemctl", "stop", unit)
        if await stopped.wait() != 0:
            raise RuntimeError((await stopped.stderr.read()).decode())

        async def restart() -> None:
            process = await command("systemctl", "start", unit)
            if await process.wait() != 0:
                raise RuntimeError((await process.stderr.read()).decode())

        return RunningFault(None, restart)
    if kind == "network_delay":
        interface = str(params.get("interface", "eth0"))
        delay_ms = checked_int(params, "delay_ms", 100, 1, 2000)
        if not SAFE_NAME.fullmatch(interface):
            raise ValueError("unsafe network interface")
        applied = await command("tc", "qdisc", "replace", "dev", interface, "root", "netem", "delay", f"{delay_ms}ms")
        if await applied.wait() != 0:
            raise RuntimeError((await applied.stderr.read()).decode())

        async def remove_qdisc() -> None:
            cleanup = await command("tc", "qdisc", "del", "dev", interface, "root")
            await cleanup.wait()

        return RunningFault(None, remove_qdisc)
    if kind in {"postgres_lock", "postgres_slowdown"}:
        dsn = str(params.get("dsn", os.getenv("MONITORED_DATABASE_URL", "postgresql://workload:workload@localhost/workload")))
        sql = f"BEGIN; LOCK TABLE processed_jobs IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep({duration}); ROLLBACK;" if kind == "postgres_lock" else f"SELECT pg_sleep({duration});"
        proc = await command("psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql)
        return RunningFault(proc, lambda: terminate(proc))
    if kind == "redis_backlog":
        queue = str(params.get("queue", "temporalrca:jobs"))
        items = checked_int(params, "items", 500, 1, 10000)
        if not SAFE_NAME.fullmatch(queue):
            raise ValueError("unsafe Redis queue")
        redis_url = str(params.get("url", os.getenv("REDIS_URL", "redis://localhost:6379/0")))
        values: list[str] = []
        for index in range(items):
            value = json.dumps({"fault": True, "experiment": str(uuid.uuid4()), "index": index}, separators=(",", ":"))
            proc = await command("redis-cli", "-u", redis_url, "RPUSH", queue, value)
            if await proc.wait() != 0:
                # Remove markers already inserted if a later insertion fails.
                for inserted in values:
                    cleanup = await command("redis-cli", "-u", redis_url, "LREM", queue, "0", inserted)
                    await cleanup.wait()
                raise RuntimeError("redis-cli failed")
            values.append(value)

        async def cleanup_redis() -> None:
            for value in values:
                cleanup = await command("redis-cli", "-u", redis_url, "LREM", queue, "0", value)
                await cleanup.wait()

        return RunningFault(None, cleanup_redis)
    raise ValueError(f"scenario {kind!r} is not allowlisted")


async def run_experiment(config: dict[str, Any], truth: GroundTruth) -> None:
    kind = str(config["scenario"])
    duration = int(config.get("duration_seconds", 30))
    if not 1 <= duration <= MAX_DURATION:
        raise ValueError(f"duration_seconds must be 1..{MAX_DURATION}")
    experiment_id = str(config.get("id", uuid.uuid4()))
    base = {"experiment_id": experiment_id, "name": str(config.get("name", kind)), "scenario": kind, "host": config.get("host"), "parameters": config.get("parameters", {})}
    await truth.record({**base, "state": "started", "timestamp": time.time()})
    fault: RunningFault | None = None
    main_error: Exception | None = None
    try:
        fault = await start_fault(kind, dict(config.get("parameters", {})), duration)
        if fault.process:
            await asyncio.wait_for(fault.process.wait(), duration + 15)
            if fault.process.returncode:
                raise RuntimeError((await fault.process.stderr.read()).decode())
        else:
            await asyncio.sleep(duration)
        await truth.record({**base, "state": "ended", "timestamp": time.time()})
    except Exception as exc:
        main_error = exc
        await truth.record({**base, "state": "failed", "timestamp": time.time(), "error": str(exc)})
        raise
    finally:
        if fault:
            try:
                await fault.cleanup()
            except Exception as cleanup_error:
                # A fault is not considered complete until its side effects
                # are removed. Record cleanup failures as failed ground truth
                # while preserving the original experiment error, if any.
                if main_error is None:
                    await truth.record({
                        **base, "state": "failed", "timestamp": time.time(),
                        "error": f"cleanup failed: {cleanup_error}",
                    })
                    raise


async def main(path: Path) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    experiments = document.get("experiments", [])
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("configuration must contain a non-empty experiments list")
    truth = GroundTruth()
    for experiment in experiments:
        await run_experiment(experiment, truth)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    asyncio.run(main(args.config))
