from __future__ import annotations

import base64
import gzip
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from temporalrca_contracts.models import BatchResponse, Heartbeat, Inventory, InventoryResponse, TelemetryBatch

from .config import Settings, get_settings
from .database import get_session
from .ingestion import ingest_batch
from .live import watermarks
from .models import (
    Agent, AgentHeartbeat, ContainerInstance, Dependency, Experiment, Host, InventorySnapshot, LogEvent,
    MetricDefinition, MetricRollup5m, MetricSample, MetricSeries, ProcessInstance,
    ServiceDependency, ServiceInstance, TimelineEvent,
)
from .security import AgentIdentity, authenticated_agent, credential_hash, new_credential, utcnow
from .partitions import ensure_partitions

router = APIRouter(prefix="/api/v1")


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(APIModel):
    enrollment_token: str
    installation_id: str = Field(min_length=1, max_length=255)
    host_external_id: str = Field(min_length=1, max_length=255)
    host_name: str = Field(min_length=1, max_length=255)
    agent_version: str | None = None
    host_attributes: dict[str, Any] = Field(default_factory=dict)


class RegisterResponse(APIModel):
    agent_id: uuid.UUID
    host_id: uuid.UUID
    credential: str


class RotateCredentialResponse(APIModel):
    credential: str
    credential_version: int


class GroundTruthWrite(APIModel):
    event_id: uuid.UUID
    experiment_id: uuid.UUID
    timestamp: datetime
    observed_timestamp: datetime
    name: str
    event_type: str
    message: str | None = None
    host_id: uuid.UUID
    service_id: uuid.UUID | None = None
    process_id: uuid.UUID | None = None
    dependency_id: uuid.UUID | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    experiment_name: str | None = None
    experiment_status: Literal["running", "completed", "failed"] = "running"
    configuration: dict[str, Any] = Field(default_factory=dict)


@router.post("/agents/register", response_model=RegisterResponse, status_code=201)
async def register(
    body: RegisterRequest, session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RegisterResponse:
    import hmac
    if not hmac.compare_digest(body.enrollment_token, settings.enrollment_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid enrollment token")
    if (await session.execute(select(Agent.id).where(Agent.installation_id == body.installation_id))).scalar_one_or_none():
        raise HTTPException(409, "installation is already registered; rotate its credential")
    credential = new_credential()
    now = utcnow()
    agent = Agent(
        installation_id=body.installation_id,
        credential_hash=credential_hash(credential, settings.credential_pepper),
        version=body.agent_version, registered_at=now, last_seen_at=now,
    )
    session.add(agent)
    await session.flush()
    host = Host(
        agent_id=agent.id, external_id=body.host_external_id, name=body.host_name,
        attributes=body.host_attributes, first_seen_at=now, last_seen_at=now,
    )
    session.add(host)
    await session.commit()
    await watermarks.publish("inventory")
    return RegisterResponse(agent_id=agent.id, host_id=host.id, credential=credential)


@router.post("/agents/me/credential", response_model=RotateCredentialResponse)
async def rotate_credential(
    identity: AgentIdentity = Depends(authenticated_agent),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
) -> RotateCredentialResponse:
    credential = new_credential()
    agent = await session.get(Agent, identity.agent_id, with_for_update=True)
    agent.credential_hash = credential_hash(credential, settings.credential_pepper)
    agent.credential_version += 1
    await session.commit()
    return RotateCredentialResponse(credential=credential, credential_version=agent.credential_version)


async def _existing(session: AsyncSession, model, host_id) -> dict[str, Any]:
    rows = (await session.execute(select(model).where(model.host_id == host_id))).scalars()
    return {row.external_id: row for row in rows}


async def _metric_container_ids(session: AsyncSession, container_id: uuid.UUID) -> list[uuid.UUID]:
    """Resolve a container UUID to all of its same-name incarnations.

    Container telemetry keeps the concrete inventory UUID in ``MetricSeries``
    so that samples remain referentially intact.  A runtime recreating a
    container gets a new UUID, however, and callers opening the current
    container should still be able to see the prior incarnation's samples.
    The inventory name is the stable logical identity, but it is only stable
    within a host; include the host predicate to avoid mixing identically
    named containers from different hosts.

    Unknown UUIDs retain the existing exact-ID behavior and produce no rows.
    """
    current = await session.get(ContainerInstance, container_id)
    if current is None:
        return [container_id]
    return list((await session.execute(
        select(ContainerInstance.id).where(
            ContainerInstance.host_id == current.host_id,
            ContainerInstance.name == current.name,
        )
    )).scalars())


@router.put("/agents/me/inventory", response_model=InventoryResponse)
async def reconcile_inventory(
    inventory: Inventory, identity: AgentIdentity = Depends(authenticated_agent),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    now, seen = inventory.observed_at, {}
    if now.tzinfo is None:
        raise HTTPException(422, "observed_at must include a timezone")
    lease = now + timedelta(seconds=inventory.lease_seconds)
    services = await _existing(session, ServiceInstance, identity.host_id)
    containers = await _existing(session, ContainerInstance, identity.host_id)
    processes = await _existing(session, ProcessInstance, identity.host_id)
    dependencies = await _existing(session, Dependency, identity.host_id)

    def touch(row, item):
        row.name = item.name
        row.attributes = item.attributes
        row.last_seen_at = now
        row.lease_expires_at = lease
        row.active = True

    for item in inventory.services:
        row = services.get(item.external_id)
        if row is None:
            row = ServiceInstance(host_id=identity.host_id, external_id=item.external_id, first_seen_at=now)
            services[item.external_id] = row
            session.add(row)
        touch(row, item)
    await session.flush()
    for item in inventory.containers:
        row = containers.get(item.external_id)
        if row is None:
            row = ContainerInstance(host_id=identity.host_id, external_id=item.external_id, first_seen_at=now)
            containers[item.external_id] = row
            session.add(row)
        touch(row, item)
        row.runtime, row.image = item.runtime, item.image
        row.service_id = services[item.service_external_id].id if item.service_external_id in services else None
    await session.flush()
    for item in inventory.processes:
        row = processes.get(item.external_id)
        identity_tuple = (item.boot_id, item.pid, item.start_time_ticks)
        if row is not None and (row.boot_id, row.pid, row.start_time_ticks) != identity_tuple:
            # external discovery IDs may be reused; preserve the prior process history.
            row.external_id = f"retired:{row.id}:{row.external_id}"[:255]
            row.active = False
            row.lease_expires_at = now
            row = None
        if row is None:
            row = ProcessInstance(host_id=identity.host_id, external_id=item.external_id, first_seen_at=now)
            processes[item.external_id] = row
            session.add(row)
        touch(row, item)
        row.boot_id, row.pid, row.start_time_ticks = identity_tuple
        row.command = item.command
        row.service_id = services[item.service_external_id].id if item.service_external_id in services else None
        row.container_id = containers[item.container_external_id].id if item.container_external_id in containers else None
    await session.flush()
    active_links: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for item in inventory.dependencies:
        row = dependencies.get(item.external_id)
        if row is None:
            row = Dependency(host_id=identity.host_id, external_id=item.external_id, first_seen_at=now)
            dependencies[item.external_id] = row
            session.add(row)
        touch(row, item)
        row.kind = item.kind
        await session.flush()
        for service_external_id in item.service_external_ids:
            service = services.get(service_external_id)
            if service is None:
                raise HTTPException(422, f"unknown service_external_id: {service_external_id}")
            key = (service.id, row.id)
            active_links.add(key)
            link = await session.get(ServiceDependency, key)
            if link is None:
                session.add(ServiceDependency(service_id=service.id, dependency_id=row.id, first_seen_at=now, last_seen_at=now))
            else:
                link.last_seen_at, link.active = now, True

    known_dependency_ids = {row.id for row in dependencies.values()}
    if known_dependency_ids:
        links = (await session.execute(select(ServiceDependency).where(ServiceDependency.dependency_id.in_(known_dependency_ids)))).scalars()
        for link in links:
            if (link.service_id, link.dependency_id) not in active_links:
                dependency = next((row for row in dependencies.values() if row.id == link.dependency_id), None)
                if dependency is not None and dependency.lease_expires_at <= now:
                    link.active = False

    included = {
        ServiceInstance: {x.external_id for x in inventory.services},
        ContainerInstance: {x.external_id for x in inventory.containers},
        ProcessInstance: {x.external_id for x in inventory.processes},
        Dependency: {x.external_id for x in inventory.dependencies},
    }
    for model, rows in ((ServiceInstance, services), (ContainerInstance, containers), (ProcessInstance, processes), (Dependency, dependencies)):
        for external_id, row in rows.items():
            if external_id not in included[model] and row.lease_expires_at <= now:
                row.active = False
    host = await session.get(Host, identity.host_id)
    host.last_seen_at, host.active = now, True
    resource_ids = {
        "service_ids": {key: str(row.id) for key, row in services.items() if key in included[ServiceInstance]},
        "process_ids": {key: str(row.id) for key, row in processes.items() if key in included[ProcessInstance]},
        "container_ids": {key: str(row.id) for key, row in containers.items() if key in included[ContainerInstance]},
        "dependency_ids": {key: str(row.id) for key, row in dependencies.items() if key in included[Dependency]},
    }
    session.add(InventorySnapshot(
        host_id=identity.host_id, observed_at=now,
        inventory=inventory.model_dump(mode="json"), resource_ids=resource_ids,
    ))
    await session.commit()
    watermark = await watermarks.publish("inventory")
    return InventoryResponse(
        inventory_watermark=watermark, lease_expires_at=lease,
        **resource_ids,
    )


@router.post("/agents/me/heartbeat", status_code=202)
async def heartbeat(
    body: Heartbeat, identity: AgentIdentity = Depends(authenticated_agent),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    session.add(AgentHeartbeat(
        agent_id=identity.agent_id, observed_at=body.observed_at,
        agent_version=body.agent_version, spool_bytes=body.spool_bytes,
        spool_events=body.spool_events,
        collectors=[item.model_dump(mode="json") for item in body.collectors],
    ))
    agent, host = await session.get(Agent, identity.agent_id), await session.get(Host, identity.host_id)
    agent.last_seen_at = body.observed_at
    agent.version = body.agent_version
    host.last_seen_at, host.active = body.observed_at, True
    await session.commit()
    return {"status": "accepted"}


@router.post(
    "/telemetry/batches", response_model=BatchResponse,
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": TelemetryBatch.model_json_schema()}}}},
)
async def telemetry_batch(
    request: Request, content_encoding: str | None = Header(default=None),
    identity: AgentIdentity = Depends(authenticated_agent),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
):
    raw = await request.body()
    if len(raw) > settings.max_compressed_batch_bytes:
        raise HTTPException(413, "compressed batch exceeds 2 MiB")
    try:
        decoded = gzip.decompress(raw) if content_encoding == "gzip" else raw
        payload = json.loads(decoded)
        batch = TelemetryBatch.model_validate(payload)
    except Exception as exc:
        raise HTTPException(422, f"invalid telemetry batch: {exc}") from exc
    limit = settings.backfill_batch_events if batch.backfill else settings.normal_batch_events
    if len(batch.events) > limit:
        raise HTTPException(413, f"batch exceeds {limit} event limit")
    result = await ingest_batch(session, identity, batch, payload, settings.normal_batch_events)
    if not result.duplicate_batch and result.accepted_event_ids:
        result.commit_watermark = await watermarks.publish("commit")
    return result


def _serialize(row) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.get("/topology")
async def topology(include_inactive: bool = False, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    async def rows(model):
        query = select(model)
        if not include_inactive and hasattr(model, "active"):
            query = query.where(model.active.is_(True))
        return [_serialize(row) for row in (await session.execute(query)).scalars()]
    return {
        "hosts": await rows(Host), "services": await rows(ServiceInstance),
        "processes": await rows(ProcessInstance), "containers": await rows(ContainerInstance),
        "dependencies": await rows(Dependency), "service_dependencies": await rows(ServiceDependency),
    }


@router.get("/hosts")
async def hosts(active: bool | None = None, session: AsyncSession = Depends(get_session)):
    query = select(Host)
    if active is not None:
        query = query.where(Host.active == active)
    return [_serialize(row) for row in (await session.execute(query.order_by(Host.name))).scalars()]


@router.get("/hosts/{host_id}")
async def host_detail(host_id: uuid.UUID, include_inactive: bool = False,
                      session: AsyncSession = Depends(get_session)):
    host = await session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "host not found")
    result = _serialize(host)
    for key, model in (("services", ServiceInstance), ("processes", ProcessInstance), ("containers", ContainerInstance), ("dependencies", Dependency)):
        query = select(model).where(model.host_id == host_id)
        if not include_inactive:
            query = query.where(model.active.is_(True))
        result[key] = [_serialize(x) for x in (await session.execute(query)).scalars()]
    return result


@router.get("/services/{service_id}")
async def service_detail(service_id: uuid.UUID, include_inactive: bool = False,
                         session: AsyncSession = Depends(get_session)):
    row = await session.get(ServiceInstance, service_id)
    if not row: raise HTTPException(404, "service not found")
    result = _serialize(row)
    process_query = select(ProcessInstance).where(ProcessInstance.service_id == service_id)
    dependency_query = select(Dependency).join(ServiceDependency).where(ServiceDependency.service_id == service_id)
    if not include_inactive:
        process_query = process_query.where(ProcessInstance.active.is_(True))
        dependency_query = dependency_query.where(Dependency.active.is_(True), ServiceDependency.active.is_(True))
    result["processes"] = [_serialize(x) for x in (await session.execute(process_query)).scalars()]
    result["dependencies"] = [_serialize(x) for x in (await session.execute(dependency_query)).scalars()]
    return result


@router.get("/processes/{process_id}")
async def process_detail(process_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(ProcessInstance, process_id)
    if not row: raise HTTPException(404, "process not found")
    return _serialize(row)


@router.get("/containers/{container_id}")
async def container_detail(container_id: uuid.UUID, include_inactive: bool = False,
                           session: AsyncSession = Depends(get_session)):
    row = await session.get(ContainerInstance, container_id)
    if not row:
        raise HTTPException(404, "container not found")
    result = _serialize(row)
    process_query = select(ProcessInstance).where(ProcessInstance.container_id == container_id)
    if not include_inactive:
        process_query = process_query.where(ProcessInstance.active.is_(True))
    result["processes"] = [_serialize(x) for x in (
        await session.execute(process_query)
    ).scalars()]
    return result


@router.get("/dependencies/{dependency_id}")
async def dependency_detail(dependency_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(Dependency, dependency_id)
    if not row: raise HTTPException(404, "dependency not found")
    return _serialize(row)


@router.get("/metrics/catalog")
async def metric_catalog(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(MetricDefinition, func.count(MetricSeries.id)).outerjoin(MetricSeries).group_by(MetricDefinition.id).order_by(MetricDefinition.name))
    return [{"id": metric.id, "name": metric.name, "unit": metric.unit, "series_count": count} for metric, count in result]


@router.get("/metrics/series")
async def metric_series(
    metric: str | None = None, host_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None, process_id: uuid.UUID | None = None,
    dependency_id: uuid.UUID | None = None, container_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    query = select(MetricSeries, MetricDefinition).join(MetricDefinition)
    for column, value in ((MetricDefinition.name, metric), (MetricSeries.host_id, host_id), (MetricSeries.service_id, service_id), (MetricSeries.process_id, process_id), (MetricSeries.dependency_id, dependency_id)):
        if value is not None: query = query.where(column == value)
    if container_id is not None:
        container_ids = await _metric_container_ids(session, container_id)
        query = query.where(MetricSeries.container_id.in_(container_ids))
    return [{**_serialize(series), "name": definition.name, "unit": definition.unit} for series, definition in (await session.execute(query)).all()]


BUCKETS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400]


def select_bucket_seconds(start: datetime, end: datetime, max_points: int) -> int:
    needed = max(1, math.ceil((end - start).total_seconds() / max_points))
    return next((size for size in BUCKETS if size >= needed), BUCKETS[-1])


@router.get("/metrics/query")
async def query_metrics(
    start: datetime, end: datetime, series_id: list[int] = Query(default=[]),
    metric: str | None = None, host_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None, process_id: uuid.UUID | None = None,
    dependency_id: uuid.UUID | None = None, container_id: uuid.UUID | None = None,
    aggregation: Literal["auto", "raw", "5m"] = "auto",
    max_points: int = Query(default=1000, ge=10, le=10_000),
    session: AsyncSession = Depends(get_session),
):
    if end <= start: raise HTTPException(422, "end must be after start")
    bucket_seconds = select_bucket_seconds(start, end, max_points)
    query = select(MetricSeries.id).join(MetricDefinition)
    if series_id: query = query.where(MetricSeries.id.in_(series_id))
    for column, value in ((MetricDefinition.name, metric), (MetricSeries.host_id, host_id), (MetricSeries.service_id, service_id), (MetricSeries.process_id, process_id), (MetricSeries.dependency_id, dependency_id)):
        if value is not None: query = query.where(column == value)
    if container_id is not None:
        container_ids = await _metric_container_ids(session, container_id)
        query = query.where(MetricSeries.container_id.in_(container_ids))
    ids = list((await session.execute(query)).scalars())
    if not ids: return {"bucket_seconds": bucket_seconds, "series": []}
    use_rollup = aggregation == "5m" or (aggregation == "auto" and bucket_seconds >= 300)
    if use_rollup:
        bucket_seconds = max(300, bucket_seconds)
        # Automatic queries splice recent raw samples onto completed rollups.
        # Without this live tail, a newly started resource in the default 24h
        # window has no visible chart until maintenance closes its first 5m
        # bucket. Keep explicit `aggregation=5m` rollup-only for callers that
        # need that exact storage tier.
        rollup_end = end
        if aggregation == "auto":
            lateness = timedelta(minutes=get_settings().rollup_lateness_minutes)
            safe = datetime.now(timezone.utc) - lateness
            rollup_end = min(end, safe.replace(minute=safe.minute - safe.minute % 5, second=0, microsecond=0))
        rollups = await session.execute(select(MetricRollup5m).where(
            MetricRollup5m.series_id.in_(ids), MetricRollup5m.bucket >= start,
            MetricRollup5m.bucket < rollup_end,
        ).order_by(MetricRollup5m.bucket))
        grouped: dict[tuple[int, int], dict[str, Any]] = {}
        for row in rollups.scalars():
            slot = int(row.bucket.timestamp()) // bucket_seconds * bucket_seconds
            key = (row.series_id, slot)
            point = grouped.setdefault(key, {
                "min": row.minimum, "max": row.maximum, "sum": 0.0, "count": 0,
                "last": row.last, "last_timestamp": row.last_timestamp,
            })
            point["min"] = min(point["min"], row.minimum)
            point["max"] = max(point["max"], row.maximum)
            point["sum"] += row.sum
            point["count"] += row.count
            if row.last_timestamp >= point["last_timestamp"]:
                point["last"], point["last_timestamp"] = row.last, row.last_timestamp
        if aggregation == "auto" and rollup_end < end:
            raw_start = max(start, rollup_end)
            raw = await session.execute(select(MetricSample).where(
                MetricSample.series_id.in_(ids), MetricSample.timestamp >= raw_start,
                MetricSample.timestamp < end,
            ).order_by(MetricSample.timestamp))
            for row in raw.scalars():
                slot = int(row.timestamp.timestamp()) // bucket_seconds * bucket_seconds
                key = (row.series_id, slot)
                point = grouped.setdefault(key, {
                    "min": row.value, "max": row.value, "sum": 0.0, "count": 0,
                    "last": row.value, "last_timestamp": row.timestamp,
                })
                point["min"] = min(point["min"], row.value)
                point["max"] = max(point["max"], row.value)
                point["sum"] += row.value
                point["count"] += 1
                if row.timestamp >= point["last_timestamp"]:
                    point["last"], point["last_timestamp"] = row.value, row.timestamp
        response_series = []
        for sid in ids:
            points = []
            for (series, slot), point in grouped.items():
                if series != sid: continue
                points.append({
                    "timestamp": datetime.fromtimestamp(slot, timezone.utc),
                    "min": point["min"], "max": point["max"],
                    "average": point["sum"] / point["count"], "last": point["last"],
                    "count": point["count"],
                })
            response_series.append({"series_id": sid, "points": points})
        return {"bucket_seconds": bucket_seconds, "series": response_series}
    else:
        rows = await session.execute(select(MetricSample).where(MetricSample.series_id.in_(ids), MetricSample.timestamp >= start, MetricSample.timestamp < end).order_by(MetricSample.timestamp))
        grouped: dict[tuple[int, int], list[MetricSample]] = {}
        for row in rows.scalars():
            slot = int(row.timestamp.timestamp()) // bucket_seconds * bucket_seconds
            grouped.setdefault((row.series_id, slot), []).append(row)
        response_series = []
        for sid in ids:
            points = []
            for (series, slot), samples in grouped.items():
                if series != sid: continue
                vals = [x.value for x in samples]
                latest = max(samples, key=lambda x: x.timestamp)
                points.append({"timestamp": datetime.fromtimestamp(slot, timezone.utc), "min": min(vals), "max": max(vals), "average": sum(vals)/len(vals), "last": latest.value, "count": len(vals)})
            response_series.append({"series_id": sid, "points": points})
        return {"bucket_seconds": bucket_seconds, "series": response_series}
    raise AssertionError("unreachable")


def _cursor(timestamp: datetime, event_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{timestamp.isoformat()}|{event_id}".encode()).decode()


def _parse_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, eid = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(eid)
    except Exception as exc: raise HTTPException(422, "invalid cursor") from exc


@router.get("/logs")
async def logs(
    start: datetime, end: datetime, host_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None, process_id: uuid.UUID | None = None,
    dependency_id: uuid.UUID | None = None, severity: list[str] = Query(default=[]),
    search: str | None = None, cursor: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000), session: AsyncSession = Depends(get_session),
):
    query = select(LogEvent).where(LogEvent.timestamp >= start, LogEvent.timestamp < end)
    for column, value in ((LogEvent.host_id, host_id), (LogEvent.service_id, service_id), (LogEvent.process_id, process_id), (LogEvent.dependency_id, dependency_id)):
        if value is not None: query = query.where(column == value)
    if severity: query = query.where(LogEvent.severity.in_(severity))
    if search: query = query.where(LogEvent.message.ilike(f"%{search}%"))
    if cursor:
        ts, eid = _parse_cursor(cursor)
        query = query.where(or_(LogEvent.timestamp > ts, and_(LogEvent.timestamp == ts, LogEvent.event_id > eid)))
    rows = list((await session.execute(query.order_by(LogEvent.timestamp, LogEvent.event_id).limit(limit + 1))).scalars())
    page, more = rows[:limit], len(rows) > limit
    return {"items": [_serialize(x) for x in page], "next_cursor": _cursor(page[-1].timestamp, page[-1].event_id) if more else None}


@router.get("/logs/histogram")
async def log_histogram(start: datetime, end: datetime, host_id: uuid.UUID | None = None, max_points: int = Query(300, ge=10, le=5000), session: AsyncSession = Depends(get_session)):
    bucket = select_bucket_seconds(start, end, max_points)
    query = select(LogEvent).where(LogEvent.timestamp >= start, LogEvent.timestamp < end)
    if host_id: query = query.where(LogEvent.host_id == host_id)
    grouped: dict[tuple[int, str], int] = {}
    for row in (await session.execute(query)).scalars():
        slot = int(row.timestamp.timestamp()) // bucket * bucket
        key = (slot, row.severity or "UNSPECIFIED")
        grouped[key] = grouped.get(key, 0) + 1
    return {"bucket_seconds": bucket, "buckets": [{"timestamp": datetime.fromtimestamp(slot, timezone.utc), "severity": severity, "count": count} for (slot, severity), count in sorted(grouped.items())]}


@router.get("/events")
async def events(start: datetime, end: datetime, signal_type: str | None = None, experiment_id: uuid.UUID | None = None, cursor: str | None = None, limit: int = Query(500, ge=1, le=2000), session: AsyncSession = Depends(get_session)):
    query = select(TimelineEvent).where(TimelineEvent.timestamp >= start, TimelineEvent.timestamp < end)
    if signal_type: query = query.where(TimelineEvent.signal_type == signal_type)
    if experiment_id: query = query.where(TimelineEvent.experiment_id == experiment_id)
    if cursor:
        ts, eid = _parse_cursor(cursor)
        query = query.where(or_(TimelineEvent.timestamp > ts, and_(TimelineEvent.timestamp == ts, TimelineEvent.event_id > eid)))
    rows = list((await session.execute(query.order_by(TimelineEvent.timestamp, TimelineEvent.event_id).limit(limit + 1))).scalars())
    page, more = rows[:limit], len(rows) > limit
    return {"items": [_serialize(x) for x in page], "next_cursor": _cursor(page[-1].timestamp, page[-1].event_id) if more else None}


@router.get("/experiments")
async def experiments(session: AsyncSession = Depends(get_session)):
    return [_serialize(x) for x in (await session.execute(select(Experiment).order_by(Experiment.started_at.desc()))).scalars()]


@router.get("/experiments/{experiment_id}")
async def experiment(experiment_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(Experiment, experiment_id)
    if not row: raise HTTPException(404, "experiment not found")
    result = _serialize(row)
    result["events"] = [_serialize(x) for x in (await session.execute(select(TimelineEvent).where(TimelineEvent.experiment_id == experiment_id).order_by(TimelineEvent.timestamp))).scalars()]
    return result


@router.get("/experiments/{experiment_id}/export", response_class=FileResponse)
async def experiment_export(
    experiment_id: uuid.UUID, session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    from pathlib import Path
    from .export import export_experiment
    try:
        bundle = await export_experiment(session, experiment_id, Path(settings.export_directory))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(bundle, media_type="application/zip", filename=bundle.name)


@router.post("/ground-truth/events", status_code=201)
async def write_ground_truth(
    body: GroundTruthWrite, authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
):
    import hmac
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(token, settings.ground_truth_token):
        raise HTTPException(401, "ground-truth bearer credential required")
    if await session.get(TimelineEvent, (body.timestamp, body.event_id)):
        return {"event_id": body.event_id, "duplicate": True}
    await ensure_partitions(session, "timeline_events", {body.timestamp})
    experiment = await session.get(Experiment, body.experiment_id)
    if experiment is None:
        experiment = Experiment(
            id=body.experiment_id, name=body.experiment_name or body.name,
            status=body.experiment_status, started_at=body.timestamp,
            ended_at=body.timestamp if body.experiment_status != "running" else None,
            configuration=body.configuration,
        )
        session.add(experiment)
    else:
        experiment.status = body.experiment_status
        if body.experiment_status != "running": experiment.ended_at = body.timestamp
    session.add(TimelineEvent(
        timestamp=body.timestamp, event_id=body.event_id, host_id=body.host_id,
        service_id=body.service_id, process_id=body.process_id,
        dependency_id=body.dependency_id, experiment_id=body.experiment_id,
        observed_timestamp=body.observed_timestamp, sequence=0,
        source_type="application", signal_type="ground-truth", name=body.name,
        event_type=body.event_type, message=body.message, attributes=body.attributes,
    ))
    await session.commit()
    watermark = await watermarks.publish("commit")
    return {"event_id": body.event_id, "duplicate": False, "commit_watermark": watermark}


@router.get("/collector-health")
async def collector_health(session: AsyncSession = Depends(get_session)):
    latest = select(AgentHeartbeat.agent_id, func.max(AgentHeartbeat.observed_at).label("observed_at")).group_by(AgentHeartbeat.agent_id).subquery()
    rows = (await session.execute(select(AgentHeartbeat).join(latest, and_(AgentHeartbeat.agent_id == latest.c.agent_id, AgentHeartbeat.observed_at == latest.c.observed_at)))).scalars()
    return [_serialize(x) for x in rows]


@router.get("/live")
async def live():
    return StreamingResponse(watermarks.events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
