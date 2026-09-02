#!/usr/bin/env python3
import hashlib
import json
import os
import time

name = os.getenv("WORKER_NAME", "worker")
while True:
    started = time.monotonic()
    digest = b"temporalrca"
    for _ in range(30000):
        digest = hashlib.sha256(digest).digest()
    print(json.dumps({"timestamp": time.time(), "severity": "INFO", "service": name, "event_type": "cycle_complete", "message": "worker cycle complete", "attributes": {"duration_ms": round((time.monotonic() - started) * 1000, 2)}}), flush=True)
    time.sleep(0.5)

