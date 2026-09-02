from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ServiceRule


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    boot_id: str
    pid: int
    start_time_ticks: int

    @property
    def key(self) -> str:
        return f"{self.boot_id}:{self.pid}:{self.start_time_ticks}"


def _pidfile_matches(rule: ServiceRule, process: dict[str, Any]) -> bool:
    if not rule.pid_file:
        return False
    try:
        return int(Path(rule.pid_file).read_text().strip()) == process["pid"]
    except (OSError, ValueError, KeyError):
        return False


def matches(rule: ServiceRule, process: dict[str, Any]) -> bool:
    """Matcher fields within a rule are ORed; rule list order supplies precedence."""
    cgroup = str(process.get("cgroup", ""))
    executable = str(process.get("exe", ""))
    command = str(process.get("cmdline", ""))
    return any((
        bool(rule.systemd_unit and rule.systemd_unit in cgroup),
        bool(rule.executable and executable == rule.executable),
        bool(rule.command_regex and re.search(rule.command_regex, command)),
        _pidfile_matches(rule, process),
        bool(rule.container_cgroup and rule.container_cgroup in cgroup),
    ))


def assign_service(rules: list[ServiceRule], process: dict[str, Any]) -> str | None:
    for rule in rules:
        if matches(rule, process):
            return rule.service
    return None


@dataclass(slots=True)
class AssociationChange:
    identity: ProcessIdentity
    previous_service: str | None
    service: str | None
    kind: str


class DiscoveryState:
    def __init__(self) -> None:
        self.associations: dict[ProcessIdentity, str | None] = {}

    def reconcile(self, boot_id: str, processes: list[dict[str, Any]], rules: list[ServiceRule]) -> list[AssociationChange]:
        current: dict[ProcessIdentity, str | None] = {}
        changes: list[AssociationChange] = []
        for process in processes:
            identity = ProcessIdentity(boot_id, int(process["pid"]), int(process["start_time_ticks"]))
            service = assign_service(rules, process)
            current[identity] = service
            if identity not in self.associations:
                changes.append(AssociationChange(identity, None, service, "process.started"))
            elif self.associations[identity] != service:
                changes.append(AssociationChange(identity, self.associations[identity], service, "process.association_changed"))
        for identity, service in self.associations.items():
            if identity not in current:
                changes.append(AssociationChange(identity, service, None, "process.stopped"))
        self.associations = current
        return changes

    def inventory(self) -> dict[str, Any]:
        services: dict[str, list[dict[str, Any]]] = {}
        unassigned: list[dict[str, Any]] = []
        for identity, service in self.associations.items():
            process = {"instance_key": identity.key, "pid": identity.pid, "start_time_ticks": identity.start_time_ticks,
                       "boot_id": identity.boot_id}
            if service is None:
                unassigned.append(process)
            else:
                services.setdefault(service, []).append(process)
        return {"services": [{"name": name, "processes": processes} for name, processes in sorted(services.items())],
                "unassigned_processes": unassigned}


_CONTAINER_PATTERNS = (
    re.compile(r"(?:docker-|/docker/)([0-9a-f]{12,64})(?:\.scope)?"),
    re.compile(r"(?:libpod-|/libpod/)([0-9a-f]{12,64})(?:\.scope)?"),
    re.compile(r"(?:cri-containerd-|crio-)([0-9a-f]{12,64})(?:\.scope)?"),
)


def container_id(cgroup: str) -> str | None:
    for pattern in _CONTAINER_PATTERNS:
        if match := pattern.search(cgroup):
            return match.group(1)
    return None
