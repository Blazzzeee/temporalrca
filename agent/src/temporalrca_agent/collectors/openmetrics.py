from __future__ import annotations

import asyncio
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..models import EntityRef, SignalType, SourceType, TelemetryEvent
from ..normalization import Normalizer

_SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+([0-9]+))?$')
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"(?:,|$)')


def _labels(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    result: dict[str, str] = {}
    position = 0
    while position < len(raw):
        match = _LABEL.match(raw, position)
        if not match:
            raise ValueError(f"malformed label set near {raw[position:]!r}")
        result[match.group(1)] = bytes(match.group(2), "utf-8").decode("unicode_escape")
        position = match.end()
    return result


def parse_openmetrics(text: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    types: dict[str, str] = {}
    units: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "# EOF":
            continue
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split(maxsplit=3)
            types[name] = kind
            continue
        if line.startswith("# UNIT "):
            _, _, name, unit = line.split(maxsplit=3)
            units[name] = unit
            continue
        if line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if not match:
            continue
        name, labels, value, timestamp = match.groups()
        try:
            number = float(value)
        except ValueError:
            continue
        family = name.removesuffix("_total") if name.endswith("_total") else name
        samples.append({"name": name, "value": number, "labels": _labels(labels), "timestamp_ms": int(timestamp) if timestamp else None,
                        "metric_type": types.get(name, types.get(family)), "unit": units.get(name, units.get(family, "1"))})
    return samples


@dataclass(slots=True)
class OpenMetricsCollector:
    name: str
    url: str
    normalizer: Normalizer
    service_id: str | None = None
    timeout: float = 3

    def _fetch(self) -> str:
        request = urllib.request.Request(self.url, headers={"Accept": "application/openmetrics-text; version=1.0.0, text/plain"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read(4 * 1024 * 1024).decode(errors="replace")

    async def collect(self) -> list[TelemetryEvent]:
        text = await asyncio.to_thread(self._fetch)
        result: list[TelemetryEvent] = []
        for sample in parse_openmetrics(text):
            attributes = {"collector": self.name, "metric_type": sample["metric_type"], **sample["labels"]}
            result.append(self.normalizer.event(source=SourceType.APPLICATION, signal=SignalType.METRIC,
                          name=sample["name"], value=sample["value"], unit=sample["unit"], attributes=attributes,
                          entity=EntityRef(service_id=self.service_id)))
        return result

