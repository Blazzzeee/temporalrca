from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import time
from pathlib import Path
from typing import Any

import asyncpg
import redis.asyncio as redis
import uvicorn
from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app, start_http_server

JOBS = Counter("demo_jobs_total", "Jobs handled", ["role", "status", "job_type", "queue"])
JOB_DURATION = Histogram("demo_job_duration_seconds", "Job processing duration", ["role", "job_type"])
QUEUE_WAIT = Histogram("demo_queue_wait_seconds", "Time spent waiting in Redis", ["queue", "job_type"])
QUEUE = Gauge("demo_queue_depth", "Current Redis queue depth", ["queue"])
IN_FLIGHT = Gauge("demo_jobs_in_flight", "Jobs currently executing", ["role", "job_type"])
EVENTS = Counter("demo_events_total", "Events handled", ["role", "event_type", "status"])
EVENT_STREAM = Gauge("demo_event_stream_depth", "Events retained in the Redis stream", ["stream"])
CRON_RUNS = Counter("demo_cron_runs_total", "Scheduled runs", ["schedule", "status"])
DB_OPERATIONS = Counter("demo_database_operations_total", "Database operations", ["operation", "status"])
DB_DURATION = Histogram("demo_database_operation_duration_seconds", "Database operation duration", ["operation"])
LAST_SUCCESS = Gauge("demo_last_success_timestamp_seconds", "Last successful operation", ["role"])

DEFAULT_QUEUE = "temporalrca:jobs"
PRIORITY_QUEUE = "temporalrca:priority"
SCHEDULED_QUEUE = "temporalrca:scheduled"
EVENT_STREAM_NAME = "temporalrca:events"
QUEUE_STATS_KEY = os.getenv("QUEUE_STATS_KEY", "temporalrca:telemetry:queue_stats")


def emit(event: str, *, severity: str = "INFO", message: str | None = None, **attributes: object) -> None:
    line = json.dumps({
        "timestamp": time.time(),
        "severity": severity,
        "event_type": event,
        "service": os.getenv("SERVICE_NAME", "demo-workload"),
        "message": message or event.replace("_", " "),
        "attributes": attributes,
    })
    print(line, flush=True)
    if destination := os.getenv("LOG_FILE"):
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def make_job(job_type: str, source: str, *, scheduled_for: float | None = None) -> dict[str, object]:
    return {
        "id": os.urandom(8).hex(), "created_at": time.time(), "scheduled_for": scheduled_for,
        "job_type": job_type, "source": source, "value": random.randint(10, 1000), "attempt": 1,
    }


def should_log(sequence: int) -> bool:
    return sequence % max(1, int(os.getenv("LOG_EVERY", "1"))) == 0


def queue_stat_field(destination: str, statistic: str) -> str:
    return f"{destination}|{statistic}"


def seed_queue_stats(pipeline: Any, destination: str) -> None:
    for statistic in ("produced", "consumed", "failures", "processing_seconds", "processing_count"):
        pipeline.hsetnx(QUEUE_STATS_KEY, queue_stat_field(destination, statistic), 0)


async def record_processing(client: redis.Redis, destination: str, duration: float, succeeded: bool) -> None:
    pipeline = client.pipeline(transaction=True)
    seed_queue_stats(pipeline, destination)
    pipeline.hincrby(QUEUE_STATS_KEY, queue_stat_field(destination, "consumed"), 1)
    if not succeeded:
        pipeline.hincrby(QUEUE_STATS_KEY, queue_stat_field(destination, "failures"), 1)
    pipeline.hincrbyfloat(QUEUE_STATS_KEY, queue_stat_field(destination, "processing_seconds"), duration)
    pipeline.hincrby(QUEUE_STATS_KEY, queue_stat_field(destination, "processing_count"), 1)
    await pipeline.execute()


async def connect_redis() -> redis.Redis:
    client = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    while True:
        try:
            await client.ping()
            return client
        except Exception as exc:
            emit("redis_connect_retry", severity="WARNING", error=type(exc).__name__)
            await asyncio.sleep(2)


async def connect_postgres() -> asyncpg.Pool:
    dsn = os.getenv("MONITORED_DATABASE_URL", "postgresql://workload:workload@monitored-postgres/workload")
    while True:
        try:
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=int(os.getenv("DB_POOL_SIZE", "4")))
            async with pool.acquire() as connection:
                await connection.execute("""CREATE TABLE IF NOT EXISTS processed_jobs (
                    id text PRIMARY KEY, processed_at timestamptz DEFAULT now(), value integer NOT NULL)""")
                await connection.execute("ALTER TABLE processed_jobs ADD COLUMN IF NOT EXISTS job_type text NOT NULL DEFAULT 'standard'")
                await connection.execute("ALTER TABLE processed_jobs ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'unknown'")
                await connection.execute("""CREATE TABLE IF NOT EXISTS processed_events (
                    id text PRIMARY KEY, event_type text NOT NULL, processed_at timestamptz DEFAULT now(), payload jsonb NOT NULL)""")
            return pool
        except Exception as exc:
            emit("postgres_connect_retry", severity="WARNING", error=type(exc).__name__)
            await asyncio.sleep(2)


async def enqueue(client: redis.Redis, queue: str, job: dict[str, object], role: str) -> int:
    pipeline = client.pipeline(transaction=True)
    seed_queue_stats(pipeline, queue)
    pipeline.rpush(queue, json.dumps(job, separators=(",", ":")))
    pipeline.hincrby(QUEUE_STATS_KEY, queue_stat_field(queue, "produced"), 1)
    depth = int((await pipeline.execute())[-2])
    job_type = str(job["job_type"])
    JOBS.labels(role, "queued", job_type, queue).inc()
    QUEUE.labels(queue).set(depth)
    return depth


async def producer(*, role: str, queue: str, job_type: str) -> None:
    client = await connect_redis()
    interval = float(os.getenv("WORK_INTERVAL", "0.75"))
    sequence = 0
    while True:
        sequence += 1
        job = make_job(job_type, role)
        depth = await enqueue(client, queue, job, role)
        LAST_SUCCESS.labels(role).set(time.time())
        if should_log(sequence):
            emit("job_queued", job_id=job["id"], job_type=job_type, queue=queue, queue_depth=depth)
        await asyncio.sleep(interval * random.uniform(0.75, 1.25))


async def burst_producer() -> None:
    client = await connect_redis()
    interval = float(os.getenv("WORK_INTERVAL", "8"))
    batch_size = int(os.getenv("BATCH_SIZE", "20"))
    while True:
        started = time.monotonic()
        depths = [await enqueue(client, DEFAULT_QUEUE, make_job("batch", "burst-producer"), "burst-producer") for _ in range(batch_size)]
        LAST_SUCCESS.labels("burst-producer").set(time.time())
        emit("job_batch_queued", batch_size=batch_size, queue=DEFAULT_QUEUE, queue_depth=depths[-1], duration_ms=round((time.monotonic() - started) * 1000, 2))
        await asyncio.sleep(interval)


async def consumer(*, role: str, queue: str) -> None:
    client = await connect_redis()
    pool = await connect_postgres()
    sequence = 0
    while True:
        item = await client.blpop(queue, timeout=2)
        if not item:
            QUEUE.labels(queue).set(0)
            continue
        sequence += 1
        started = time.monotonic()
        job = json.loads(item[1])
        job_type = str(job.get("job_type", "standard"))
        IN_FLIGHT.labels(role, job_type).inc()
        queue_wait = max(0.0, time.time() - float(job.get("created_at", time.time())))
        succeeded = False
        try:
            if scheduled_for := job.get("scheduled_for"):
                if (delay := float(scheduled_for) - time.time()) > 0:
                    await asyncio.sleep(min(delay, 5))
            if random.random() < float(os.getenv("FAILURE_RATE", "0")):
                raise RuntimeError("simulated_processing_failure")
            await asyncio.sleep(random.uniform(0.01, float(os.getenv("MAX_PROCESSING_SECONDS", "0.12"))))
            async with pool.acquire() as connection:
                await connection.execute(
                    "INSERT INTO processed_jobs(id, value, job_type, source) VALUES($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                    job["id"], int(job["value"]), job_type, str(job.get("source", role)),
                )
            JOBS.labels(role, "ok", job_type, queue).inc()
            QUEUE_WAIT.labels(queue, job_type).observe(queue_wait)
            LAST_SUCCESS.labels(role).set(time.time())
            succeeded = True
            if should_log(sequence):
                emit("job_processed", job_id=job["id"], job_type=job_type, queue=queue, queue_wait_ms=round(queue_wait * 1000, 2))
        except Exception as exc:
            JOBS.labels(role, "error", job_type, queue).inc()
            emit("job_failed", severity="ERROR", job_id=job.get("id"), job_type=job_type, queue=queue, error=type(exc).__name__)
        finally:
            IN_FLIGHT.labels(role, job_type).dec()
            duration = time.monotonic() - started
            JOB_DURATION.labels(role, job_type).observe(duration)
            try:
                await record_processing(client, queue, duration, succeeded)
            except Exception as exc:
                emit("queue_telemetry_failed", severity="WARNING", queue=queue, error=type(exc).__name__)
            QUEUE.labels(queue).set(await client.llen(queue))


async def scheduler(*, role: str, schedule: str, interval: float, job_type: str) -> None:
    client = await connect_redis()
    await asyncio.sleep(random.uniform(0, min(interval, 3)))
    while True:
        started = time.monotonic()
        CRON_RUNS.labels(schedule, "started").inc()
        emit("cron_started", schedule=schedule, cadence_seconds=interval)
        try:
            job = make_job(job_type, role, scheduled_for=time.time())
            depth = await enqueue(client, SCHEDULED_QUEUE, job, role)
            CRON_RUNS.labels(schedule, "ok").inc()
            LAST_SUCCESS.labels(role).set(time.time())
            emit("cron_completed", schedule=schedule, job_id=job["id"], queue_depth=depth, duration_ms=round((time.monotonic() - started) * 1000, 2))
        except Exception as exc:
            CRON_RUNS.labels(schedule, "error").inc()
            emit("cron_failed", severity="ERROR", schedule=schedule, error=type(exc).__name__)
        await asyncio.sleep(interval)


async def event_publisher() -> None:
    client = await connect_redis()
    interval = float(os.getenv("WORK_INTERVAL", "2.5"))
    event_types = ("order.created", "payment.authorized", "inventory.changed", "shipment.updated")
    while True:
        event_type, event_id, created_at = random.choice(event_types), os.urandom(8).hex(), time.time()
        pipeline = client.pipeline(transaction=True)
        seed_queue_stats(pipeline, EVENT_STREAM_NAME)
        pipeline.xadd(EVENT_STREAM_NAME, {"event_id": event_id, "event_type": event_type, "created_at": str(created_at), "value": str(random.randint(1, 1000))}, maxlen=10_000, approximate=True)
        pipeline.hincrby(QUEUE_STATS_KEY, queue_stat_field(EVENT_STREAM_NAME, "produced"), 1)
        pipeline.xlen(EVENT_STREAM_NAME)
        depth = int((await pipeline.execute())[-1])
        EVENTS.labels("event-publisher", event_type, "published").inc()
        EVENT_STREAM.labels(EVENT_STREAM_NAME).set(depth)
        LAST_SUCCESS.labels("event-publisher").set(time.time())
        emit("domain_event_published", event_id=event_id, domain_event=event_type, stream=EVENT_STREAM_NAME, stream_depth=depth)
        await asyncio.sleep(interval * random.uniform(0.8, 1.2))


async def event_worker() -> None:
    client = await connect_redis()
    pool = await connect_postgres()
    cursor = "$"
    while True:
        for _, messages in await client.xread({EVENT_STREAM_NAME: cursor}, count=20, block=2000):
            for message_id, payload in messages:
                cursor = message_id
                event_type, started = str(payload.get("event_type", "unknown")), time.monotonic()
                succeeded = False
                try:
                    if random.random() < float(os.getenv("FAILURE_RATE", "0")):
                        raise RuntimeError("simulated_event_failure")
                    async with pool.acquire() as connection:
                        await connection.execute(
                            "INSERT INTO processed_events(id, event_type, payload) VALUES($1, $2, $3::jsonb) ON CONFLICT DO NOTHING",
                            str(payload.get("event_id", message_id)), event_type, json.dumps(payload),
                        )
                    EVENTS.labels("event-worker", event_type, "processed").inc()
                    DB_OPERATIONS.labels("event_insert", "ok").inc()
                    LAST_SUCCESS.labels("event-worker").set(time.time())
                    succeeded = True
                    emit("domain_event_processed", event_id=payload.get("event_id"), domain_event=event_type, stream_id=message_id, duration_ms=round((time.monotonic() - started) * 1000, 2))
                except Exception as exc:
                    EVENTS.labels("event-worker", event_type, "error").inc()
                    DB_OPERATIONS.labels("event_insert", "error").inc()
                    emit("domain_event_failed", severity="ERROR", event_id=payload.get("event_id"), domain_event=event_type, error=type(exc).__name__)
                finally:
                    try:
                        await record_processing(client, EVENT_STREAM_NAME, time.monotonic() - started, succeeded)
                    except Exception as exc:
                        emit("queue_telemetry_failed", severity="WARNING", queue=EVENT_STREAM_NAME, error=type(exc).__name__)
        EVENT_STREAM.labels(EVENT_STREAM_NAME).set(await client.xlen(EVENT_STREAM_NAME))


async def database_worker() -> None:
    pool = await connect_postgres()
    interval = float(os.getenv("WORK_INTERVAL", "12"))
    while True:
        started = time.monotonic()
        try:
            async with pool.acquire() as connection:
                row = await connection.fetchrow("""SELECT count(*) AS jobs,
                    count(*) FILTER (WHERE processed_at > now() - interval '1 minute') AS recent,
                    COALESCE(avg(value), 0) AS average_value FROM processed_jobs""")
            DB_OPERATIONS.labels("analytics_query", "ok").inc()
            DB_DURATION.labels("analytics_query").observe(time.monotonic() - started)
            LAST_SUCCESS.labels("database-worker").set(time.time())
            emit("database_snapshot", jobs=int(row["jobs"]), recent_jobs=int(row["recent"]), average_value=round(float(row["average_value"]), 2))
        except Exception as exc:
            DB_OPERATIONS.labels("analytics_query", "error").inc()
            emit("database_snapshot_failed", severity="ERROR", error=type(exc).__name__)
        await asyncio.sleep(interval)


async def file_processor() -> None:
    root = Path(os.getenv("WORK_DIR", "/tmp/workload"))
    root.mkdir(parents=True, exist_ok=True)
    sequence = 0
    while True:
        sequence += 1
        path = root / f"input-{time.time_ns()}.dat"
        payload = os.urandom(int(os.getenv("FILE_SIZE", "65536")))
        path.write_bytes(payload)
        checksum = sum(payload) % 65536
        await asyncio.to_thread(path.unlink)
        JOBS.labels("file-processor", "ok", "file", "filesystem").inc()
        LAST_SUCCESS.labels("file-processor").set(time.time())
        if should_log(sequence):
            emit("file_processed", bytes=len(payload), checksum=checksum)
        await asyncio.sleep(float(os.getenv("WORK_INTERVAL", "1")))


def serve() -> None:
    app = FastAPI(title="TemporalRCA demo workload")
    app.mount("/metrics", make_asgi_app())

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "9100")), log_config=None)


def operation_for(role: str) -> Any:
    return {
        "producer": lambda: producer(role="producer", queue=DEFAULT_QUEUE, job_type="standard"),
        "burst-producer": burst_producer,
        "priority-producer": lambda: producer(role="priority-producer", queue=PRIORITY_QUEUE, job_type="priority"),
        "consumer": lambda: consumer(role="consumer", queue=DEFAULT_QUEUE),
        "consumer-b": lambda: consumer(role="consumer-b", queue=DEFAULT_QUEUE),
        "priority-consumer": lambda: consumer(role="priority-consumer", queue=PRIORITY_QUEUE),
        "scheduled-consumer": lambda: consumer(role="scheduled-consumer", queue=SCHEDULED_QUEUE),
        "report-cron": lambda: scheduler(role="report-cron", schedule="report-refresh", interval=float(os.getenv("CRON_INTERVAL", "15")), job_type="scheduled-report"),
        "cleanup-cron": lambda: scheduler(role="cleanup-cron", schedule="cleanup-scan", interval=float(os.getenv("CRON_INTERVAL", "30")), job_type="scheduled-cleanup"),
        "event-publisher": event_publisher,
        "event-worker": event_worker,
        "database-worker": database_worker,
        "file-processor": file_processor,
    }[role]


async def run(role: str) -> None:
    start_http_server(int(os.getenv("PORT", "9100")))
    loop, stop = asyncio.get_running_loop(), asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(operation_for(role)())
    await stop.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


ROLES = (
    "producer", "burst-producer", "priority-producer", "consumer", "consumer-b", "priority-consumer",
    "scheduled-consumer", "report-cron", "cleanup-cron", "event-publisher", "event-worker",
    "database-worker", "file-processor", "metrics",
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=ROLES)
    args = parser.parse_args()
    serve() if args.role == "metrics" else asyncio.run(run(args.role))
