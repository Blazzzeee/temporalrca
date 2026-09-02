from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

SCHEMA_VERSION = "1.0"
MAX_ATTRIBUTES = 64
MAX_ATTRIBUTES_BYTES = 16_384


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceType(StrEnum):
    SYSTEM = "system"
    PROCESS = "process"
    APPLICATION = "application"
    DEPENDENCY = "dependency"


class SignalType(StrEnum):
    METRIC = "metric"
    LOG = "log"
    LIFECYCLE = "lifecycle"
    COLLECTOR_HEALTH = "collector-health"
    GROUND_TRUTH = "ground-truth"


Scalar = str | int | float | bool | None


class TelemetryEvent(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    event_id: UUID
    timestamp: datetime
    observed_timestamp: datetime
    sequence: Annotated[int, Field(ge=0)]
    host_id: UUID | None = None
    service_id: UUID | None = None
    process_id: UUID | None = None
    container_id: UUID | None = None
    dependency_id: UUID | None = None
    experiment_id: UUID | None = None
    source_type: SourceType
    signal_type: SignalType
    name: Annotated[str, Field(min_length=1, max_length=255)]
    value: float | None = None
    unit: Annotated[str | None, Field(max_length=64)] = None
    severity: Annotated[str | None, Field(max_length=32)] = None
    event_type: Annotated[str | None, Field(max_length=128)] = None
    message: Annotated[str | None, Field(max_length=65_536)] = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("timestamp", "observed_timestamp")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("attributes")
    @classmethod
    def bounded_attributes(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(value) > MAX_ATTRIBUTES:
            raise ValueError(f"attributes may contain at most {MAX_ATTRIBUTES} keys")
        if any(len(key) > 128 for key in value):
            raise ValueError("attribute keys may not exceed 128 characters")
        if len(json.dumps(value, separators=(",", ":"), default=str).encode()) > MAX_ATTRIBUTES_BYTES:
            raise ValueError(f"attributes may not exceed {MAX_ATTRIBUTES_BYTES} bytes")
        return value

    @model_validator(mode="after")
    def signal_fields(self) -> TelemetryEvent:
        if self.signal_type == SignalType.METRIC and self.value is None:
            raise ValueError("metric events require value")
        if self.signal_type == SignalType.LOG and self.message is None:
            raise ValueError("log events require message")
        if self.source_type == SourceType.PROCESS and self.process_id is None:
            raise ValueError("process events require process_id")
        if self.source_type == SourceType.DEPENDENCY and self.dependency_id is None:
            raise ValueError("dependency events require dependency_id")
        return self


class TelemetryBatch(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    batch_id: UUID
    created_at: datetime
    first_sequence: Annotated[int, Field(ge=0)]
    last_sequence: Annotated[int, Field(ge=0)]
    backfill: bool = False
    events: list[dict[str, Any]] = Field(max_length=2_000)

    @model_validator(mode="after")
    def sequences_are_valid(self) -> TelemetryBatch:
        if self.last_sequence < self.first_sequence:
            raise ValueError("last_sequence must be >= first_sequence")
        return self


class RejectedEvent(StrictModel):
    index: int
    event_id: str | None = None
    errors: list[dict[str, Any]]


class BatchResponse(StrictModel):
    batch_id: UUID
    duplicate_batch: bool = False
    accepted_event_ids: list[UUID] = Field(default_factory=list)
    duplicate_event_ids: list[UUID] = Field(default_factory=list)
    rejected: list[RejectedEvent] = Field(default_factory=list)
    commit_watermark: int | None = None


class InventoryService(StrictModel):
    external_id: Annotated[str, Field(min_length=1, max_length=255)]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class InventoryContainer(StrictModel):
    external_id: Annotated[str, Field(min_length=1, max_length=255)]
    name: str
    runtime: str | None = None
    image: str | None = None
    service_external_id: str | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class InventoryProcess(StrictModel):
    external_id: Annotated[str, Field(min_length=1, max_length=255)]
    boot_id: UUID
    pid: Annotated[int, Field(gt=0)]
    start_time_ticks: Annotated[int, Field(ge=0)]
    name: str
    command: str | None = None
    service_external_id: str | None = None
    container_external_id: str | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class InventoryDependency(StrictModel):
    external_id: Annotated[str, Field(min_length=1, max_length=255)]
    name: str
    kind: str
    service_external_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class Inventory(StrictModel):
    observed_at: datetime
    lease_seconds: Annotated[int, Field(ge=10, le=3600)] = 30
    services: list[InventoryService] = Field(default_factory=list)
    containers: list[InventoryContainer] = Field(default_factory=list)
    processes: list[InventoryProcess] = Field(default_factory=list)
    dependencies: list[InventoryDependency] = Field(default_factory=list)


class InventoryResponse(StrictModel):
    status: Literal["ok"] = "ok"
    inventory_watermark: int
    lease_expires_at: datetime
    service_ids: dict[str, UUID]
    process_ids: dict[str, UUID]
    container_ids: dict[str, UUID]
    dependency_ids: dict[str, UUID]


class CollectorStatus(StrictModel):
    name: str
    healthy: bool
    message: str | None = None
    last_success_at: datetime | None = None


class Heartbeat(StrictModel):
    observed_at: datetime
    agent_version: str
    spool_bytes: Annotated[int, Field(ge=0)]
    spool_events: Annotated[int, Field(ge=0)]
    collectors: list[CollectorStatus] = Field(default_factory=list)
