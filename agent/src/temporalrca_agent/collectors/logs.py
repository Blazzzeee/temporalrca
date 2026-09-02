from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import EntityRef, SignalType, SourceType, TelemetryEvent
from ..normalization import Normalizer
from ..spool import SQLiteSpool


@dataclass(slots=True)
class FileCursor:
    device: int
    inode: int
    offset: int
    partial: bytes = b""
    partial_truncated: bool = False


class FileLogCollector:
    def __init__(self, name: str, path: str | Path, normalizer: Normalizer, spool: SQLiteSpool,
                 max_line_bytes: int = 65536, max_lines: int = 1000, service_id: str | None = None) -> None:
        self.name, self.path, self.normalizer, self.spool = name, Path(path), normalizer, spool
        self.max_line_bytes, self.max_lines, self.service_id = max_line_bytes, max_lines, service_id
        self.cursor: FileCursor | None = None
        self._pending: tuple[FileCursor, str] | None = None

    async def restore(self) -> None:
        raw = await self.spool.get_state(f"file:{self.name}")
        if raw:
            state = json.loads(raw)
            self.cursor = FileCursor(state["device"], state["inode"], state["offset"],
                                     bytes.fromhex(state.get("partial", "")), state.get("partial_truncated", False))

    def _read(self) -> tuple[list[tuple[bytes, bool]], FileCursor] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        cursor = self.cursor
        rotated = cursor is None or (cursor.device, cursor.inode) != (stat.st_dev, stat.st_ino)
        truncated = cursor is not None and not rotated and stat.st_size < cursor.offset
        offset = 0 if rotated or truncated else cursor.offset
        partial = b"" if rotated or truncated else cursor.partial
        partial_was_truncated = False if rotated or truncated else cursor.partial_truncated
        with self.path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(self.max_line_bytes * self.max_lines + 1)
            final_offset = handle.tell()
        combined = partial + chunk
        pieces = combined.split(b"\n")
        trailing = pieces.pop() if pieces else combined
        lines = [(line, partial_was_truncated if index == 0 else False) for index, line in enumerate(pieces[:self.max_lines])]
        if len(pieces) > self.max_lines:
            consumed = sum(len(line) + 1 for line in pieces[:self.max_lines])
            # Re-read unconsumed bytes next time. Bytes from the previous partial
            # do not contribute to the on-disk offset.
            final_offset = offset + max(0, consumed - len(partial))
            trailing = b""
        trailing_truncated = (partial_was_truncated and not pieces) or len(trailing) > self.max_line_bytes
        return lines, FileCursor(stat.st_dev, stat.st_ino, final_offset, trailing[:self.max_line_bytes], trailing_truncated)

    async def collect(self) -> list[TelemetryEvent]:
        result = await asyncio.to_thread(self._read)
        if result is None:
            return []
        lines, next_cursor = result
        state = json.dumps({"device": next_cursor.device, "inode": next_cursor.inode,
                            "offset": next_cursor.offset, "partial": next_cursor.partial.hex(),
                            "partial_truncated": next_cursor.partial_truncated})
        self._pending = (next_cursor, state)
        return [self._event(line, forced_truncated) for line, forced_truncated in lines]

    def pending_state_update(self) -> tuple[str, str] | None:
        if self._pending is None:
            return None
        return f"file:{self.name}", self._pending[1]

    def commit_state_update(self) -> None:
        if self._pending:
            self.cursor = self._pending[0]
            self._pending = None

    def _event(self, line: bytes, forced_truncated: bool = False) -> TelemetryEvent:
        truncated = forced_truncated or len(line) > self.max_line_bytes
        text = line[:self.max_line_bytes].decode(errors="replace")
        attributes: dict[str, Any] = {"log.source": self.name, "log.file.path": str(self.path), "truncated": truncated}
        severity, timestamp, message = "INFO", None, text
        try:
            structured = json.loads(text)
            if isinstance(structured, dict):
                severity = str(structured.get("severity", structured.get("level", severity))).upper()
                timestamp = structured.get("timestamp")
                message = str(structured.get("message", structured.get("msg", text)))
                attributes.update({f"log.{key}": value for key, value in structured.items()
                                   if key not in {"severity", "level", "timestamp", "message", "msg"}})
        except json.JSONDecodeError:
            attributes["parse_error"] = True
        return self.normalizer.event(source=SourceType.APPLICATION, signal=SignalType.LOG, name="application.log",
                    timestamp=timestamp, severity=severity, message=message, attributes=attributes,
                    entity=EntityRef(service_id=self.service_id))


class JournalCollector:
    """Reads finite journalctl pages; its cursor is committed with the resulting events."""

    def __init__(self, name: str, unit: str, normalizer: Normalizer, spool: SQLiteSpool, max_lines: int = 1000,
                 service_id: str | None = None) -> None:
        self.name, self.unit, self.normalizer, self.spool, self.max_lines = name, unit, normalizer, spool, max_lines
        self.service_id = service_id
        self._pending_cursor: str | None = None

    async def collect(self) -> list[TelemetryEvent]:
        cursor = await self.spool.get_state(f"journal:{self.name}")
        command = ["journalctl", "--output=json", "--no-pager", f"--lines={self.max_lines}", f"--unit={self.unit}"]
        if cursor:
            command.append(f"--after-cursor={cursor}")
        else:
            command.append("--since=now")
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace").strip())
        events: list[TelemetryEvent] = []
        final_cursor = cursor
        for line in stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            final_cursor = record.get("__CURSOR", final_cursor)
            realtime = record.get("__REALTIME_TIMESTAMP")
            timestamp = None
            if realtime:
                from datetime import UTC, datetime
                timestamp = datetime.fromtimestamp(int(realtime) / 1_000_000, UTC)
            events.append(self.normalizer.event(source=SourceType.APPLICATION, signal=SignalType.LOG, name="journald.log",
                timestamp=timestamp, severity=str(record.get("PRIORITY", "6")), message=str(record.get("MESSAGE", "")),
                attributes={"journal.unit": self.unit, "journal.cursor": final_cursor or ""},
                entity=EntityRef(service_id=self.service_id)))
        if final_cursor and final_cursor != cursor:
            self._pending_cursor = final_cursor
        return events

    def pending_state_update(self) -> tuple[str, str] | None:
        if self._pending_cursor is None:
            return None
        return f"journal:{self.name}", self._pending_cursor

    def commit_state_update(self) -> None:
        self._pending_cursor = None
