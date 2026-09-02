#!/usr/bin/env python3
"""Repeatable sampling-overhead benchmark for the Compose lab."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

INTERVALS = (0.5, 1.0, 2.0, 5.0)


def compose(*args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(["docker", "compose", *args], check=True, text=True, capture_output=True, env=env)
    return completed.stdout.strip()


def server_metrics(url: str) -> dict[str, float]:
    try:
        text = urllib.request.urlopen(f"{url.rstrip('/')}/metrics", timeout=5).read().decode()
    except Exception:
        return {}
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        key, value = line.rsplit(" ", 1)
        if "{" not in key:
            try:
                metrics[key] = float(value)
            except ValueError:
                pass
    return metrics


def sample_container(container: str) -> dict[str, str]:
    raw = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    return json.loads(raw)


def run(output: Path, duration: int, repetitions: int, url: str) -> None:
    rows: list[dict[str, object]] = []
    for interval in INTERVALS:
        for repetition in range(1, repetitions + 1):
            environment = dict(os.environ)
            environment["AGENT_COLLECTION_INTERVAL_SECONDS"] = str(interval)
            compose("up", "-d", "--force-recreate", "agent", env=environment)
            container = compose("ps", "-q", "agent")
            if not container:
                raise RuntimeError("Compose service 'agent' did not start")
            started_metrics = server_metrics(url)
            started = time.time()
            samples: list[dict[str, str]] = []
            while time.time() - started < duration:
                samples.append(sample_container(container))
                time.sleep(interval)
            ended_metrics = server_metrics(url)
            for sample in samples:
                rows.append({
                    "sampling_interval_seconds": interval,
                    "repetition": repetition,
                    "timestamp": time.time(),
                    "cpu_percent": sample.get("CPUPerc"),
                    "memory": sample.get("MemUsage"),
                    "network_io": sample.get("NetIO"),
                    "block_io": sample.get("BlockIO"),
                    "server_metric_delta": json.dumps({key: ended_metrics.get(key, 0) - value for key, value in started_metrics.items()}),
                })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--server", default="http://localhost:8000")
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.csv"))
    args = parser.parse_args()
    run(args.output, args.duration, args.repetitions, args.server)
