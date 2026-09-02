from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temporalrca_server.api import metric_series, query_metrics
from temporalrca_server.models import Base, ContainerInstance, Host, MetricDefinition, MetricSample, MetricSeries


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sessions() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_container_metric_filters_include_same_name_history_without_cross_host_rows(session):
    host = Host(
        id=uuid.uuid4(), agent_id=uuid.uuid4(), external_id="host", name="host",
        first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
    )
    other_host = Host(
        id=uuid.uuid4(), agent_id=uuid.uuid4(), external_id="other-host", name="other-host",
        first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    common = dict(
        name="worker", attributes={}, first_seen_at=now, last_seen_at=now,
        lease_expires_at=now + timedelta(hours=1), active=True,
    )
    old = ContainerInstance(id=uuid.uuid4(), host_id=host.id, external_id="runtime-old", **common)
    current = ContainerInstance(id=uuid.uuid4(), host_id=host.id, external_id="runtime-current", **common)
    unrelated_host = ContainerInstance(id=uuid.uuid4(), host_id=other_host.id, external_id="runtime-other", **common)
    definition = MetricDefinition(id=1, name="container.queue.depth", unit="1")
    old_series = MetricSeries(
        id=101, metric_definition_id=definition.id, host_id=host.id, container_id=old.id,
        source_type="application", attributes={}, fingerprint="old-series",
    )
    current_series = MetricSeries(
        id=102, metric_definition_id=definition.id, host_id=host.id, container_id=current.id,
        source_type="application", attributes={}, fingerprint="current-series",
    )
    other_series = MetricSeries(
        id=103, metric_definition_id=definition.id, host_id=other_host.id, container_id=unrelated_host.id,
        source_type="application", attributes={}, fingerprint="other-series",
    )
    session.add_all([host, other_host, old, current, unrelated_host, definition, old_series, current_series, other_series])
    await session.flush()
    session.add_all([
        MetricSample(timestamp=now, event_id=uuid.uuid4(), series_id=old_series.id,
                     observed_timestamp=now, sequence=1, value=1.0),
        MetricSample(timestamp=now + timedelta(seconds=1), event_id=uuid.uuid4(), series_id=current_series.id,
                     observed_timestamp=now + timedelta(seconds=1), sequence=2, value=2.0),
        MetricSample(timestamp=now, event_id=uuid.uuid4(), series_id=other_series.id,
                     observed_timestamp=now, sequence=1, value=99.0),
    ])
    await session.commit()

    listed = await metric_series(container_id=current.id, session=session)
    assert {row["id"] for row in listed} == {old_series.id, current_series.id}

    queried = await query_metrics(
        start=now - timedelta(seconds=1), end=now + timedelta(seconds=2),
        container_id=current.id, aggregation="raw", max_points=100, session=session,
    )
    assert {row["series_id"] for row in queried["series"]} == {old_series.id, current_series.id}
    points = {row["series_id"]: row["points"] for row in queried["series"]}
    assert points[old_series.id][0]["last"] == 1.0
    assert points[current_series.id][0]["last"] == 2.0

