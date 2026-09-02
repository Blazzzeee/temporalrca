from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def floor_hour(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return value.replace(minute=0, second=0, microsecond=0)


def floor_day(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


async def ensure_partitions(session: AsyncSession, table: str, timestamps: set[datetime], period: str = "hour") -> None:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    if table not in {"metric_samples", "log_events", "timeline_events", "metric_rollups_5m", "log_rollups_5m"}:
        raise ValueError("unsupported partition table")
    for timestamp in timestamps:
        start = floor_hour(timestamp) if period == "hour" else floor_day(timestamp)
        end = start + (timedelta(hours=1) if period == "hour" else timedelta(days=1))
        suffix = start.strftime("%Y%m%d%H" if period == "hour" else "%Y%m%d")
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:partition))"), {"partition": f"{table}_{suffix}"})
        # Identifiers are selected from an allowlist and a numeric UTC suffix.
        await session.execute(text(
            f"CREATE TABLE IF NOT EXISTS {table}_{suffix} PARTITION OF {table} "
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        ))
