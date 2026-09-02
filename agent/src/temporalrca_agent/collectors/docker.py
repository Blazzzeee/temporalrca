"""Native Docker Engine inventory and container statistics collector.

The agent deliberately talks to the Engine API over its Unix socket instead of
shelling out to the docker CLI.  This keeps collection cheap and also means the
collector can be used in the small runtime image.
"""

from __future__ import annotations

import http.client
import asyncio
import json
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..models import EntityRef, SignalType, SourceType, TelemetryEvent
from ..normalization import Normalizer
from ..procfs import counter_rate


class DockerEngineError(RuntimeError):
    """The Docker socket is unavailable or returned an invalid response."""


class DockerSocketConnection(http.client.HTTPConnection):
    """HTTPConnection variant that connects to a Unix domain socket."""

    def __init__(self, socket_path: str | Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = str(socket_path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except Exception:
            sock.close()
            raise
        self.sock = sock


class DockerEngineClient:
    def __init__(self, socket_path: str | Path = "/var/run/docker.sock", timeout: float = 3.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout

    def request(self, path: str) -> Any:
        try:
            connection = DockerSocketConnection(self.socket_path, self.timeout)
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read()
            connection.close()
        except (FileNotFoundError, PermissionError, ConnectionError, TimeoutError, OSError) as error:
            raise DockerEngineError(f"Docker socket {self.socket_path} unavailable: {error}") from error
        if response.status < 200 or response.status >= 300:
            detail = body.decode("utf-8", "replace")[:512]
            raise DockerEngineError(f"Docker Engine returned HTTP {response.status}: {detail}")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DockerEngineError("Docker Engine returned invalid JSON") from error

    def list_containers(self) -> list[dict[str, Any]]:
        # Inventory represents currently running machines. Containers that
        # disappear from this response naturally age out through the server's
        # inventory lease and remain available as historical entities.
        result = self.request("/containers/json")
        if not isinstance(result, list):
            raise DockerEngineError("Docker Engine container list was not an array")
        return [item for item in result if isinstance(item, dict) and item.get("Id")]

    def stats(self, container_id: str) -> dict[str, Any]:
        result = self.request(f"/containers/{quote(container_id, safe='')}/stats?stream=false")
        if not isinstance(result, dict):
            raise DockerEngineError(f"stats for container {container_id[:12]} was not an object")
        return result


def aggregate_network_bytes(networks: dict[str, Any] | None) -> dict[str, int]:
    totals = {"rx_bytes": 0, "tx_bytes": 0}
    for counters in (networks or {}).values():
        if not isinstance(counters, dict):
            continue
        for key in totals:
            value = counters.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                totals[key] += int(value)
    return totals


def aggregate_block_bytes(block_io: dict[str, Any] | None) -> dict[str, int]:
    totals = {"read_bytes": 0, "write_bytes": 0}
    entries = (block_io or {}).get("io_service_bytes_recursive", [])
    if not entries:
        entries = (block_io or {}).get("io_service_bytes", [])
    if not isinstance(entries, list):
        return totals
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op", "")).lower()
        value = entry.get("value")
        if not isinstance(value, (int, float)) or value < 0:
            continue
        if op == "read":
            totals["read_bytes"] += int(value)
        elif op == "write":
            totals["write_bytes"] += int(value)
    return totals


def container_cpu_utilization(stats: dict[str, Any], previous: dict[str, Any] | None = None) -> float | None:
    """Return Docker's CPU percentage using cumulative nanosecond counters."""
    cpu = stats.get("cpu_stats") or stats
    old = stats.get("precpu_stats") or previous or {}
    current_usage = cpu.get("cpu_usage", {}) if isinstance(cpu, dict) else {}
    old_usage = old.get("cpu_usage", {}) if isinstance(old, dict) else {}
    current_total = current_usage.get("total_usage")
    old_total = old_usage.get("total_usage")
    current_system = cpu.get("system_cpu_usage") if isinstance(cpu, dict) else None
    old_system = old.get("system_cpu_usage") if isinstance(old, dict) else None
    if not all(isinstance(item, (int, float)) for item in (current_total, old_total, current_system, old_system)):
        return None
    cpu_delta = current_total - old_total
    system_delta = current_system - old_system
    if cpu_delta < 0 or system_delta <= 0:
        return None
    online = cpu.get("online_cpus") if isinstance(cpu, dict) else None
    if not isinstance(online, (int, float)) or online <= 0:
        online = len(current_usage.get("percpu_usage", [])) or 1
    return max(0.0, min(100.0 * online, (cpu_delta / system_delta) * online * 100.0))


def container_stats_metrics(stats: dict[str, Any], previous: dict[str, Any] | None = None,
                            elapsed: float = 0) -> dict[str, float | int | None]:
    """Normalize one Docker stats response and calculate safe counter rates."""
    memory = stats.get("memory_stats") or {}
    usage = memory.get("usage")
    limit = memory.get("limit")
    nested = memory.get("stats") or {}
    # Docker reports page cache as part of usage on some cgroup versions.
    cache = nested.get("inactive_file", nested.get("total_inactive_file", nested.get("cache", 0)))
    if isinstance(usage, (int, float)) and isinstance(cache, (int, float)):
        usage = max(0, usage - cache)
    result: dict[str, float | int | None] = {
        "cpu_utilization": container_cpu_utilization(stats, previous),
        "memory_usage": int(usage) if isinstance(usage, (int, float)) else None,
        "memory_limit": int(limit) if isinstance(limit, (int, float)) else None,
    }
    if result["memory_usage"] is not None and isinstance(limit, (int, float)) and limit > 0:
        result["memory_percent"] = float(result["memory_usage"]) * 100.0 / limit
    else:
        result["memory_percent"] = None

    network = aggregate_network_bytes(stats.get("networks"))
    block = aggregate_block_bytes(stats.get("blkio_stats"))
    old_network = aggregate_network_bytes((previous or {}).get("networks"))
    old_block = aggregate_block_bytes((previous or {}).get("blkio_stats"))
    for key, value in {**network, **block}.items():
        result[key] = value
        rate = counter_rate(value, ({**old_network, **old_block}).get(key), elapsed)
        result[f"{key}_rate"] = rate.value
    return result


class DockerCollector:
    """Discover containers and emit container-scoped runtime metrics."""

    def __init__(self, socket_path: str | Path, normalizer: Normalizer, timeout: float = 3.0,
                 client: DockerEngineClient | None = None) -> None:
        self.client = client or DockerEngineClient(socket_path, timeout)
        self.normalizer = normalizer
        self.previous: dict[str, tuple[float, dict[str, Any]]] = {}
        self.latest_inventory: dict[str, dict[str, Any]] = {}

    def list_containers(self) -> list[dict[str, Any]]:
        containers = self.client.list_containers()
        inventory: dict[str, dict[str, Any]] = {}
        for item in containers:
            external_id = str(item["Id"])
            names = item.get("Names") or []
            name = str(names[0] if names else external_id[:12]).lstrip("/")
            labels = item.get("Labels") if isinstance(item.get("Labels"), dict) else {}
            service = labels.get("com.docker.compose.service")
            inventory[external_id] = {
                "external_id": external_id,
                "name": name,
                "runtime": "docker",
                "image": str(item.get("Image")) if item.get("Image") else None,
                "service_external_id": str(service) if service else None,
                "attributes": {"state": str(item.get("State", "")), "status": str(item.get("Status", "")),
                               "restart_count": int(item.get("RestartCount", 0) or 0)},
                "state": str(item.get("State", "")),
                "status": str(item.get("Status", "")),
                "restart_count": int(item.get("RestartCount", 0) or 0),
            }
        self.latest_inventory = inventory
        return containers

    def inventory(self) -> list[dict[str, Any]]:
        return list(self.latest_inventory.values())

    def collect(self, resource_ids: dict[str, str]) -> list[TelemetryEvent]:
        containers = self.list_containers()
        stats_by_id = {
            str(item["Id"]): self.client.stats(str(item["Id"]))
            for item in containers
            if resource_ids.get(str(item["Id"])) and str(item.get("State", "")) == "running"
        }
        return self._events(containers, resource_ids, stats_by_id)

    async def collect_async(self, resource_ids: dict[str, str], max_concurrency: int = 32) -> list[TelemetryEvent]:
        """Collect without blocking the event loop, with bounded Engine requests."""
        containers = await asyncio.to_thread(self.list_containers)
        semaphore = asyncio.Semaphore(max(1, max_concurrency))
        running = [item for item in containers
                   if resource_ids.get(str(item["Id"])) and str(item.get("State", "")) == "running"]

        async def fetch(item: dict[str, Any]) -> tuple[str, dict[str, Any] | BaseException]:
            external_id = str(item["Id"])
            async with semaphore:
                try:
                    return external_id, await asyncio.to_thread(self.client.stats, external_id)
                except Exception as error:
                    return external_id, error

        results = await asyncio.gather(*(fetch(item) for item in running))
        stats_by_id: dict[str, dict[str, Any]] = {}
        failures: list[BaseException] = []
        for external_id, result in results:
            if isinstance(result, BaseException):
                failures.append(result)
            else:
                stats_by_id[external_id] = result
        events = self._events(containers, resource_ids, stats_by_id)
        # A transient failure for one container should not discard samples from
        # the others. Surface the error only when no sample could be collected;
        # the guarded loop then reports a clear collector-health failure.
        if failures and not stats_by_id and running:
            raise failures[0]
        return events

    def _events(self, containers: list[dict[str, Any]], resource_ids: dict[str, str],
                stats_by_id: dict[str, dict[str, Any]]) -> list[TelemetryEvent]:
        now = time.monotonic()
        events: list[TelemetryEvent] = []
        active_ids = set()
        for item in containers:
            external_id = str(item["Id"])
            resource_id = resource_ids.get(external_id)
            stats = stats_by_id.get(external_id)
            if not resource_id or stats is None:
                continue
            active_ids.add(external_id)
            old_entry = self.previous.get(external_id)
            old = old_entry[1] if old_entry else None
            elapsed = now - old_entry[0] if old_entry else 0
            values = container_stats_metrics(stats, old, elapsed)
            ref = EntityRef(container_id=resource_id)
            for name, value, unit in (
                ("container.cpu.utilization", values["cpu_utilization"], "%"),
                ("container.memory.usage", values["memory_usage"], "By"),
                ("container.memory.limit", values["memory_limit"], "By"),
                ("container.memory.percent", values["memory_percent"], "%"),
                ("container.network.rx_bytes", values["rx_bytes"], "By"),
                ("container.network.tx_bytes", values["tx_bytes"], "By"),
                ("container.block_io.read_bytes", values["read_bytes"], "By"),
                ("container.block_io.write_bytes", values["write_bytes"], "By"),
                ("container.network.rx_bytes.rate", values["rx_bytes_rate"], "By/s"),
                ("container.network.tx_bytes.rate", values["tx_bytes_rate"], "By/s"),
                ("container.block_io.read_bytes.rate", values["read_bytes_rate"], "By/s"),
                ("container.block_io.write_bytes.rate", values["write_bytes_rate"], "By/s"),
            ):
                if value is not None:
                    events.append(self._metric(name, value, unit, ref))
            info = self.latest_inventory.get(external_id, {})
            events.append(self._metric("container.restarts", info.get("restart_count", 0), "1", ref,
                                       {"state": info.get("state", ""), "name": info.get("name", "")}))
            self.previous[external_id] = (now, stats)
        for external_id in list(self.previous):
            if external_id not in active_ids:
                del self.previous[external_id]
        return events

    def _metric(self, name: str, value: float | int, unit: str, entity: EntityRef,
                attributes: dict[str, Any] | None = None) -> TelemetryEvent:
        return self.normalizer.event(source=SourceType.SYSTEM, signal=SignalType.METRIC, name=name,
                                     value=value, unit=unit, entity=entity, attributes=attributes)
