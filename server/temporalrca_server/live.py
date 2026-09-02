import asyncio
import json
from collections.abc import AsyncIterator


class LiveWatermarks:
    def __init__(self) -> None:
        self.commit = 0
        self.inventory = 0
        self._condition = asyncio.Condition()

    async def publish(self, kind: str) -> int:
        async with self._condition:
            if kind == "commit":
                self.commit += 1
                value = self.commit
            else:
                self.inventory += 1
                value = self.inventory
            self._condition.notify_all()
            return value

    async def events(self) -> AsyncIterator[str]:
        seen = (-1, -1)
        while True:
            current = (self.commit, self.inventory)
            if current != seen:
                seen = current
                yield "event: watermark\ndata: " + json.dumps(
                    {"commit": current[0], "inventory": current[1]}
                ) + "\n\n"
            try:
                async with self._condition:
                    await asyncio.wait_for(self._condition.wait(), timeout=15)
            except TimeoutError:
                yield ": keepalive\n\n"


watermarks = LiveWatermarks()
