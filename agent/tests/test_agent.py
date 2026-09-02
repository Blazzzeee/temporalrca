import json
import os
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from temporalrca_agent.adapters import PostgreSQLAdapter, RedisAdapter, _created_at_age
from temporalrca_agent.collectors.logs import FileLogCollector, JournalCollector
from temporalrca_agent.collectors.docker import (DockerCollector, aggregate_block_bytes,
    aggregate_network_bytes, container_cpu_utilization, container_stats_metrics)
from temporalrca_agent.collectors.openmetrics import parse_openmetrics
from temporalrca_agent.config import Config, ServiceRule, load_config
from temporalrca_agent.discovery import DiscoveryState, assign_service
from temporalrca_agent.models import SignalType, SourceType
from temporalrca_agent.normalization import Normalizer
from temporalrca_agent.procfs import (counter_rate, cpu_utilization, parse_diskstats, parse_loadavg,
    parse_meminfo, parse_net_dev, parse_proc_stat, parse_process_stat, parse_status)
from temporalrca_agent.spool import SQLiteSpool
from temporalrca_agent.transport import AgentHTTPClient, HTTPResult, backoff_delay, retryable
from temporalrca_agent.runtime import Agent


async def run_inline(function, *args, **kwargs):
    """The restricted host test runner cannot wake an asyncio thread executor."""
    return function(*args, **kwargs)


class ProcParserTests(unittest.TestCase):
    def test_system_parsers(self):
        stat = parse_proc_stat("cpu  10 0 5 85 0 0 0 0 0 0\ncpu0 5 0 3 42 0 0 0 0 0 0\nctxt 99\nprocs_running 2\n")
        self.assertEqual(stat["cpus"]["cpu"]["idle"], 85)
        self.assertEqual(stat["ctxt"], 99)
        self.assertEqual(parse_meminfo("MemTotal: 2 kB\nHugePages_Total: 3\n"), {"MemTotal": 2048, "HugePages_Total": 3})
        self.assertEqual(parse_loadavg("0.1 0.2 0.3 2/44 100")["tasks_total"], 44)
        self.assertEqual(parse_diskstats("8 0 sda 1 2 3 4 5 6 7 8 0 10 11\n")["sda"]["sectors_written"], 7)
        net = parse_net_dev("Inter-| Receive\n face |bytes\n eth0: 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16\n")
        self.assertEqual(net["eth0"]["tx_bytes"], 9)

    def test_process_parser_handles_parentheses(self):
        values = ["S", "1", "0", "0", "0", "0", "0", "7", "0", "9", "0", "11", "12", "0", "0", "0", "0", "17", "0", "19", "20", "21"]
        parsed = parse_process_stat("42 (worker ) special) " + " ".join(values))
        self.assertEqual(parsed["name"], "worker ) special")
        self.assertEqual(parsed["start_time_ticks"], 19)
        self.assertEqual(parsed["rss_pages"], 21)

    def test_status_deltas_resets_and_cpu(self):
        status = parse_status("Name:\tx\nVmRSS:\t12 kB\nThreads:\t4\nvoluntary_ctxt_switches:\t8\n")
        self.assertEqual(status["VmRSS"], 12 * 1024)
        self.assertEqual(counter_rate(120, 100, 2).value, 10)
        self.assertTrue(counter_rate(2, 100, 2).reset)
        self.assertIsNone(counter_rate(2, None, 2).value)
        self.assertEqual(cpu_utilization({"user": 20, "idle": 180}, {"user": 10, "idle": 90}), 10)
        self.assertIsNone(cpu_utilization({"user": 1, "idle": 1}, {"user": 2, "idle": 2}))


class DockerCollectorTests(unittest.TestCase):
    def test_stats_aggregate_deltas_and_counter_resets(self):
        first = {
            "cpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000, "online_cpus": 2},
            "precpu_stats": {}, "memory_stats": {"usage": 1200, "limit": 2000,
                                                     "stats": {"inactive_file": 200}},
            "networks": {"eth0": {"rx_bytes": 100, "tx_bytes": 200}, "eth1": {"rx_bytes": 5, "tx_bytes": 7}},
            "blkio_stats": {"io_service_bytes_recursive": [{"op": "Read", "value": 300},
                                                               {"op": "Write", "value": 400}]},
        }
        second = {
            "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 1400, "online_cpus": 2},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
            "memory_stats": {"usage": 1500, "limit": 2000},
            "networks": {"eth0": {"rx_bytes": 140, "tx_bytes": 230}},
            "blkio_stats": {"io_service_bytes_recursive": [{"op": "Read", "value": 500},
                                                               {"op": "Write", "value": 450}]},
        }
        self.assertEqual(aggregate_network_bytes(first["networks"]), {"rx_bytes": 105, "tx_bytes": 207})
        self.assertEqual(aggregate_block_bytes(first["blkio_stats"]), {"read_bytes": 300, "write_bytes": 400})
        self.assertEqual(container_cpu_utilization(second), 100.0)
        values = container_stats_metrics(second, first, 2)
        self.assertEqual(values["memory_usage"], 1500)
        self.assertEqual(values["rx_bytes_rate"], 17.5)
        self.assertEqual(values["write_bytes_rate"], 25)
        reset = {**second, "networks": {"eth0": {"rx_bytes": 1, "tx_bytes": 2}}}
        self.assertIsNone(container_stats_metrics(reset, second, 2)["rx_bytes_rate"])

    def test_collector_maps_container_and_keeps_stopped_inventory(self):
        class Client:
            def list_containers(self):
                return [{"Id": "a" * 64, "Names": ["/running"], "Image": "demo:1", "State": "running",
                         "Status": "Up 2 minutes", "Labels": {"com.docker.compose.service": "demo"}},
                        {"Id": "b" * 64, "Names": ["/stopped"], "Image": "demo:1", "State": "exited",
                         "Status": "Exited (0)", "Labels": {}}]
            def stats(self, container_id):
                return {"cpu_stats": {"cpu_usage": {"total_usage": 10}, "system_cpu_usage": 100,
                                       "online_cpus": 1}, "precpu_stats": {}, "memory_stats": {"usage": 10,
                                       "limit": 100}, "networks": {}, "blkio_stats": {}}
        collector = DockerCollector("/unused", Normalizer("host"), client=Client())
        events = collector.collect({"a" * 64: "container-uuid"})
        self.assertIn("b" * 64, collector.latest_inventory)
        self.assertTrue(events)
        self.assertTrue(all(event.entity.container_id == "container-uuid" for event in events))

    def test_config_disables_docker_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.toml"
            path.write_text('central_server="http://server"\ndocker_collection_enabled=false\n')
            config = load_config(path)
        self.assertFalse(config.docker_collection_enabled)


class ConfigDiscoveryTests(unittest.TestCase):
    def test_config_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.toml"
            path.write_text('central_server="http://server/"\n[[services]]\nservice="api"\ncommand_regex="api.*run"\n')
            with patch.dict(os.environ, {"TEMPORALRCA_ENROLLMENT_TOKEN": "secret",
                                         "TEMPORALRCA_COLLECTION_INTERVAL_SECONDS": "0.5"}):
                config = load_config(path)
        self.assertEqual(config.central_server, "http://server")
        self.assertEqual(config.enrollment_token, "secret")
        self.assertEqual(config.collection_interval_seconds, 0.5)

    def test_server_url_can_be_overridden_for_remote_workloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.toml"
            path.write_text('central_server="http://api:8000"\n')
            with patch.dict(os.environ, {"TEMPORALRCA_SERVER_URL": "http://192.0.2.11:8000"}):
                config = load_config(path)
        self.assertEqual(config.central_server, "http://192.0.2.11:8000")

    def test_container_environment_creates_node_service_collectors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.toml"
            path.write_text('central_server="http://server"\ndocker_collection_enabled=false\n')
            environment = {
                "TEMPORALRCA_HOST_NAME": "producer",
                "TEMPORALRCA_INSTALLATION_ID": "compose-workload:producer",
                "TEMPORALRCA_NODE_KIND": "container",
                "TEMPORALRCA_SERVICE_NAME": "producer",
                "TEMPORALRCA_METRICS_URL": "http://127.0.0.1:9100/metrics",
                "TEMPORALRCA_LOG_PATH": "/var/log/temporalrca/demo.jsonl",
            }
            with patch.dict(os.environ, environment, clear=False):
                config = load_config(path)
        self.assertEqual(config.host_name, "producer")
        self.assertEqual(config.installation_id, "compose-workload:producer")
        self.assertEqual(config.host_attributes["node_kind"], "container")
        self.assertEqual([item.service for item in config.services], ["producer"])
        self.assertEqual([item.service for item in config.metrics], ["producer"])
        self.assertEqual([item.service for item in config.logs], ["producer"])

    def test_validation(self):
        with self.assertRaises(ValueError):
            Config("server")
        with self.assertRaises(ValueError):
            ServiceRule("empty")
        with self.assertRaises(Exception):
            ServiceRule("bad", command_regex="[")

    def test_precedence_and_pid_reuse(self):
        rules = [ServiceRule("first", command_regex="worker"), ServiceRule("second", executable="/bin/worker")]
        process = {"pid": 9, "start_time_ticks": 100, "cmdline": "worker --run", "exe": "/bin/worker", "cgroup": ""}
        self.assertEqual(assign_service(rules, process), "first")
        state = DiscoveryState()
        self.assertEqual(state.reconcile("boot", [process], rules)[0].kind, "process.started")
        reused = {**process, "start_time_ticks": 200}
        self.assertEqual({change.kind for change in state.reconcile("boot", [reused], rules)}, {"process.started", "process.stopped"})


class SpoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.thread_patch = patch("asyncio.to_thread", side_effect=run_inline)
        self.thread_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "spool.db"

    async def asyncTearDown(self):
        self.temporary.cleanup()
        self.thread_patch.stop()

    @staticmethod
    def event(number, padding=0):
        return {"event_id": f"event-{number}", "name": "metric", "padding": "x" * padding}

    async def test_recovery_ack_and_deduplication(self):
        spool = SQLiteSpool(self.path)
        await spool.open()
        await spool.append([self.event(1), self.event(1), self.event(2)], {"atomic-cursor": "42"})
        self.assertEqual(await spool.get_state("atomic-cursor"), "42")
        self.assertEqual([record.event_id for record in await spool.batch()], ["event-1", "event-2"])
        self.assertEqual([record.event_id for record in await spool.batch(max_events=1, newest=True)], ["event-2"])
        await spool.close()
        reopened = SQLiteSpool(self.path)
        await reopened.open()
        await reopened.acknowledge(["event-1"])
        self.assertEqual([record.event_id for record in await reopened.batch()], ["event-2"])
        await reopened.close()

    async def test_quarantine_state_capacity_and_age(self):
        spool = SQLiteSpool(self.path, max_bytes=100, max_age_seconds=3600)
        await spool.open()
        evicted = await spool.append([self.event(1, 100), self.event(2, 100)])
        self.assertTrue(evicted)
        self.assertLessEqual((await spool.usage())["bytes"], 100)
        await spool.append([self.event(3)])
        await spool.quarantine({"event-3": "invalid unit"})
        self.assertEqual((await spool.usage())["quarantine"], 1)
        await spool.append([self.event(4)])
        spool.db.execute("UPDATE events SET created=?", (time.time() - 4000,))
        spool.db.commit()
        self.assertEqual(spool._enforce_limits_sync()[0]["event_id"], "event-4")
        await spool.set_state("cursor", "abc")
        self.assertEqual(await spool.get_state("cursor"), "abc")
        await spool.close()


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.thread_patch = patch("asyncio.to_thread", side_effect=run_inline)
        self.thread_patch.start()

    async def asyncTearDown(self):
        self.thread_patch.stop()

    def test_openmetrics(self):
        sample = parse_openmetrics('# TYPE requests_total counter\n# UNIT requests requests\nrequests_total{method="GET"} 12 1234\n# EOF\n')[0]
        self.assertEqual(sample["labels"], {"method": "GET"})
        self.assertEqual(sample["metric_type"], "counter")

    async def test_file_partial_json_rotation_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = SQLiteSpool(root / "spool.db")
            await spool.open()
            log = root / "events.log"
            log.write_bytes(b'{"level":"error","message":"bad"}\npartial')
            collector = FileLogCollector("events", log, Normalizer("host"), spool)
            first = await collector.collect()
            collector.commit_state_update()
            self.assertEqual((first[0].severity, first[0].message), ("ERROR", "bad"))
            with log.open("ab") as handle:
                handle.write(b" line\nnot json\n")
            second = await collector.collect()
            collector.commit_state_update()
            self.assertEqual([item.message for item in second], ["partial line", "not json"])
            log.rename(root / "old.log")
            log.write_text("new file\n")
            rotated_events = await collector.collect()
            collector.commit_state_update()
            self.assertEqual(rotated_events[0].message, "new file")
            log.write_text("short\n")
            truncated_events = await collector.collect()
            collector.commit_state_update()
            self.assertEqual(truncated_events[0].message, "short")
            limited = FileLogCollector("limited", log, Normalizer("host"), spool, max_line_bytes=3)
            log.write_bytes(b"abcdef")
            self.assertEqual(await limited.collect(), [])
            limited.commit_state_update()
            with log.open("ab") as handle:
                handle.write(b"\n")
            oversized = await limited.collect()
            self.assertTrue(oversized[0].attributes["truncated"])
            await spool.close()

    async def test_adapters(self):
        async def pg_query():
            return [{"connect_latency_seconds": .01, "waiting_locks": 2,
                     "databases": [{"datname": "app", "numbackends": 3, "deadlocks": 1, "temp_bytes": 4}]}]
        pg_events = await PostgreSQLAdapter("pg", "unused", Normalizer("host"), query=pg_query).collect()
        pg = {event.name: event for event in pg_events}
        self.assertEqual(pg["dependency.connections"].attributes["vendor_metric_name"], "pg_stat_database.numbackends")
        self.assertTrue(all("query" not in event.attributes for event in pg_events))
        self.assertTrue(all(event.attributes["dependency.system"] == "postgresql" for event in pg_events))
        database_attributes = pg["dependency.connections"].attributes
        self.assertEqual(database_attributes["db.system"], "postgresql")
        self.assertEqual(database_attributes["db.namespace"], "app")
        self.assertEqual(database_attributes["db.name"], "app")

    async def test_redis_destination_telemetry_uses_shared_messaging_contract(self):
        samples = iter([
            {"connected_clients": 3, "rdb_last_bgsave_status": "err", "role": "replica",
             "queue_depths": {"jobs": 7}, "stream_depths": {"events": 11},
             "queue_oldest_ages": {"jobs": 12.5}, "stream_oldest_ages": {"events": 8.0},
             "queue_stats": {
                 "jobs": {"produced": 100, "consumed": 80, "failures": 5,
                           "processing_seconds": 2.0, "processing_count": 20},
                 "events": {"produced": 40, "consumed": 35, "failures": 1,
                             "processing_seconds": 1.0, "processing_count": 10},
             }},
            {"connected_clients": 4, "queue_depths": {"jobs": 9}, "stream_depths": {"events": 13},
             "queue_oldest_ages": {"jobs": 14.0}, "stream_oldest_ages": {"events": 10.0},
             "queue_stats": {
                 "jobs": {"produced": 105, "consumed": 82, "failures": 7,
                           "processing_seconds": 3.0, "processing_count": 30},
                 "events": {"produced": 44, "consumed": 39, "failures": 2,
                             "processing_seconds": 1.8, "processing_count": 18},
             }},
            {"connected_clients": 4, "queue_depths": {"jobs": 1}, "stream_depths": {"events": 2},
             "queue_oldest_ages": {"jobs": 1.0}, "stream_oldest_ages": {"events": 2.0},
             "queue_stats": {
                 "jobs": {"produced": 2, "consumed": 2, "failures": 0,
                           "processing_seconds": .2, "processing_count": 2},
                 "events": {"produced": 1, "consumed": 1, "failures": 0,
                             "processing_seconds": .3, "processing_count": 1},
             }},
        ])

        async def redis_fetch():
            return next(samples)

        adapter = RedisAdapter("redis", "unused", Normalizer("host"), queues=["jobs"], streams=["events"],
                               fetch=redis_fetch)
        with patch("temporalrca_agent.adapters.time.monotonic", side_effect=[100.0, 102.0, 104.0]):
            first = await adapter.collect()
            second = await adapter.collect()
            reset = await adapter.collect()

        def metric(events, name, destination):
            return next(event for event in events
                        if event.name == name
                        and event.attributes.get("messaging.destination.name") == destination)

        queue_depth = metric(first, "dependency.queue.depth", "jobs")
        stream_depth = metric(first, "dependency.queue.depth", "events")
        self.assertEqual(queue_depth.value, 7)
        self.assertEqual(stream_depth.value, 11)
        self.assertEqual(metric(first, "dependency.messaging.oldest_item.age", "jobs").value, 12.5)
        self.assertEqual(metric(first, "dependency.messaging.oldest_item.age", "events").value, 8.0)
        self.assertEqual(next(event for event in first if event.name == "dependency.persistence.rdb_ok").value, 0)

        for event in (queue_depth, metric(first, "dependency.messaging.produced", "jobs"), stream_depth):
            self.assertEqual(event.attributes["dependency.system"], "redis")
            self.assertEqual(event.attributes["messaging.system"], "redis")
            self.assertEqual(event.attributes["messaging.destination.name"], event.attributes["queue"])
        self.assertEqual(queue_depth.attributes["messaging.destination.kind"], "queue")
        self.assertEqual(stream_depth.attributes["messaging.destination.kind"], "stream")

        for destination, totals, rates in (
            ("jobs", (100, 80, 5), (2.5, 1.0, 1.0)),
            ("events", (40, 35, 1), (2.0, 2.0, 0.5)),
        ):
            for statistic, value in zip(("produced", "consumed", "failures"), totals, strict=True):
                self.assertEqual(metric(first, f"dependency.messaging.{statistic}", destination).value, value)
            for statistic, value in zip(("produced", "consumed", "failures"), rates, strict=True):
                self.assertAlmostEqual(metric(second, f"dependency.messaging.{statistic}.rate", destination).value, value)

            processing = metric(first, "dependency.messaging.processing.latency", destination)
            self.assertAlmostEqual(processing.value, 0.1)
            self.assertEqual(processing.attributes["aggregation"], "cumulative_mean")
            processing = metric(second, "dependency.messaging.processing.latency", destination)
            self.assertAlmostEqual(processing.value, 0.1)
            self.assertEqual(processing.attributes["aggregation"], "interval_mean")

        self.assertFalse(any(event.name.endswith(".rate") for event in first))
        self.assertEqual(metric(second, "dependency.queue.depth", "events").value, 13)
        self.assertEqual(metric(second, "dependency.messaging.oldest_item.age", "events").value, 10.0)
        self.assertFalse(any(event.name.endswith(".rate") for event in reset))
        reset_latency = metric(reset, "dependency.messaging.processing.latency", "jobs")
        self.assertAlmostEqual(reset_latency.value, 0.1)
        self.assertEqual(reset_latency.attributes["aggregation"], "cumulative_mean")

    def test_oldest_item_age_ignores_valid_non_object_json(self):
        self.assertIsNone(_created_at_age("[]", 100.0))
        self.assertIsNone(_created_at_age('"text"', 100.0))
        self.assertIsNone(_created_at_age("not-json", 100.0))
        self.assertEqual(_created_at_age(None, 100.0), 0.0)
        self.assertEqual(_created_at_age('{"created_at": 95}', 100.0), 5.0)

    async def test_journal_cursor_is_advanced_with_emitted_event(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = SQLiteSpool(Path(directory) / "spool.db")
            await spool.open()
            await spool.set_state("journal:worker", "old-cursor")
            record = {"__CURSOR": "new-cursor", "__REALTIME_TIMESTAMP": "1600000000000000",
                      "PRIORITY": "3", "MESSAGE": "failed"}
            class Process:
                returncode = 0
                async def communicate(self):
                    return json.dumps(record).encode() + b"\n", b""
            async def subprocess(*command, **kwargs):
                self.assertIn("--after-cursor=old-cursor", command)
                return Process()
            collector = JournalCollector("worker", "worker.service", Normalizer("host"), spool)
            with patch("asyncio.create_subprocess_exec", side_effect=subprocess):
                events = await collector.collect()
            update = collector.pending_state_update()
            self.assertEqual(update, ("journal:worker", "new-cursor"))
            await spool.append([event.as_dict() for event in events], dict([update]))
            collector.commit_state_update()
            self.assertEqual(await spool.get_state("journal:worker"), "new-cursor")
            self.assertEqual(events[0].message, "failed")
            await spool.close()


class NormalizationTransportTests(unittest.TestCase):
    def test_normalizer_and_retry_policy(self):
        event = Normalizer("host").event(source=SourceType.SYSTEM, signal=SignalType.METRIC, name="cpu",
                 timestamp=datetime(2020, 1, 1, tzinfo=UTC), value=1, attributes={"bad key": "x" * 3000}).as_dict()
        self.assertEqual(event["host_id"], "host")
        self.assertEqual(len(event["attributes"]["bad_key"]), 2048)
        self.assertNotIn("entity", event)
        self.assertTrue(all(retryable(status) for status in (None, 401, 403, 408, 429, 500, 503)))
        self.assertFalse(retryable(400))
        self.assertEqual(backoff_delay(4, base=.5, random_value=.5), 4)
        self.assertEqual(backoff_delay(20, cap=30, random_value=1), 30)


class TransportContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.thread_patch = patch("asyncio.to_thread", side_effect=run_inline)
        self.thread_patch.start()

    async def asyncTearDown(self):
        self.thread_patch.stop()

    async def test_registration_and_deterministic_batch_contract(self):
        requests = []
        client = AgentHTTPClient("http://server")
        def request(method, path, body, **kwargs):
            requests.append((method, path, body, kwargs))
            if path.endswith("register"):
                return HTTPResult(201, {"agent_id": "a", "host_id": "h", "credential": "credential"})
            return HTTPResult(200, {"accepted_event_ids": [body["events"][0]["event_id"]]})
        client._request = request
        await client.register("enroll", "installation", "host", {"node_kind": "container"})
        registration = requests[-1][2]
        self.assertEqual(registration["enrollment_token"], "enroll")
        self.assertEqual(registration["host_external_id"], "installation")
        self.assertEqual(registration["host_attributes"], {"node_kind": "container"})
        event = Normalizer("00000000-0000-0000-0000-000000000001").event(
            source=SourceType.SYSTEM, signal=SignalType.METRIC, name="cpu", value=1).as_dict()
        await client.send([event])
        first = requests[-1][2]
        await client.send([event])
        second = requests[-1][2]
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], "1.0")
        self.assertEqual(first["first_sequence"], event["sequence"])
        self.assertEqual(requests[-1][3]["timeout"], 60.0)


class RuntimeDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.thread_patch = patch("asyncio.to_thread", side_effect=run_inline)
        self.thread_patch.start()
        self.temporary = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.temporary.cleanup()
        self.thread_patch.stop()

    async def test_inventory_maps_server_ids_and_reports_permission_failure(self):
        process = {"pid": 1, "start_time_ticks": 20, "name": "api", "cmdline": "api run", "exe": "/bin/api",
                   "cgroup": "0::/docker/abcdefabcdef", "state": "S"}
        class FakeProc:
            def boot_id(self): return "00000000-0000-0000-0000-000000000001"
            def pids(self): return [1, 2]
            def process(self, pid):
                if pid == 2: raise PermissionError("hidden")
                return process
        class FakeClient:
            credential = "credential"
            async def inventory(self, body):
                self.body = body
                key = body["processes"][0]["external_id"]
                return {"service_ids": {"api": "00000000-0000-0000-0000-000000000002"},
                        "process_ids": {key: "00000000-0000-0000-0000-000000000003"},
                        "container_ids": {"abcdefabcdef": "00000000-0000-0000-0000-000000000004"},
                        "dependency_ids": {}}
        client = FakeClient()
        config = Config("http://server", credential="credential", state_dir=Path(self.temporary.name),
                        services=[ServiceRule("api", command_regex="api")])
        agent = Agent(config, proc=FakeProc(), client=client)
        await agent.spool.open()
        await agent.discover()
        self.assertEqual(client.body["processes"][0]["container_external_id"], "abcdefabcdef")
        self.assertIn("api", agent.resource_ids["services"])
        records = await agent.spool.batch()
        names = {record.payload["name"] for record in records}
        self.assertEqual(names, {"process.started", "collector.process.permission_denied"})
        await agent.spool.close()

    async def test_container_inventory_is_not_limited_by_process_sampling_cap(self):
        class FakeProc:
            def boot_id(self): return "00000000-0000-0000-0000-000000000001"
            def pids(self): return [1, 2]
            def process(self, pid):
                return {"pid": pid, "start_time_ticks": 20 + pid, "name": f"worker-{pid}",
                        "cmdline": f"worker {pid}", "exe": "/bin/worker",
                        "cgroup": f"0::/docker/{'a' if pid == 1 else 'b'}bcdefabcdef", "state": "S"}
        class FakeClient:
            credential = "credential"
            async def inventory(self, body):
                self.body = body
                return {"service_ids": {}, "process_ids": {}, "container_ids": {}, "dependency_ids": {}}
        client = FakeClient()
        config = Config("http://server", credential="credential", state_dir=Path(self.temporary.name),
                        max_monitored_processes=1)
        agent = Agent(config, proc=FakeProc(), client=client)
        await agent.spool.open()
        await agent.discover()
        self.assertEqual(len(client.body["processes"]), 1)
        self.assertEqual({item["external_id"] for item in client.body["containers"]},
                         {"abcdefabcdef", "bbcdefabcdef"})
        await agent.spool.close()


if __name__ == "__main__":
    unittest.main()
