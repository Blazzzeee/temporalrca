from __future__ import annotations

import time
from typing import Any

from ..models import EntityRef, SignalType, SourceType, TelemetryEvent
from ..normalization import Normalizer
from ..procfs import ProcFS, counter_rate, cpu_utilization


class ProcCollector:
    def __init__(self, proc: ProcFS, normalizer: Normalizer) -> None:
        self.proc = proc
        self.normalizer = normalizer
        self.previous_system: dict[str, Any] | None = None
        self.previous_process: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
        self.collected_at: float | None = None

    def collect_system(self) -> list[TelemetryEvent]:
        now = time.monotonic()
        sample = self.proc.system()
        elapsed = now - self.collected_at if self.collected_at is not None else 0
        events: list[TelemetryEvent] = []
        old = self.previous_system or {}
        for cpu, counters in sample["stat"]["cpus"].items():
            attrs = {} if cpu == "cpu" else {"cpu": cpu[3:]}
            utilization = cpu_utilization(counters, old.get("stat", {}).get("cpus", {}).get(cpu))
            if utilization is not None:
                events.append(self._metric("system.cpu.utilization", utilization, "%", attrs))
            for name, value in counters.items():
                events.append(self._metric(f"system.cpu.{name}.ticks", value, "ticks", attrs))
        memory = sample["meminfo"]
        for source, name in (("MemTotal", "system.memory.total"), ("MemAvailable", "system.memory.available"),
                             ("SwapTotal", "system.swap.total"), ("SwapFree", "system.swap.free")):
            if source in memory:
                events.append(self._metric(name, memory[source], "By"))
        for key in ("load1", "load5", "load15", "tasks_running", "tasks_total"):
            events.append(self._metric(f"system.{key}", sample["loadavg"][key], "1"))
        for key in ("ctxt", "processes", "procs_running", "procs_blocked"):
            if key in sample["stat"]:
                events.append(self._metric(f"system.{key}", sample["stat"][key], "1"))
        for key in ("pswpin", "pswpout", "pgfault", "pgmajfault"):
            if key in sample["vmstat"]:
                value = sample["vmstat"][key]
                events.append(self._metric(f"system.vm.{key}", value, "1"))
                rate = counter_rate(value, old.get("vmstat", {}).get(key), elapsed)
                if rate.value is not None:
                    events.append(self._metric(f"system.vm.{key}.rate", rate.value, "1/s"))
        for device, counters in sample["disks"].items():
            for key, value in counters.items():
                events.append(self._metric(f"system.disk.{key}", value, "1", {"device": device}))
                rate = counter_rate(value, old.get("disks", {}).get(device, {}).get(key), elapsed)
                if rate.value is not None and key != "io_in_progress":
                    events.append(self._metric(f"system.disk.{key}.rate", rate.value, "1/s", {"device": device}))
        for interface, counters in sample["net"].items():
            for key, value in counters.items():
                events.append(self._metric(f"system.network.{key}", value, "By" if "bytes" in key else "1", {"interface": interface}))
                rate = counter_rate(value, old.get("net", {}).get(interface, {}).get(key), elapsed)
                if rate.value is not None:
                    events.append(self._metric(f"system.network.{key}.rate", rate.value, "By/s" if "bytes" in key else "1/s", {"interface": interface}))
        self.previous_system, self.collected_at = sample, now
        return events

    def collect_process(self, process: dict[str, Any], process_id: str, service_id: str | None = None,
                        container_id: str | None = None) -> list[TelemetryEvent]:
        now = time.monotonic()
        identity = (int(process["pid"]), int(process["start_time_ticks"]))
        previous_entry = self.previous_process.get(identity)
        previous = previous_entry[1] if previous_entry else {}
        elapsed = now - previous_entry[0] if previous_entry else 0
        ref = EntityRef(process_id=process_id, service_id=service_id, container_id=container_id)
        status, io = process.get("status", {}), process.get("io", {})
        metrics = {
            "process.memory.rss": (process.get("rss_bytes"), "By"), "process.memory.virtual": (process.get("virtual_bytes"), "By"),
            "process.faults.minor": (process.get("minor_faults"), "1"), "process.faults.major": (process.get("major_faults"), "1"),
            "process.threads": (process.get("threads"), "1"), "process.file_descriptors": (process.get("fd_count"), "1"),
            "process.io.read_bytes": (io.get("read_bytes"), "By"), "process.io.write_bytes": (io.get("write_bytes"), "By"),
            "process.context_switches.voluntary": (status.get("voluntary_ctxt_switches"), "1"),
            "process.context_switches.involuntary": (status.get("nonvoluntary_ctxt_switches"), "1"),
        }
        events = [self._metric(name, value, unit, entity=ref) for name, (value, unit) in metrics.items() if value is not None]
        ticks = int(process.get("utime_ticks", 0)) + int(process.get("stime_ticks", 0))
        old_ticks = int(previous.get("utime_ticks", 0)) + int(previous.get("stime_ticks", 0)) if previous else None
        rate = counter_rate(ticks, old_ticks, elapsed)
        if rate.value is not None:
            events.append(self._metric("process.cpu.utilization", rate.value / self.proc.clock_ticks * 100, "%", entity=ref))
        events.append(self._metric("process.info", 1, "1", {"pid": process["pid"], "ppid": process["ppid"],
                            "name": process["name"], "state": process["state"], "start_time_ticks": process["start_time_ticks"]}, ref))
        self.previous_process[identity] = (now, process)
        # Remove an earlier incarnation of a reused PID immediately.
        for key in [key for key in self.previous_process if key[0] == identity[0] and key != identity]:
            del self.previous_process[key]
        return events

    def _metric(self, name: str, value: float | int, unit: str, attributes: dict[str, Any] | None = None,
                entity: EntityRef | None = None) -> TelemetryEvent:
        return self.normalizer.event(source=SourceType.PROCESS if name.startswith("process.") else SourceType.SYSTEM,
                                     signal=SignalType.METRIC, name=name, value=value, unit=unit,
                                     attributes=attributes, entity=entity)
