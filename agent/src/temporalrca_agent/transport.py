from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import random
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5


@dataclass(slots=True)
class HTTPResult:
    status: int
    body: dict[str, Any]


class AgentHTTPClient:
    def __init__(self, base_url: str, credential: str | None = None, timeout: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any], *, compressed: bool = False,
                 bearer: str | None = None, timeout: float | None = None) -> HTTPResult:
        raw = json.dumps(body, separators=(",", ":")).encode()
        data = gzip.compress(raw) if compressed else raw
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if compressed:
            headers["Content-Encoding"] = "gzip"
        token = bearer if bearer is not None else self.credential
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                payload = response.read()
                return HTTPResult(response.status, json.loads(payload) if payload else {})
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                body_value = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                body_value = {"detail": payload.decode(errors="replace")}
            return HTTPResult(error.code, body_value)

    async def register(self, enrollment_token: str, installation_id: str, host_name: str,
                       host_attributes: dict[str, str] | None = None) -> dict[str, Any]:
        result = await asyncio.to_thread(self._request, "POST", "/api/v1/agents/register",
                 {"enrollment_token": enrollment_token, "installation_id": installation_id,
                  "host_external_id": installation_id, "host_name": host_name, "agent_version": "0.1.0",
                  "host_attributes": host_attributes or {}}, bearer="")
        if result.status not in (200, 201):
            raise RuntimeError(f"registration failed ({result.status}): {result.body}")
        self.credential = result.body["credential"]
        return result.body

    async def inventory(self, inventory: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(self._request, "PUT", "/api/v1/agents/me/inventory", inventory)
        if result.status not in (200, 204):
            raise RuntimeError(f"inventory failed ({result.status})")
        return result.body

    async def heartbeat(self, body: dict[str, Any]) -> None:
        result = await asyncio.to_thread(self._request, "POST", "/api/v1/agents/me/heartbeat", body)
        if result.status not in (200, 202, 204):
            raise RuntimeError(f"heartbeat failed ({result.status})")

    async def send(self, events: list[dict[str, Any]]) -> HTTPResult:
        digest = hashlib.sha256(json.dumps(events, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        # A retried payload has the same batch ID and exact body.
        sequences = [int(event["sequence"]) for event in events]
        body = {"schema_version": "1.0", "batch_id": str(uuid5(NAMESPACE_URL, digest)),
                "created_at": events[0]["observed_timestamp"], "first_sequence": min(sequences),
                "last_sequence": max(sequences), "backfill": len(events) > 500, "events": events}
        # Backfill writes can legitimately wait behind another agent's
        # partition transaction. Do not time out and immediately retry a batch
        # that the server is still committing.
        return await asyncio.to_thread(
            self._request, "POST", "/api/v1/telemetry/batches", body,
            compressed=True, timeout=max(60.0, self.timeout),
        )


def retryable(status: int | None) -> bool:
    # Authentication failures are operational/configuration failures, not
    # permanent event rejections, so retain the spool while credentials recover.
    return status is None or status in (401, 403, 408, 429) or (status >= 500)


def backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 30, random_value: float | None = None) -> float:
    # Full jitter avoids synchronized reconnect storms.
    ceiling = min(cap, base * (2 ** max(0, attempt)))
    return ceiling * (random.random() if random_value is None else random_value)
