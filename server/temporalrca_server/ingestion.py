from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from temporalrca_contracts.models import BatchResponse, RejectedEvent, SignalType, TelemetryBatch, TelemetryEvent

from .models import (
    Agent, BatchReceipt, ContainerInstance, Dependency, EventReceipt, LogEvent,
    MetricDefinition, MetricSample, MetricSeries, ProcessInstance, ServiceInstance,
    TimelineEvent,
)
from .security import AgentIdentity
from .partitions import ensure_partitions


def canonical_digest(payload: dict[str, Any]) -> bytes:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).digest()


def series_fingerprint(event: TelemetryEvent, host_id: str) -> str:
    identity = {
        "name": event.name, "host_id": host_id, "service_id": event.service_id,
        "process_id": event.process_id, "container_id": event.container_id,
        "dependency_id": event.dependency_id, "source_type": event.source_type,
        "attributes": event.attributes,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


async def _insert_ignore_conflicts(session: AsyncSession, model: type, rows: list[dict[str, Any]], conflict_column: Any) -> None:
    """Insert rows in one statement while allowing another writer to win a unique key.

    Metric definitions are globally unique by name and metric series are globally
    unique by fingerprint.  Agents can create either row concurrently, so a
    normal ORM flush would turn an otherwise harmless race into a transaction
    rollback.  PostgreSQL is the production database; retain a savepoint-based
    fallback for the lightweight SQLite test database.
    """
    if not rows:
        return
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        statement = pg_insert(model).values(rows).on_conflict_do_nothing(
            index_elements=[conflict_column],
        )
        await session.execute(statement)
        return
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        statement = sqlite_insert(model).values(rows).on_conflict_do_nothing(
            index_elements=[conflict_column],
        )
        await session.execute(statement)
        return

    # This is only for unsupported development dialects.  It still keeps the
    # conflict rollback local to a savepoint rather than poisoning the batch.
    for row in rows:
        try:
            async with session.begin_nested():
                await session.execute(insert(model).values(row))
        except IntegrityError:
            pass


async def _metric_series_batch(
    session: AsyncSession,
    events: list[TelemetryEvent],
    host_id,
) -> dict[str, MetricSeries]:
    """Resolve all metric definitions and series needed by one telemetry batch.

    The old per-event path performed two SELECTs and two flushes for every new
    metric series.  Prefetching unique names/fingerprints and inserting missing
    rows in bulk reduces this to at most four statements for the whole batch.
    The ordered dictionaries preserve the first-seen unit and series metadata,
    matching the old event-order behavior when a batch introduces new rows.
    Depending on whether rows already exist, this performs four to six SQL
    statements for the whole batch (plus the final batch write), never one per
    event.
    """
    if not events:
        return {}

    host_key = str(host_id)
    fingerprints: dict[str, TelemetryEvent] = {}
    units: dict[str, str | None] = {}
    for event in events:
        units.setdefault(event.name, event.unit)
        fingerprint = series_fingerprint(event, host_key)
        fingerprints.setdefault(fingerprint, event)

    names = list(units)
    definitions = list((await session.execute(
        select(MetricDefinition).where(MetricDefinition.name.in_(names))
    )).scalars())
    definitions_by_name = {definition.name: definition for definition in definitions}
    missing_definitions = [
        {"name": name, "unit": units[name]}
        for name in names
        if name not in definitions_by_name
    ]
    await _insert_ignore_conflicts(session, MetricDefinition, missing_definitions, MetricDefinition.name)
    if missing_definitions:
        # Re-read after the insert so this also picks up rows committed by a
        # concurrent agent that won an ON CONFLICT race.
        definitions = list((await session.execute(
            select(MetricDefinition).where(MetricDefinition.name.in_(names))
        )).scalars())
        definitions_by_name = {definition.name: definition for definition in definitions}

    series_fingerprints = list(fingerprints)
    series = list((await session.execute(
        select(MetricSeries).where(MetricSeries.fingerprint.in_(series_fingerprints))
    )).scalars())
    series_by_fingerprint = {item.fingerprint: item for item in series}
    missing_series: list[dict[str, Any]] = []
    for fingerprint, event in fingerprints.items():
        if fingerprint in series_by_fingerprint:
            continue
        definition = definitions_by_name[event.name]
        missing_series.append({
            "metric_definition_id": definition.id,
            "host_id": host_id,
            "service_id": event.service_id,
            "process_id": event.process_id,
            "container_id": event.container_id,
            "dependency_id": event.dependency_id,
            "source_type": event.source_type.value,
            "attributes": event.attributes,
            "fingerprint": fingerprint,
        })
    await _insert_ignore_conflicts(session, MetricSeries, missing_series, MetricSeries.fingerprint)
    if missing_series:
        series = list((await session.execute(
            select(MetricSeries).where(MetricSeries.fingerprint.in_(series_fingerprints))
        )).scalars())
        series_by_fingerprint = {item.fingerprint: item for item in series}
    return series_by_fingerprint


async def ingest_batch(
    session: AsyncSession, identity: AgentIdentity, batch: TelemetryBatch,
    raw_payload: dict[str, Any], normal_limit: int,
) -> BatchResponse:
    # Serialize deliveries per agent so concurrent retries under distinct batch IDs
    # still converge on the unique event receipt.
    await session.execute(select(Agent.id).where(Agent.id == identity.agent_id).with_for_update())
    if not batch.backfill and len(batch.events) > normal_limit:
        raise HTTPException(413, f"normal batches may contain at most {normal_limit} events")
    digest = canonical_digest(raw_payload)
    receipt = await session.get(BatchReceipt, (identity.agent_id, batch.batch_id))
    if receipt:
        if receipt.payload_digest != digest:
            raise HTTPException(409, "batch_id was already used with a different payload")
        saved = dict(receipt.response)
        saved["duplicate_batch"] = True
        return BatchResponse.model_validate(saved)

    entity_models = {
        "service_id": ServiceInstance, "process_id": ProcessInstance,
        "container_id": ContainerInstance, "dependency_id": Dependency,
    }
    owned: dict[str, set] = {}
    for field, model in entity_models.items():
        owned[field] = set((await session.execute(select(model.id).where(model.host_id == identity.host_id))).scalars())
    valid: list[TelemetryEvent] = []
    rejected: list[RejectedEvent] = []
    for index, raw in enumerate(batch.events):
        try:
            event = TelemetryEvent.model_validate(raw)
            if event.host_id is not None and event.host_id != identity.host_id:
                raise ValueError("host_id does not match authenticated agent")
            for field in entity_models:
                reference = getattr(event, field)
                if reference is not None and reference not in owned[field]:
                    raise ValueError(f"{field} does not belong to authenticated agent")
            valid.append(event)
        except (ValidationError, ValueError) as exc:
            raw_errors = exc.errors(include_url=False) if isinstance(exc, ValidationError) else [{"type": "identity", "msg": str(exc)}]
            # Pydantic validation contexts may embed the original ValueError; receipts
            # must remain JSON-serializable for deterministic replay.
            errors = json.loads(json.dumps(raw_errors, default=str))
            rejected.append(RejectedEvent(index=index, event_id=str(raw.get("event_id")) if raw.get("event_id") else None, errors=errors))

    ids = [event.event_id for event in valid]
    duplicates = set((await session.execute(
        select(EventReceipt.event_id).where(EventReceipt.agent_id == identity.agent_id, EventReceipt.event_id.in_(ids))
    )).scalars()) if ids else set()
    accepted = [event for event in valid if event.event_id not in duplicates]
    now = datetime.now(timezone.utc)
    try:
        await ensure_partitions(session, "metric_samples", {x.timestamp for x in accepted if x.signal_type == SignalType.METRIC})
        await ensure_partitions(session, "log_events", {x.timestamp for x in accepted if x.signal_type == SignalType.LOG})
        await ensure_partitions(session, "timeline_events", {x.timestamp for x in accepted if x.signal_type not in (SignalType.METRIC, SignalType.LOG)})
        metric_events = [event for event in accepted if event.signal_type == SignalType.METRIC]
        metric_series = await _metric_series_batch(session, metric_events, identity.host_id)
        for event in accepted:
            session.add(EventReceipt(
                agent_id=identity.agent_id, event_id=event.event_id,
                event_timestamp=event.timestamp, received_at=now,
            ))
            common = dict(
                timestamp=event.timestamp, event_id=event.event_id, host_id=identity.host_id,
                service_id=event.service_id, process_id=event.process_id,
                dependency_id=event.dependency_id, experiment_id=event.experiment_id,
                observed_timestamp=event.observed_timestamp, sequence=event.sequence,
            )
            if event.signal_type == SignalType.METRIC:
                series = metric_series[series_fingerprint(event, str(identity.host_id))]
                session.add(MetricSample(
                    timestamp=event.timestamp, event_id=event.event_id, series_id=series.id,
                    observed_timestamp=event.observed_timestamp, sequence=event.sequence,
                    value=event.value, experiment_id=event.experiment_id,
                ))
            elif event.signal_type == SignalType.LOG:
                session.add(LogEvent(
                    **common, container_id=event.container_id, source_type=event.source_type.value,
                    severity=event.severity, event_type=event.event_type,
                    message=event.message or "", attributes=event.attributes,
                ))
            else:
                session.add(TimelineEvent(
                    **common, source_type=event.source_type.value,
                    signal_type=event.signal_type.value, name=event.name,
                    event_type=event.event_type, message=event.message,
                    attributes=event.attributes,
                ))
        response = BatchResponse(
            batch_id=batch.batch_id,
            accepted_event_ids=[event.event_id for event in accepted],
            duplicate_event_ids=sorted(duplicates, key=str), rejected=rejected,
        )
        session.add(BatchReceipt(
            agent_id=identity.agent_id, batch_id=batch.batch_id, payload_digest=digest,
            received_at=now, response=response.model_dump(mode="json"),
        ))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # A concurrent request won the unique receipt race; replay it deterministically.
        winner = await session.get(BatchReceipt, (identity.agent_id, batch.batch_id))
        if winner and winner.payload_digest == digest:
            saved = dict(winner.response)
            saved["duplicate_batch"] = True
            return BatchResponse.model_validate(saved)
        raise
    return response
