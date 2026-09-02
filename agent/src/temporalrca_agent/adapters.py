from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .models import EntityRef, SignalType, SourceType, TelemetryEvent
from .normalization import Normalizer


class DependencyAdapter(Protocol):
    name: str

    async def collect(self) -> list[TelemetryEvent]: ...


@dataclass(frozen=True, slots=True)
class VendorMetric:
    vendor_name: str
    concept: str
    value: float
    unit: str = "1"
    attributes: dict[str, Any] | None = None


def _created_at_age(payload: Any, now: float) -> float | None:
    """Return a non-negative item age without letting malformed payloads break collection."""
    if payload is None:
        return 0.0
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, Mapping):
        return None
    try:
        return max(0.0, now - float(payload.get("created_at")))
    except (TypeError, ValueError):
        return None


def normalize_vendor_metrics(dependency_id: str, system: str, metrics: list[VendorMetric],
                             normalizer: Normalizer) -> list[TelemetryEvent]:
    """Convert vendor samples into the shared dependency metric envelope.

    `dependency.system` is present for every adapter. Database and messaging
    adapters add namespace/destination dimensions without changing metric
    names, allowing PostgreSQL, Redis, and future Kafka/RabbitMQ adapters to
    share central ingestion and dashboard behavior.
    """
    ref = EntityRef(dependency_id=dependency_id)
    base_attributes = {"dependency.system": system}
    if system == "postgresql":
        base_attributes["db.system"] = system
    return [normalizer.event(source=SourceType.DEPENDENCY, signal=SignalType.METRIC, name=metric.concept,
              value=metric.value, unit=metric.unit, entity=ref,
              attributes={"vendor_metric_name": metric.vendor_name, **(metric.attributes or {}),
                          **base_attributes}) for metric in metrics]


class PostgreSQLAdapter:
    name = "postgresql"

    def __init__(self, dependency_id: str, dsn: str, normalizer: Normalizer, pg_stat_statements: bool = False,
                 query: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None) -> None:
        self.dependency_id, self.dsn, self.normalizer = dependency_id, dsn, normalizer
        self.include_statements, self._query_override = pg_stat_statements, query
        self._previous: dict[tuple[str, str], float] = {}
        self._previous_at: float | None = None

    async def _query(self) -> list[dict[str, Any]]:
        if self._query_override:
            return await self._query_override()
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("install temporalrca-agent[postgres] for PostgreSQL collection") from error
        def execute() -> list[dict[str, Any]]:
            started = time.perf_counter()
            with psycopg.connect(self.dsn, connect_timeout=3) as connection:
                connect_latency = time.perf_counter() - started
                with connection.cursor() as cursor:
                    cursor.execute("""SELECT datname, numbackends, xact_commit, xact_rollback, blks_read, blks_hit,
                      tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted, conflicts, temp_files,
                      temp_bytes, deadlocks, pg_database_size(datname) AS database_size FROM pg_stat_database
                      WHERE datname IS NOT NULL""")
                    names = [column.name for column in cursor.description]
                    rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
                    cursor.execute("SELECT count(*), count(*) FILTER (WHERE NOT granted) FROM pg_locks")
                    locks, waits = cursor.fetchone()
                    statements = None
                    if self.include_statements:
                        cursor.execute("SELECT sum(calls), sum(total_exec_time), sum(rows) FROM pg_stat_statements")
                        statement_row = cursor.fetchone()
                        statements = {"calls": statement_row[0] or 0, "total_exec_time_ms": statement_row[1] or 0,
                                      "rows": statement_row[2] or 0}
            return [{"connect_latency_seconds": connect_latency, "locks": locks, "waiting_locks": waits,
                     "databases": rows, "statements": statements}]
        return await asyncio.to_thread(execute)

    async def collect(self) -> list[TelemetryEvent]:
        collected_at = time.monotonic()
        result = await self._query()
        root = result[0] if result else {}
        mapping = {"connect_latency_seconds": ("dependency.connectivity.latency", "s"),
                   "locks": ("dependency.locks", "1"),
                   "waiting_locks": ("dependency.locks.waiting", "1")}
        metrics = [VendorMetric(key, concept, float(root[key]), unit) for key, (concept, unit) in mapping.items() if root.get(key) is not None]
        database_mapping = {
            "numbackends": "dependency.connections", "xact_commit": "dependency.transactions.committed",
            "xact_rollback": "dependency.transactions.rolled_back", "blks_read": "dependency.blocks.read",
            "blks_hit": "dependency.blocks.hit", "tup_returned": "dependency.rows.returned",
            "tup_fetched": "dependency.rows.fetched", "tup_inserted": "dependency.rows.inserted",
            "tup_updated": "dependency.rows.updated", "tup_deleted": "dependency.rows.deleted",
            "conflicts": "dependency.errors.conflicts", "temp_files": "dependency.temporary.files",
            "temp_bytes": "dependency.temporary.bytes", "deadlocks": "dependency.locks.deadlocks",
            "database_size": "dependency.storage.size",
        }
        for database in root.get("databases", []):
            for vendor, concept in database_mapping.items():
                if database.get(vendor) is not None:
                    metrics.append(VendorMetric(f"pg_stat_database.{vendor}", concept, float(database[vendor]),
                                               "By" if vendor in {"temp_bytes", "database_size"} else "1",
                                               {"database": database.get("datname", ""),
                                                "db.namespace": database.get("datname", ""),
                                                "db.name": database.get("datname", "")}))
                    key = (str(database.get("datname", "")), vendor)
                    previous = self._previous.get(key)
                    elapsed = collected_at - self._previous_at if self._previous_at is not None else 0
                    if vendor not in {"numbackends", "database_size"} and previous is not None and elapsed > 0:
                        delta = float(database[vendor]) - previous
                        if delta >= 0:
                            metrics.append(VendorMetric(f"pg_stat_database.{vendor}", f"{concept}.rate",
                                delta / elapsed, "By/s" if vendor == "temp_bytes" else "1/s",
                                {"database": database.get("datname", ""),
                                 "db.namespace": database.get("datname", ""),
                                 "db.name": database.get("datname", ""), "derived": "counter_rate"}))
                    self._previous[key] = float(database[vendor])
        for vendor, concept, unit in (("calls", "dependency.queries.calls", "1"),
                                      ("total_exec_time_ms", "dependency.queries.execution_time", "ms"),
                                      ("rows", "dependency.queries.rows", "1")):
            statements = root.get("statements") or {}
            if statements.get(vendor) is not None:
                metrics.append(VendorMetric(f"pg_stat_statements.{vendor}", concept, float(statements[vendor]), unit))
                key = ("pg_stat_statements", vendor)
                previous = self._previous.get(key)
                elapsed = collected_at - self._previous_at if self._previous_at is not None else 0
                if previous is not None and elapsed > 0 and float(statements[vendor]) >= previous:
                    metrics.append(VendorMetric(f"pg_stat_statements.{vendor}", f"{concept}.rate",
                        (float(statements[vendor]) - previous) / elapsed, f"{unit}/s",
                        {"derived": "counter_rate"}))
                self._previous[key] = float(statements[vendor])
        self._previous_at = collected_at
        return normalize_vendor_metrics(self.dependency_id, self.name, metrics, self.normalizer)


class RedisAdapter:
    name = "redis"

    def __init__(self, dependency_id: str, dsn: str, normalizer: Normalizer, queues: list[str] | None = None,
                 streams: list[str] | None = None,
                 queue_stats_key: str = "temporalrca:telemetry:queue_stats",
                 fetch: Callable[[], Awaitable[dict[str, Any]]] | None = None) -> None:
        self.dependency_id, self.dsn, self.normalizer = dependency_id, dsn, normalizer
        self.queues, self.streams = queues or [], streams or []
        self.queue_stats_key = queue_stats_key
        self._fetch_override = fetch
        self._previous: dict[str, float] = {}
        self._previous_at: float | None = None

    async def _fetch(self) -> dict[str, Any]:
        if self._fetch_override:
            return await self._fetch_override()
        try:
            import redis.asyncio as redis  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("install temporalrca-agent[redis] for Redis collection") from error
        client = redis.from_url(self.dsn, socket_connect_timeout=3, decode_responses=True)
        try:
            started = time.perf_counter()
            info = await client.info()
            latency = time.perf_counter() - started
            depths = {queue: await client.llen(queue) for queue in self.queues}
            stream_depths = {stream: await client.xlen(stream) for stream in self.streams}
            now = time.time()
            queue_oldest_ages: dict[str, float] = {}
            for queue in self.queues:
                age = _created_at_age(await client.lindex(queue, 0), now)
                if age is not None:
                    queue_oldest_ages[queue] = age
            stream_oldest_ages: dict[str, float] = {}
            for stream in self.streams:
                oldest = await client.xrange(stream, min="-", max="+", count=1)
                age = _created_at_age(oldest[0][1] if oldest else None, now)
                if age is not None:
                    stream_oldest_ages[stream] = age
            queue_stats: dict[str, dict[str, float]] = {}
            for field, raw_value in (await client.hgetall(self.queue_stats_key)).items():
                if "|" not in field:
                    continue
                destination, statistic = field.rsplit("|", 1)
                try:
                    queue_stats.setdefault(destination, {})[statistic] = float(raw_value)
                except (TypeError, ValueError):
                    continue
            return {**info, "connect_latency_seconds": latency, "queue_depths": depths,
                    "stream_depths": stream_depths, "queue_oldest_ages": queue_oldest_ages,
                    "stream_oldest_ages": stream_oldest_ages, "queue_stats": queue_stats}
        finally:
            await client.aclose()

    async def collect(self) -> list[TelemetryEvent]:
        collected_at = time.monotonic()
        data = await self._fetch()
        mapping = {
            "connect_latency_seconds": ("dependency.connectivity.latency", "s"),
            "connected_clients": ("dependency.connections", "1"), "used_memory": ("dependency.memory.used", "By"),
            "used_memory_rss": ("dependency.memory.rss", "By"), "total_commands_processed": ("dependency.operations", "1"),
            "total_net_input_bytes": ("dependency.network.received", "By"), "total_net_output_bytes": ("dependency.network.sent", "By"),
            "rejected_connections": ("dependency.connections.rejected", "1"), "evicted_keys": ("dependency.keys.evicted", "1"),
            "expired_keys": ("dependency.keys.expired", "1"), "rdb_last_bgsave_status": ("dependency.persistence.rdb_ok", "1"),
            "master_repl_offset": ("dependency.replication.offset", "By"), "total_error_replies": ("dependency.errors", "1"),
        }
        metrics: list[VendorMetric] = []
        for vendor, (concept, unit) in mapping.items():
            value = data.get(vendor)
            if vendor == "rdb_last_bgsave_status":
                value = 1 if value == "ok" else 0
            if isinstance(value, (int, float)):
                metrics.append(VendorMetric(vendor, concept, float(value), unit))
                if vendor in {"total_commands_processed", "total_net_input_bytes", "total_net_output_bytes",
                              "rejected_connections", "evicted_keys", "expired_keys", "total_error_replies"}:
                    previous = self._previous.get(vendor)
                    elapsed = collected_at - self._previous_at if self._previous_at is not None else 0
                    if previous is not None and elapsed > 0 and float(value) >= previous:
                        metrics.append(VendorMetric(vendor, f"{concept}.rate", (float(value) - previous) / elapsed,
                            "By/s" if unit == "By" else "1/s", {"derived": "counter_rate"}))
                    self._previous[vendor] = float(value)
        role = data.get("role")
        if role:
            metrics.append(VendorMetric("role", "dependency.replication.role", 1, "1", {"role": role}))
        destinations = [
            *( (queue, "queue", "redis_list", "LLEN", data.get("queue_depths", {}).get(queue),
                data.get("queue_oldest_ages", {}).get(queue)) for queue in data.get("queue_depths", {}) ),
            *( (stream, "stream", "redis_stream", "XLEN", data.get("stream_depths", {}).get(stream),
                data.get("stream_oldest_ages", {}).get(stream)) for stream in data.get("stream_depths", {}) ),
        ]
        elapsed = collected_at - self._previous_at if self._previous_at is not None else 0
        for destination, destination_kind, queue_type, depth_vendor, depth, oldest_age in destinations:
            attributes = {"queue": destination, "queue_type": queue_type, "messaging.system": self.name,
                          "messaging.destination.name": destination,
                          "messaging.destination.kind": destination_kind}
            if depth is not None:
                metrics.append(VendorMetric(depth_vendor, "dependency.queue.depth", float(depth), "1", attributes))
            if oldest_age is not None:
                metrics.append(VendorMetric(f"{queue_type}.oldest_item.created_at", "dependency.messaging.oldest_item.age",
                                            float(oldest_age), "s", attributes))
            statistics = data.get("queue_stats", {}).get(destination, {})
            for statistic, concept in (("produced", "dependency.messaging.produced"),
                                       ("consumed", "dependency.messaging.consumed"),
                                       ("failures", "dependency.messaging.failures")):
                value = statistics.get(statistic)
                if value is None:
                    continue
                vendor_name = f"{self.queue_stats_key}.{statistic}"
                metrics.append(VendorMetric(vendor_name, concept, float(value), "1", attributes))
                previous_key = f"messaging:{destination}:{statistic}"
                previous = self._previous.get(previous_key)
                if previous is not None and elapsed > 0 and float(value) >= previous:
                    metrics.append(VendorMetric(vendor_name, f"{concept}.rate", (float(value) - previous) / elapsed,
                                                "1/s", {**attributes, "derived": "counter_rate"}))
                self._previous[previous_key] = float(value)
            processing_seconds = statistics.get("processing_seconds")
            processing_count = statistics.get("processing_count")
            if processing_seconds is not None and processing_count:
                seconds_key, count_key = f"messaging:{destination}:processing_seconds", f"messaging:{destination}:processing_count"
                previous_seconds, previous_count = self._previous.get(seconds_key), self._previous.get(count_key)
                latency = float(processing_seconds) / float(processing_count)
                aggregation = "cumulative_mean"
                if previous_seconds is not None and previous_count is not None:
                    count_delta, seconds_delta = float(processing_count) - previous_count, float(processing_seconds) - previous_seconds
                    if count_delta > 0 and seconds_delta >= 0:
                        latency = seconds_delta / count_delta
                        aggregation = "interval_mean"
                metrics.append(VendorMetric(f"{self.queue_stats_key}.processing_seconds",
                                            "dependency.messaging.processing.latency", latency, "s",
                                            {**attributes, "aggregation": aggregation}))
                self._previous[seconds_key], self._previous[count_key] = float(processing_seconds), float(processing_count)
        self._previous_at = collected_at
        return normalize_vendor_metrics(self.dependency_id, self.name, metrics, self.normalizer)
