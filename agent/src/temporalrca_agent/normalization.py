from __future__ import annotations

import re
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .models import EntityRef, SignalType, SourceType, TelemetryEvent, utc_now

_KEY = re.compile(r"[^a-zA-Z0-9_.:/-]")


class Normalizer:
    """Builds bounded, versioned envelopes and owns the agent sequence."""

    def __init__(self, host_id: str | None = None, sequence: int = 0) -> None:
        self.host_id = host_id
        self.sequence = sequence

    def _next(self) -> int:
        self.sequence += 1
        return self.sequence

    @staticmethod
    def attributes(values: Mapping[str, Any] | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for index, (key, value) in enumerate((values or {}).items()):
            if index >= 64:
                break
            safe_key = _KEY.sub("_", str(key))[:128]
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[safe_key] = value[:2048] if isinstance(value, str) else value
            else:
                result[safe_key] = str(value)[:2048]
            if len(json.dumps(result, separators=(",", ":"), default=str).encode()) > 16_384:
                del result[safe_key]
                break
        return result

    def event(
        self,
        *,
        source: SourceType,
        signal: SignalType,
        name: str,
        timestamp: str | datetime | None = None,
        entity: EntityRef | None = None,
        value: float | int | None = None,
        unit: str | None = None,
        severity: str | None = None,
        message: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> TelemetryEvent:
        observed = utc_now()
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            timestamp = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
        ref = entity or EntityRef()
        if ref.host_id is None:
            ref.host_id = self.host_id
        return TelemetryEvent(
            timestamp=timestamp or observed,
            observed_timestamp=observed,
            sequence=self._next(),
            source_type=source,
            signal_type=signal,
            name=name[:255],
            entity=ref,
            value=float(value) if value is not None else None,
            unit=unit,
            severity=severity,
            message=message[:65536] if message else None,
            attributes=self.attributes(attributes),
        )
