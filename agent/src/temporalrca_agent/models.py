from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


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


@dataclass(slots=True)
class EntityRef:
    host_id: str | None = None
    service_id: str | None = None
    process_id: str | None = None
    container_id: str | None = None
    dependency_id: str | None = None


@dataclass(slots=True)
class TelemetryEvent:
    timestamp: str
    observed_timestamp: str
    sequence: int
    source_type: SourceType
    signal_type: SignalType
    name: str
    event_id: str = field(default_factory=lambda: str(uuid4()))
    entity: EntityRef = field(default_factory=EntityRef)
    value: float | None = None
    unit: str | None = None
    severity: str | None = None
    message: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_type"] = self.source_type.value
        value["signal_type"] = self.signal_type.value
        # Keep refs flat on the wire, matching the common server envelope.
        value.update(value.pop("entity"))
        return {key: item for key, item in value.items() if item is not None}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

