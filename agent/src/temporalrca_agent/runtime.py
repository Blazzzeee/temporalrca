from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any, Awaitable, Callable

from . import __version__
from .adapters import PostgreSQLAdapter, RedisAdapter
from .collectors.logs import FileLogCollector, JournalCollector
from .collectors.docker import DockerCollector
from .collectors.openmetrics import OpenMetricsCollector
from .collectors.proc import ProcCollector
from .config import Config
from .discovery import DiscoveryState, ProcessIdentity, assign_service, container_id
from .models import EntityRef, SignalType, SourceType, TelemetryEvent
from .normalization import Normalizer
from .procfs import ProcFS
from .spool import SQLiteSpool
from .transport import AgentHTTPClient, backoff_delay, retryable

LOGGER = logging.getLogger("temporalrca_agent")


class Agent:
    def __init__(self, config: Config, *, proc: ProcFS | None = None, client: AgentHTTPClient | None = None) -> None:
        self.config = config
        self.proc = proc or ProcFS(config.proc_root)
        self.client = client or AgentHTTPClient(config.central_server, config.credential)
        self.spool = SQLiteSpool(config.state_dir / "spool.db", config.spool_max_bytes, config.spool_max_age_seconds)
        self.normalizer = Normalizer()
        self.proc_collector = ProcCollector(self.proc, self.normalizer)
        self.docker_collector = DockerCollector(config.docker_socket, self.normalizer)
        self.discovery = DiscoveryState()
        self.processes: dict[ProcessIdentity, dict[str, Any]] = {}
        self.collector_health: dict[str, dict[str, Any]] = {}
        self.resource_ids: dict[str, dict[str, str]] = {name: {} for name in ("services", "processes", "containers", "dependencies")}
        self.stop_event = asyncio.Event()

    async def initialize(self) -> None:
        await self.spool.open()
        saved_credential = await self.spool.get_state("credential")
        if saved_credential:
            self.client.credential = saved_credential
        identity = await self.spool.get_state("identity")
        installation_id = self.config.installation_id or await self.spool.get_state("installation_id")
        if not installation_id:
            from uuid import uuid4
            installation_id = str(uuid4())
        await self.spool.set_state("installation_id", installation_id)
        if not self.client.credential:
            if not self.config.enrollment_token:
                raise RuntimeError("an enrollment token is required for first registration")
            identity_data = await self.client.register(
                self.config.enrollment_token, installation_id, self.config.host_name, self.config.host_attributes,
            )
            await self.spool.set_state("credential", identity_data["credential"])
            await self.spool.set_state("identity", json.dumps(identity_data))
            identity = json.dumps(identity_data)
        if identity:
            data = json.loads(identity)
            self.normalizer.host_id = data.get("host_id")
        sequence = await self.spool.get_state("sequence")
        self.normalizer.sequence = int(sequence or 0)

    async def emit(self, events: list[TelemetryEvent], state_updates: dict[str, str] | None = None) -> None:
        if not events and not state_updates:
            return
        evictions = await self.spool.append((event.as_dict() for event in events), state_updates)
        if evictions:
            loss = self.normalizer.event(source=SourceType.SYSTEM, signal=SignalType.COLLECTOR_HEALTH,
                   name="agent.spool.data_loss", severity="ERROR", message="events evicted from local spool",
                   attributes={"events_lost": len(evictions), "bytes_lost": sum(item["bytes"] for item in evictions),
                               "first_event_id": evictions[0]["event_id"]})
            # The explicit gap event is compact enough to be admitted after eviction.
            await self.spool.append([loss.as_dict()])
        await self.spool.set_state("sequence", str(self.normalizer.sequence))

    async def health_error(self, collector: str, error: BaseException) -> None:
        details = {"status": "error", "error": type(error).__name__, "message": str(error)[:512]}
        self.collector_health[collector] = details
        await self.emit([self.normalizer.event(source=SourceType.SYSTEM, signal=SignalType.COLLECTOR_HEALTH,
                        name="collector.error", severity="ERROR", message=str(error),
                        attributes={"collector": collector, "error.type": type(error).__name__})])

    async def _guarded_loop(self, name: str, interval: float, operation: Callable[[], Awaitable[None]]) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        while not self.stop_event.is_set():
            try:
                await operation()
                self.collector_health[name] = {"status": "ok"}
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.exception("collector failed", extra={"collector": name})
                await self.health_error(name, error)
            deadline += interval
            delay = max(0, deadline - loop.time())
            try:
                await asyncio.wait_for(self.stop_event.wait(), delay)
            except TimeoutError:
                pass

    async def collect_system(self) -> None:
        await self.emit(self.proc_collector.collect_system())

    async def discover(self) -> None:
        boot_id = self.proc.boot_id()
        discovered: list[dict[str, Any]] = []
        failures = 0
        for pid in self.proc.pids():
            try:
                discovered.append(self.proc.process(pid))
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError:
                failures += 1
        # Service-matched processes are the product signal. Keep a bounded,
        # deterministic set of unassigned processes for host context so an
        # unexpectedly busy machine cannot overwhelm the durable spool.
        # Container inventory is derived before the process telemetry cap. The
        # cap controls sampling cost; it must not hide otherwise running
        # containers from topology.
        discovered_containers = {
            found for process in discovered
            if (found := container_id(str(process.get("cgroup", ""))))
        }
        discovered.sort(key=lambda item: (assign_service(self.config.services, item) is None, int(item["pid"])))
        discovered = discovered[:self.config.max_monitored_processes]
        changes = self.discovery.reconcile(boot_id, discovered, self.config.services)
        self.processes = {ProcessIdentity(boot_id, int(item["pid"]), int(item["start_time_ticks"])): item for item in discovered}
        associations = self.discovery.associations
        service_names = sorted(
            {rule.service for rule in self.config.services}
            | {item.service for item in self.config.metrics if item.service}
            | {item.service for item in self.config.logs if item.service}
            | {service for dependency in self.config.dependencies for service in dependency.services}
        )
        # Docker inventory is authoritative when the Engine socket is
        # available.  Keep cgroup-derived IDs as a fallback for runtimes that
        # are not Docker (or while the independent Docker collector is down).
        docker_containers = {item["external_id"]: item for item in self.docker_collector.inventory()}
        for external_id in discovered_containers:
            docker_containers.setdefault(external_id, {
                "external_id": external_id, "name": external_id[:12], "runtime": "container",
                "image": None, "service_external_id": None, "attributes": {},
            })
        containers = docker_containers
        inventory = {
            "observed_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
            "lease_seconds": max(10, min(3600, int(self.config.discovery_interval_seconds * 3))),
            "services": [{"external_id": name, "name": name, "attributes": {}} for name in service_names],
            "containers": [{"external_id": item, "name": details.get("name", item[:12]),
                             "runtime": details.get("runtime", "container"), "image": details.get("image"),
                             "service_external_id": details.get("service_external_id"),
                             "attributes": details.get("attributes", {})}
                           for item, details in sorted(containers.items())],
            "processes": [{"external_id": identity.key, "boot_id": identity.boot_id, "pid": identity.pid,
                "start_time_ticks": identity.start_time_ticks, "name": str(process.get("name", identity.pid)),
                "command": str(process.get("cmdline", "")) or None, "service_external_id": associations.get(identity),
                                "container_external_id": self._container_external_id(str(process.get("cgroup", ""))),
                "attributes": {"state": str(process.get("state", ""))}} for identity, process in self.processes.items()],
            "dependencies": [{"external_id": item.name, "name": item.name, "kind": item.type,
                              "service_external_ids": item.services, "attributes": {}} for item in self.config.dependencies],
        }
        response = await self.client.inventory(inventory)
        response_keys = {"services": "service_ids", "processes": "process_ids",
                         "containers": "container_ids", "dependencies": "dependency_ids"}
        for kind in self.resource_ids:
            mapping = response.get(response_keys[kind], {})
            if isinstance(mapping, dict):
                self.resource_ids[kind].update({str(key): str(value) for key, value in mapping.items()})
        events: list[TelemetryEvent] = []
        for change in changes:
            process_id = self.resource_ids["processes"].get(change.identity.key)
            if process_id:
                events.append(self.normalizer.event(source=SourceType.PROCESS, signal=SignalType.LIFECYCLE, name=change.kind,
                    entity=EntityRef(process_id=process_id, service_id=self.resource_ids["services"].get(change.service or "")),
                    attributes={"pid": change.identity.pid, "start_time_ticks": change.identity.start_time_ticks,
                                "previous_service": change.previous_service or "", "service": change.service or ""}))
        if failures:
            events.append(self.normalizer.event(source=SourceType.SYSTEM, signal=SignalType.COLLECTOR_HEALTH,
                          name="collector.process.permission_denied", severity="WARNING",
                          attributes={"processes": failures}))
        await self.emit(events)

    async def collect_processes(self) -> None:
        events: list[TelemetryEvent] = []
        for identity, old in list(self.processes.items()):
            try:
                current = self.proc.process(identity.pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as error:
                await self.health_error(f"process:{identity.pid}", error)
                continue
            # Do not merge samples if the PID was reused between discovery cycles.
            if int(current["start_time_ticks"]) != identity.start_time_ticks:
                continue
            service = assign_service(self.config.services, current)
            process_id = self.resource_ids["processes"].get(identity.key)
            if process_id:
                external_container = self._container_external_id(str(current.get("cgroup", "")))
                events.extend(self.proc_collector.collect_process(current, process_id,
                    self.resource_ids["services"].get(service or ""),
                    self.resource_ids["containers"].get(external_container or "")))
        await self.emit(events)

    def _container_external_id(self, cgroup: str) -> str | None:
        """Resolve short cgroup IDs to the full Docker inventory ID."""
        found = container_id(cgroup)
        if not found:
            return None
        if found in self.resource_ids["containers"]:
            return found
        matches = [external_id for external_id in self.resource_ids["containers"] if external_id.startswith(found)]
        return matches[0] if len(matches) == 1 else found

    async def collect_docker(self) -> None:
        await self.emit(await self.docker_collector.collect_async(self.resource_ids["containers"]))

    async def sender(self) -> None:
        attempt = 0
        newest_next = True
        while not self.stop_event.is_set():
            # Normal connected delivery stays within the 500-event contract.
            # Once more than one normal batch is queued, use the server's
            # explicit 2,000-event backfill allowance to recover faster than
            # the one-second collectors can produce new telemetry.
            usage = await self.spool.usage()
            backfilling = usage["events"] > 500
            # Alternate the two ends of a large queue. Historical events keep
            # draining, while current samples do not wait minutes behind them.
            records = await self.spool.batch(max_events=2_000 if backfilling else 500,
                                             newest=backfilling and newest_next)
            if backfilling:
                newest_next = not newest_next
            if not records:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), 1)
                except TimeoutError:
                    pass
                continue
            try:
                result = await self.client.send([record.payload for record in records])
                if retryable(result.status):
                    raise ConnectionError(f"retryable HTTP response {result.status}")
                if 200 <= result.status < 300:
                    rejected_raw = result.body.get("rejections", result.body.get("rejected", []))
                    rejected: dict[str, str] = {}
                    for item in rejected_raw:
                        event_id = item.get("event_id")
                        if not event_id and isinstance(item.get("index"), int) and item["index"] < len(records):
                            event_id = records[item["index"]].event_id
                        if event_id:
                            rejected[str(event_id)] = json.dumps(item.get("errors", item.get("reason", "permanent rejection")))
                    acknowledged = result.body.get("acknowledged_event_ids", result.body.get("accepted_event_ids"))
                    acknowledged = [*acknowledged, *result.body.get("duplicate_event_ids", [])] if acknowledged is not None else None
                    if acknowledged is None:
                        acknowledged = [record.event_id for record in records if record.event_id not in rejected]
                    await self.spool.acknowledge(acknowledged)
                    await self.spool.quarantine(rejected)
                    attempt = 0
                    continue
                # Other 4xx responses are permanent for the complete batch.
                await self.spool.quarantine({record.event_id: f"HTTP {result.status}" for record in records})
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.collector_health["sender"] = {"status": "error", "message": str(error)[:512]}
                try:
                    await asyncio.wait_for(self.stop_event.wait(), backoff_delay(attempt))
                except TimeoutError:
                    pass
                attempt += 1

    async def heartbeat(self) -> None:
        usage = await self.spool.usage()
        await self.client.heartbeat({"observed_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
             "agent_version": __version__, "spool_bytes": usage["bytes"], "spool_events": usage["events"],
             "collectors": [{"name": name, "healthy": state.get("status") == "ok", "message": state.get("message")}
                            for name, state in self.collector_health.items()]})

    async def _emit_collector(self, collector: Any, mapping_kind: str | None = None, external_id: str | None = None) -> None:
        if mapping_kind and external_id:
            resource_id = self.resource_ids[mapping_kind].get(external_id)
            if not resource_id:
                return
            if mapping_kind == "services":
                collector.service_id = resource_id
            elif mapping_kind == "dependencies":
                collector.dependency_id = resource_id
        events = await collector.collect()
        state_updates: dict[str, str] = {}
        if hasattr(collector, "pending_state_update") and (update := collector.pending_state_update()):
            state_updates[update[0]] = update[1]
        await self.emit(events, state_updates)
        if hasattr(collector, "commit_state_update"):
            collector.commit_state_update()

    async def run(self) -> None:
        await self.initialize()
        file_collectors = [(item, FileLogCollector(item.name, item.path or "", self.normalizer, self.spool,
                           item.max_line_bytes, item.max_lines_per_second)) for item in self.config.logs if item.type == "file"]
        for _, collector in file_collectors:
            await collector.restore()
        journals = [(item, JournalCollector(item.name, item.unit or "", self.normalizer, self.spool,
                    item.max_lines_per_second)) for item in self.config.logs if item.type == "journald"]
        metrics = [(item, OpenMetricsCollector(item.name, item.url, self.normalizer, None, item.timeout_seconds))
                   for item in self.config.metrics]
        adapters: list[Any] = []
        for item in self.config.dependencies:
            if item.type == "postgresql":
                adapters.append((item.interval_seconds, PostgreSQLAdapter(item.name, item.dsn, self.normalizer, item.pg_stat_statements)))
            elif item.type == "redis":
                adapters.append((item.interval_seconds, RedisAdapter(
                    item.name, item.dsn, self.normalizer, item.queues, item.streams, item.queue_stats_key,
                )))
        tasks = [
            asyncio.create_task(self._guarded_loop("system", self.config.collection_interval_seconds, self.collect_system)),
            asyncio.create_task(self._guarded_loop("process", self.config.collection_interval_seconds, self.collect_processes)),
            asyncio.create_task(self._guarded_loop("discovery", self.config.discovery_interval_seconds, self.discover)),
            asyncio.create_task(self._guarded_loop("heartbeat", 10, self.heartbeat)),
            asyncio.create_task(self.sender()),
        ]
        if self.config.docker_collection_enabled:
            tasks.append(asyncio.create_task(self._guarded_loop("docker", self.config.docker_collection_interval_seconds,
                                                               self.collect_docker)))
        else:
            self.collector_health["docker"] = {"status": "disabled"}
        for definition, collector in [*file_collectors, *journals]:
            tasks.append(asyncio.create_task(self._guarded_loop(f"log:{collector.name}", 1,
                lambda c=collector, d=definition: self._emit_collector(c, "services", d.service) if d.service else self._emit_collector(c))))
        for definition, collector in metrics:
            tasks.append(asyncio.create_task(self._guarded_loop(f"metrics:{collector.name}", self.config.collection_interval_seconds,
                           lambda c=collector, d=definition: self._emit_collector(c, "services", d.service) if d.service else self._emit_collector(c))))
        for interval, adapter in adapters:
            external_id = adapter.dependency_id
            tasks.append(asyncio.create_task(self._guarded_loop(f"dependency:{adapter.name}", interval,
                           lambda c=adapter, e=external_id: self._emit_collector(c, "dependencies", e))))
        try:
            await self.stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.spool.close()

    def stop(self) -> None:
        self.stop_event.set()


async def run_agent(config: Config) -> None:
    agent = Agent(config)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, agent.stop)
    await agent.run()
