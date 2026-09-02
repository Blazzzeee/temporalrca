from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class SpoolRecord:
    id: int
    event_id: str
    payload: dict[str, Any]
    size: int


class SQLiteSpool:
    """Durable event queue. All database work is moved off the asyncio loop."""

    def __init__(self, path: str | Path, max_bytes: int = 512 * 1024 * 1024, max_age_seconds: int = 86400) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.max_age_seconds = max_age_seconds
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    def _open_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                created REAL NOT NULL, payload BLOB NOT NULL, size INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quarantine (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
                quarantined REAL NOT NULL, reason TEXT NOT NULL, payload BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        connection.commit()
        self._connection = connection

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    async def close(self) -> None:
        if self._connection:
            await asyncio.to_thread(self._connection.close)
            self._connection = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("spool is not open")
        return self._connection

    async def append(self, events: Iterable[dict[str, Any]], state_updates: dict[str, str] | None = None) -> list[dict[str, Any]]:
        encoded = [(str(event["event_id"]), time.time(), json.dumps(event, separators=(",", ":")).encode()) for event in events]
        async with self._lock:
            return await asyncio.to_thread(self._append_sync, encoded, state_updates or {})

    def _append_sync(self, encoded: list[tuple[str, float, bytes]], state_updates: dict[str, str]) -> list[dict[str, Any]]:
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO events(event_id,created,payload,size) VALUES(?,?,?,?)",
                                [(event_id, created, payload, len(payload)) for event_id, created, payload in encoded])
            self.db.executemany("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                list(state_updates.items()))
        return self._enforce_limits_sync()

    def _enforce_limits_sync(self) -> list[dict[str, Any]]:
        cutoff = time.time() - self.max_age_seconds
        rows = self.db.execute("SELECT id,event_id,payload,size FROM events WHERE created < ? ORDER BY id", (cutoff,)).fetchall()
        total = int(self.db.execute("SELECT COALESCE(SUM(size),0) FROM events").fetchone()[0])
        selected = list(rows)
        selected_ids = {row[0] for row in selected}
        total -= sum(row[3] for row in selected)
        for row in self.db.execute("SELECT id,event_id,payload,size FROM events ORDER BY id"):
            if total <= self.max_bytes:
                break
            if row[0] not in selected_ids:
                selected.append(row)
                selected_ids.add(row[0])
            total -= row[3]
        if selected:
            with self.db:
                self.db.executemany("DELETE FROM events WHERE id=?", [(row[0],) for row in selected])
        return [{"event_id": row[1], "reason": "spool_capacity_eviction", "bytes": row[3]} for row in selected]

    async def batch(self, max_events: int = 500, max_bytes: int = 1024 * 1024,
                    *, newest: bool = False) -> list[SpoolRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._batch_sync, max_events, max_bytes, newest)

    def _batch_sync(self, max_events: int, max_bytes: int, newest: bool = False) -> list[SpoolRecord]:
        records: list[SpoolRecord] = []
        total = 0
        direction = "DESC" if newest else "ASC"
        query = f"SELECT id,event_id,payload,size FROM events ORDER BY id {direction} LIMIT ?"
        for row in self.db.execute(query, (max_events,)):
            if records and total + row[3] > max_bytes:
                break
            records.append(SpoolRecord(row[0], row[1], json.loads(row[2]), row[3]))
            total += row[3]
        if newest:
            records.reverse()
        return records

    async def acknowledge(self, event_ids: Iterable[str]) -> None:
        ids = [(item,) for item in event_ids]
        async with self._lock:
            await asyncio.to_thread(self._execute_many, "DELETE FROM events WHERE event_id=?", ids)

    async def quarantine(self, rejections: dict[str, str]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._quarantine_sync, rejections)

    def _quarantine_sync(self, rejections: dict[str, str]) -> None:
        with self.db:
            for event_id, reason in rejections.items():
                row = self.db.execute("SELECT payload FROM events WHERE event_id=?", (event_id,)).fetchone()
                if row:
                    self.db.execute("INSERT INTO quarantine(event_id,quarantined,reason,payload) VALUES(?,?,?,?)",
                                    (event_id, time.time(), reason, row[0]))
                    self.db.execute("DELETE FROM events WHERE event_id=?", (event_id,))

    def _execute_many(self, statement: str, values: list[tuple[Any, ...]]) -> None:
        with self.db:
            self.db.executemany(statement, values)

    async def usage(self) -> dict[str, int]:
        async with self._lock:
            def read() -> dict[str, int]:
                count, size = self.db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM events").fetchone()
                quarantine = self.db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
                return {"events": count, "bytes": size, "quarantine": quarantine}
            return await asyncio.to_thread(read)

    async def get_state(self, key: str) -> str | None:
        async with self._lock:
            row = await asyncio.to_thread(lambda: self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone())
            return row[0] if row else None

    async def set_state(self, key: str, value: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._execute_many, "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(key, value)])
