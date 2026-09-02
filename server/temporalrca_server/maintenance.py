from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update

from .config import get_settings
from .database import SessionLocal
from .models import ContainerInstance, Dependency, LogEvent, MaintenanceState, MetricSample, ProcessInstance, ServiceInstance
from .partitions import ensure_partitions, floor_day

LOG = logging.getLogger("temporalrca.maintenance")
LOCK_ID = 0x544D5243


async def rollup_once(now: datetime | None = None) -> bool:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    # Reprocess recent closed buckets, so late/out-of-order samples converge.
    end = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
    async with SessionLocal() as session:
        if session.bind is None or session.bind.dialect.name != "postgresql":
            raise RuntimeError("maintenance requires PostgreSQL")
        locked = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": LOCK_ID})).scalar_one()
        if not locked:
            await session.rollback()
            return False
        try:
            state = await session.get(MaintenanceState, 1, with_for_update=True)
            if state is None:
                oldest_metric = (await session.execute(select(func.min(MetricSample.timestamp)))).scalar_one()
                oldest_log = (await session.execute(select(func.min(LogEvent.timestamp)))).scalar_one()
                oldest = min(x for x in (oldest_metric, oldest_log, end) if x is not None)
                start = oldest.replace(second=0, microsecond=0) - timedelta(minutes=oldest.minute % 5)
                state = MaintenanceState(id=1)
                session.add(state)
            else:
                start = min(state.rolled_through or end, end) - timedelta(minutes=settings.rollup_lateness_minutes)
            days: set[datetime] = set()
            day = floor_day(start)
            while day <= floor_day(end - timedelta(microseconds=1)):
                days.add(day)
                day += timedelta(days=1)
            await ensure_partitions(session, "metric_rollups_5m", days, "day")
            await ensure_partitions(session, "log_rollups_5m", days, "day")
            await session.execute(text("""
                INSERT INTO metric_rollups_5m
                    (bucket, series_id, count, sum, minimum, maximum, average, last, last_timestamp, completed_at)
                SELECT date_bin('5 minutes', timestamp, TIMESTAMPTZ '1970-01-01'), series_id,
                       count(*), sum(value), min(value), max(value), avg(value),
                       (array_agg(value ORDER BY timestamp DESC, event_id DESC))[1],
                       max(timestamp), :completed
                  FROM metric_samples
                 WHERE timestamp >= :start AND timestamp < :end
                 GROUP BY 1, series_id
                ON CONFLICT (bucket, series_id) DO UPDATE SET
                    count=EXCLUDED.count, sum=EXCLUDED.sum, minimum=EXCLUDED.minimum,
                    maximum=EXCLUDED.maximum, average=EXCLUDED.average, last=EXCLUDED.last,
                    last_timestamp=EXCLUDED.last_timestamp, completed_at=EXCLUDED.completed_at
            """), {"start": start, "end": end, "completed": now})
            await session.execute(text("""
                INSERT INTO log_rollups_5m (bucket, host_id, severity, count, completed_at)
                SELECT date_bin('5 minutes', timestamp, TIMESTAMPTZ '1970-01-01'), host_id,
                       COALESCE(severity, 'UNSPECIFIED'), count(*), :completed
                  FROM log_events
                 WHERE timestamp >= :start AND timestamp < :end
                 GROUP BY 1, host_id, COALESCE(severity, 'UNSPECIFIED')
                ON CONFLICT (bucket, host_id, severity) DO UPDATE SET
                    count=EXCLUDED.count, completed_at=EXCLUDED.completed_at
            """), {"start": start, "end": end, "completed": now})
            for model in (ServiceInstance, ContainerInstance, ProcessInstance, Dependency):
                await session.execute(update(model).where(model.active.is_(True), model.lease_expires_at < now).values(active=False))
            state.rolled_through, state.completed_at = end, now
            await apply_retention(session, now, end)
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise


async def apply_retention(session, now: datetime, rolled_through: datetime) -> None:
    settings = get_settings()
    raw_cutoff = now - timedelta(hours=settings.raw_retention_hours)
    rollup_cutoff = now - timedelta(days=settings.rollup_retention_days)
    rows = (await session.execute(text("""
        SELECT parent.relname AS parent, child.relname AS child
          FROM pg_inherits
          JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
          JOIN pg_class child ON pg_inherits.inhrelid = child.oid
         WHERE parent.relname IN ('metric_samples','log_events','metric_rollups_5m','log_rollups_5m')
    """))).all()
    hourly = re.compile(r"^(metric_samples|log_events)_(\d{10})$")
    daily = re.compile(r"^(metric_rollups_5m|log_rollups_5m)_(\d{8})$")
    for parent, child in rows:
        match = hourly.match(child)
        cutoff = raw_cutoff
        fmt, duration = "%Y%m%d%H", timedelta(hours=1)
        if match is None:
            match = daily.match(child)
            cutoff, fmt, duration = rollup_cutoff, "%Y%m%d", timedelta(days=1)
        if match is None:
            continue
        partition_end = datetime.strptime(match.group(2), fmt).replace(tzinfo=timezone.utc) + duration
        # Raw partitions are retained through the lateness window, proving their closed
        # buckets have been included by the repeated rollup pass before removal.
        if parent in {"metric_samples", "log_events"}:
            cutoff -= timedelta(minutes=settings.rollup_lateness_minutes)
        rollup_complete = parent not in {"metric_samples", "log_events"} or rolled_through >= partition_end
        if partition_end <= cutoff and rollup_complete:
            await session.execute(text(f'DROP TABLE IF EXISTS "{child}"'))


async def worker() -> None:
    while True:
        try:
            await rollup_once()
        except Exception:
            LOG.exception("maintenance cycle failed")
        await asyncio.sleep(60)


def run() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(worker())
