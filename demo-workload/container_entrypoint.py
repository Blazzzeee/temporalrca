"""Run the demo workload and its telemetry agent as one container node."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("a workload role is required")

    service = os.environ.get("SERVICE_NAME", sys.argv[1])
    os.environ.setdefault("TEMPORALRCA_SERVICE_NAME", service)
    os.environ.setdefault("TEMPORALRCA_HOST_NAME", service)
    os.environ.setdefault("TEMPORALRCA_INSTALLATION_ID", f"compose-workload:{service}")

    agent = subprocess.Popen([
        "temporalrca-agent", "--config", "/etc/temporalrca-agent/container.toml", "run",
    ])
    workload = subprocess.Popen([sys.executable, "-m", "workload", *sys.argv[1:]])

    def stop(signum: int, _frame: object) -> None:
        for child in (workload, agent):
            if child.poll() is None:
                child.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            if (workload_status := workload.poll()) is not None:
                return workload_status
            if (agent_status := agent.poll()) is not None:
                workload.terminate()
                return agent_status
            time.sleep(0.25)
    finally:
        for child in (workload, agent):
            if child.poll() is None:
                child.terminate()
        for child in (workload, agent):
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    raise SystemExit(main())
