import asyncio
import json

from workload.__main__ import ROLES, emit, make_job, operation_for, queue_stat_field, record_processing


def test_emit_is_structured(capsys):
    emit("job_test", job_id="abc")
    event = json.loads(capsys.readouterr().out)
    assert event["event_type"] == "job_test"
    assert event["attributes"]["job_id"] == "abc"


def test_queue_stat_field_is_stable_for_all_telemetry_counters():
    destination = "temporalrca:events"
    assert [queue_stat_field(destination, statistic) for statistic in
            ("produced", "consumed", "failures", "processing_seconds", "processing_count")] == [
                "temporalrca:events|produced",
                "temporalrca:events|consumed",
                "temporalrca:events|failures",
                "temporalrca:events|processing_seconds",
                "temporalrca:events|processing_count",
            ]


def test_failed_processing_counts_as_consumed_and_failed():
    class Pipeline:
        def __init__(self):
            self.commands = []

        def hsetnx(self, *args):
            self.commands.append(("hsetnx", *args))

        def hincrby(self, *args):
            self.commands.append(("hincrby", *args))

        def hincrbyfloat(self, *args):
            self.commands.append(("hincrbyfloat", *args))

        async def execute(self):
            return []

    class Client:
        def __init__(self):
            self.value = Pipeline()

        def pipeline(self, transaction=True):
            assert transaction
            return self.value

    client = Client()
    asyncio.run(record_processing(client, "jobs", 0.25, False))
    increments = [command[2] for command in client.value.commands if command[0] == "hincrby"]
    assert "jobs|consumed" in increments
    assert "jobs|failures" in increments


def test_expanded_workload_roles_and_job_metadata():
    job = make_job("scheduled-report", "report-cron", scheduled_for=123.0)
    assert job["job_type"] == "scheduled-report"
    assert job["source"] == "report-cron"
    assert job["scheduled_for"] == 123.0
    for role in set(ROLES) - {"metrics"}:
        assert callable(operation_for(role))
