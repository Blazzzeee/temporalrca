from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def parse_proc_stat(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"cpus": {}}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        name = fields[0]
        if name.startswith("cpu"):
            values = [int(value) for value in fields[1:]]
            keys = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal", "guest", "guest_nice")
            result["cpus"][name] = dict(zip(keys, values, strict=False))
        elif name in {"ctxt", "processes", "procs_running", "procs_blocked"}:
            result[name] = int(fields[1])
    return result


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        fields = raw.split()
        if not fields:
            continue
        value = int(fields[0])
        if len(fields) > 1 and fields[1].lower() == "kb":
            value *= 1024
        values[key] = value
    return values


def parse_loadavg(text: str) -> dict[str, float | int]:
    one, five, fifteen, tasks, last_pid = text.split()[:5]
    running, total = tasks.split("/", 1)
    return {"load1": float(one), "load5": float(five), "load15": float(fifteen),
            "tasks_running": int(running), "tasks_total": int(total), "last_pid": int(last_pid)}


def parse_diskstats(text: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    keys = ("reads_completed", "reads_merged", "sectors_read", "read_ms", "writes_completed",
            "writes_merged", "sectors_written", "write_ms", "io_in_progress", "io_ms", "weighted_io_ms",
            "discards_completed", "discards_merged", "sectors_discarded", "discard_ms", "flushes_completed", "flush_ms")
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        result[fields[2]] = dict(zip(keys, (int(value) for value in fields[3:]), strict=False))
    return result


def parse_net_dev(text: str) -> dict[str, dict[str, int]]:
    keys = ("rx_bytes", "rx_packets", "rx_errors", "rx_dropped", "rx_fifo", "rx_frame", "rx_compressed",
            "rx_multicast", "tx_bytes", "tx_packets", "tx_errors", "tx_dropped", "tx_fifo", "tx_collisions",
            "tx_carrier", "tx_compressed")
    result: dict[str, dict[str, int]] = {}
    for line in text.splitlines()[2:]:
        interface, separator, raw = line.partition(":")
        if separator:
            result[interface.strip()] = dict(zip(keys, (int(value) for value in raw.split()), strict=False))
    return result


def parse_process_stat(text: str) -> dict[str, int | str]:
    # comm may contain spaces or closing parentheses, so the final ')' is authoritative.
    opening, closing = text.find("("), text.rfind(")")
    if opening < 0 or closing < opening:
        raise ValueError("malformed /proc/<pid>/stat")
    pid = int(text[:opening].strip())
    tail = text[closing + 1:].split()
    if len(tail) < 22:
        raise ValueError("incomplete /proc/<pid>/stat")
    return {
        "pid": pid, "name": text[opening + 1:closing], "state": tail[0], "ppid": int(tail[1]),
        "minor_faults": int(tail[7]), "major_faults": int(tail[9]), "utime_ticks": int(tail[11]),
        "stime_ticks": int(tail[12]), "threads": int(tail[17]), "start_time_ticks": int(tail[19]),
        "virtual_bytes": int(tail[20]), "rss_pages": int(tail[21]),
    }


def parse_status(text: str) -> dict[str, int | str]:
    result: dict[str, int | str] = {}
    wanted = {"Name", "State", "VmRSS", "VmSize", "Threads", "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"}
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if not separator or key not in wanted:
            continue
        fields = raw.split()
        if fields and fields[0].isdigit():
            value = int(fields[0])
            if len(fields) > 1 and fields[1].lower() == "kb":
                value *= 1024
            result[key] = value
        else:
            result[key] = raw.strip()
    return result


def parse_io(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if separator:
            result[key] = int(raw.strip())
    return result


def parse_vmstat(text: str) -> dict[str, int]:
    return {fields[0]: int(fields[1]) for line in text.splitlines() if len(fields := line.split()) == 2}


@dataclass(slots=True)
class Delta:
    value: float | None
    reset: bool = False


def counter_rate(current: int, previous: int | None, elapsed: float) -> Delta:
    if previous is None or elapsed <= 0:
        return Delta(None)
    if current < previous:
        return Delta(None, reset=True)
    return Delta((current - previous) / elapsed)


def cpu_utilization(current: dict[str, int], previous: dict[str, int] | None) -> float | None:
    if previous is None:
        return None
    idle = current.get("idle", 0) + current.get("iowait", 0)
    old_idle = previous.get("idle", 0) + previous.get("iowait", 0)
    total = sum(current.get(key, 0) for key in ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"))
    old_total = sum(previous.get(key, 0) for key in ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"))
    delta_total = total - old_total
    delta_idle = idle - old_idle
    if delta_total <= 0 or delta_idle < 0:
        return None
    return max(0.0, min(100.0, 100.0 * (delta_total - delta_idle) / delta_total))


class ProcFS:
    def __init__(self, root: str | Path = "/proc") -> None:
        self.root = Path(root)
        self.clock_ticks = os.sysconf("SC_CLK_TCK")
        self.page_size = os.sysconf("SC_PAGE_SIZE")

    def _read(self, relative: str) -> str:
        return (self.root / relative).read_text(errors="replace")

    def boot_id(self) -> str:
        return self._read("sys/kernel/random/boot_id").strip()

    def system(self) -> dict[str, Any]:
        return {"stat": parse_proc_stat(self._read("stat")), "meminfo": parse_meminfo(self._read("meminfo")),
                "loadavg": parse_loadavg(self._read("loadavg")), "disks": parse_diskstats(self._read("diskstats")),
                "net": parse_net_dev(self._read("net/dev")), "vmstat": parse_vmstat(self._read("vmstat"))}

    def pids(self) -> list[int]:
        return sorted(int(path.name) for path in self.root.iterdir() if path.name.isdigit() and path.is_dir())

    def process(self, pid: int) -> dict[str, Any]:
        stat = parse_process_stat(self._read(f"{pid}/stat"))
        status = parse_status(self._read(f"{pid}/status"))
        io = parse_io(self._read(f"{pid}/io"))
        stat.update({"rss_bytes": int(stat["rss_pages"]) * self.page_size, "status": status, "io": io,
                     "fd_count": len(list((self.root / str(pid) / "fd").iterdir()))})
        for optional in ("cmdline", "exe", "cgroup"):
            try:
                if optional == "exe":
                    stat[optional] = os.readlink(self.root / str(pid) / optional)
                else:
                    stat[optional] = self._read(f"{pid}/{optional}").replace("\0", " ").strip()
            except (FileNotFoundError, PermissionError, OSError):
                stat[optional] = ""
        return stat
