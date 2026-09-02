from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary,
    String, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .database import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[str] = mapped_column(String(255), unique=True)
    credential_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    credential_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Host(Base):
    __tablename__ = "hosts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), unique=True)
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("agent_id", "external_id", name="uq_host_agent_external_id"),)


class InventoryMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    host_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hosts.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ServiceInstance(InventoryMixin, Base):
    __tablename__ = "service_instances"
    __table_args__ = (UniqueConstraint("host_id", "external_id"),)


class ContainerInstance(InventoryMixin, Base):
    __tablename__ = "container_instances"
    runtime: Mapped[str | None] = mapped_column(String(64))
    image: Mapped[str | None] = mapped_column(String(512))
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_instances.id"))
    __table_args__ = (UniqueConstraint("host_id", "external_id"),)


class ProcessInstance(InventoryMixin, Base):
    __tablename__ = "process_instances"
    boot_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    pid: Mapped[int] = mapped_column(Integer)
    start_time_ticks: Mapped[int] = mapped_column(BigInteger)
    command: Mapped[str | None] = mapped_column(Text)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_instances.id"), index=True)
    container_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("container_instances.id"))
    __table_args__ = (
        UniqueConstraint("host_id", "external_id"),
        UniqueConstraint("host_id", "boot_id", "pid", "start_time_ticks", name="uq_process_identity"),
    )


class Dependency(InventoryMixin, Base):
    __tablename__ = "dependencies"
    kind: Mapped[str] = mapped_column(String(128))
    __table_args__ = (UniqueConstraint("host_id", "external_id"),)


class ServiceDependency(Base):
    __tablename__ = "service_dependencies"
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_instances.id"), primary_key=True)
    dependency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dependencies.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentHeartbeat(Base):
    __tablename__ = "agent_heartbeats"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    agent_version: Mapped[str] = mapped_column(String(64))
    spool_bytes: Mapped[int] = mapped_column(BigInteger)
    spool_events: Mapped[int] = mapped_column(BigInteger)
    collectors: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    host_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hosts.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    inventory: Mapped[dict[str, Any]] = mapped_column(JSONType)
    resource_ids: Mapped[dict[str, Any]] = mapped_column(JSONType)


class BatchReceipt(Base):
    __tablename__ = "batch_receipts"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    payload_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response: Mapped[dict[str, Any]] = mapped_column(JSONType)


class EventReceipt(Base):
    __tablename__ = "event_receipts"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    unit: Mapped[str | None] = mapped_column(String(64))


class MetricSeries(Base):
    __tablename__ = "metric_series"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    metric_definition_id: Mapped[int] = mapped_column(ForeignKey("metric_definitions.id"), index=True)
    host_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hosts.id"), index=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_instances.id"), index=True)
    process_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("process_instances.id"), index=True)
    container_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("container_instances.id"), index=True)
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dependencies.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)


class MetricSample(Base):
    __tablename__ = "metric_samples"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("metric_series.id"), index=True)
    observed_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(BigInteger)
    value: Mapped[float] = mapped_column(Float)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    __table_args__ = (
        Index("ix_metric_samples_series_timestamp", "series_id", "timestamp"),
        {"postgresql_partition_by": "RANGE (timestamp)"},
    )


class LogEvent(Base):
    __tablename__ = "log_events"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    host_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hosts.id"), index=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    process_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    container_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    observed_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(BigInteger)
    source_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str | None] = mapped_column(String(32), index=True)
    event_type: Mapped[str | None] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    __table_args__ = ({"postgresql_partition_by": "RANGE (timestamp)"},)


class TimelineEvent(Base):
    __tablename__ = "timeline_events"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    host_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hosts.id"), index=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    process_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    observed_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(BigInteger)
    source_type: Mapped[str] = mapped_column(String(32))
    signal_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str | None] = mapped_column(String(128))
    message: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    __table_args__ = ({"postgresql_partition_by": "RANGE (timestamp)"},)


class MetricRollup5m(Base):
    __tablename__ = "metric_rollups_5m"
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("metric_series.id"), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger)
    sum: Mapped[float] = mapped_column(Float)
    minimum: Mapped[float] = mapped_column(Float)
    maximum: Mapped[float] = mapped_column(Float)
    average: Mapped[float] = mapped_column(Float)
    last: Mapped[float] = mapped_column(Float)
    last_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = ({"postgresql_partition_by": "RANGE (bucket)"},)


class LogRollup5m(Base):
    __tablename__ = "log_rollups_5m"
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    host_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    severity: Mapped[str] = mapped_column(String(32), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = ({"postgresql_partition_by": "RANGE (bucket)"},)


class MaintenanceState(Base):
    __tablename__ = "maintenance_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rolled_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
