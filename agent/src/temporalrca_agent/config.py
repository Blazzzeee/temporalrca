from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ServiceRule:
    service: str
    systemd_unit: str | None = None
    executable: str | None = None
    command_regex: str | None = None
    pid_file: str | None = None
    container_cgroup: str | None = None

    def __post_init__(self) -> None:
        if self.command_regex:
            re.compile(self.command_regex)
        if not any((self.systemd_unit, self.executable, self.command_regex, self.pid_file, self.container_cgroup)):
            raise ValueError(f"service {self.service!r} requires a discovery matcher")


@dataclass(slots=True)
class LogSource:
    name: str
    type: str
    path: str | None = None
    unit: str | None = None
    service: str | None = None
    max_line_bytes: int = 65536
    max_lines_per_second: int = 1000


@dataclass(slots=True)
class MetricsEndpoint:
    name: str
    url: str
    service: str | None = None
    timeout_seconds: float = 3.0


@dataclass(slots=True)
class Dependency:
    name: str
    type: str
    dsn: str
    interval_seconds: float = 5.0
    queues: list[str] = field(default_factory=list)
    streams: list[str] = field(default_factory=list)
    queue_stats_key: str = "temporalrca:telemetry:queue_stats"
    services: list[str] = field(default_factory=list)
    pg_stat_statements: bool = False


@dataclass(slots=True)
class Config:
    central_server: str
    enrollment_token: str | None = None
    credential: str | None = None
    installation_id: str | None = None
    host_name: str = ""
    host_attributes: dict[str, str] = field(default_factory=dict)
    state_dir: Path = Path("/var/lib/temporalrca-agent")
    proc_root: Path = Path("/proc")
    collection_interval_seconds: float = 1.0
    discovery_interval_seconds: float = 5.0
    max_monitored_processes: int = 25
    spool_max_bytes: int = 512 * 1024 * 1024
    spool_max_age_seconds: int = 24 * 3600
    docker_socket: Path = Path("/var/run/docker.sock")
    docker_collection_enabled: bool = True
    docker_collection_interval_seconds: float = 1.0
    services: list[ServiceRule] = field(default_factory=list)
    logs: list[LogSource] = field(default_factory=list)
    metrics: list[MetricsEndpoint] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.central_server.startswith(("http://", "https://")):
            raise ValueError("central_server must be an HTTP(S) URL")
        if self.collection_interval_seconds < 0.1:
            raise ValueError("collection interval must be at least 100ms")
        if self.spool_max_bytes <= 0 or self.spool_max_age_seconds <= 0:
            raise ValueError("spool limits must be positive")
        if self.max_monitored_processes <= 0:
            raise ValueError("max_monitored_processes must be positive")
        if self.docker_collection_interval_seconds < 0.1:
            raise ValueError("docker collection interval must be at least 100ms")


def _records(cls: type, values: list[dict[str, Any]] | None) -> list[Any]:
    return [cls(**value) for value in values or []]


def load_config(path: str | Path) -> Config:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    # Secrets can be provisioned without writing them into the config file.
    enrollment = os.getenv("TEMPORALRCA_ENROLLMENT_TOKEN", raw.get("enrollment_token"))
    credential = os.getenv("TEMPORALRCA_AGENT_CREDENTIAL", raw.get("credential"))
    enabled_raw = os.getenv("TEMPORALRCA_DOCKER_COLLECTION_ENABLED", raw.get("docker_collection_enabled", True))
    if isinstance(enabled_raw, str):
        docker_enabled = enabled_raw.strip().lower() not in {"0", "false", "no", "off"}
    else:
        docker_enabled = bool(enabled_raw)
    host_name = os.getenv("TEMPORALRCA_HOST_NAME", raw.get("host_name", os.uname().nodename))
    installation_id = os.getenv("TEMPORALRCA_INSTALLATION_ID", raw.get("installation_id"))
    host_attributes = {str(key): str(value) for key, value in raw.get("host_attributes", {}).items()}
    if node_kind := os.getenv("TEMPORALRCA_NODE_KIND"):
        host_attributes["node_kind"] = node_kind
    if runtime := os.getenv("TEMPORALRCA_NODE_RUNTIME"):
        host_attributes["runtime"] = runtime

    services = _records(ServiceRule, raw.get("services"))
    logs = _records(LogSource, raw.get("logs"))
    metrics = _records(MetricsEndpoint, raw.get("metrics"))
    dependencies = _records(Dependency, raw.get("dependencies"))
    # Container images use one generic config. Runtime environment identifies
    # the workload process and its local telemetry endpoint so every reporting
    # container becomes a node with the process inside it represented as a
    # service.
    service_name = os.getenv("TEMPORALRCA_SERVICE_NAME")
    if service_name:
        host_attributes.setdefault("service", service_name)
        if not any(item.service == service_name for item in services):
            services.append(ServiceRule(
                service_name,
                command_regex=os.getenv("TEMPORALRCA_SERVICE_COMMAND_REGEX", r"^python -m workload(?:\s|$)"),
            ))
        if endpoint := os.getenv("TEMPORALRCA_METRICS_URL"):
            metrics.append(MetricsEndpoint(service_name, endpoint, service_name))
        if log_path := os.getenv("TEMPORALRCA_LOG_PATH"):
            logs.append(LogSource(f"{service_name}-logs", "file", path=log_path, service=service_name))
        discover_dependencies = os.getenv("TEMPORALRCA_DISCOVER_RUNTIME_DEPENDENCIES", "").lower() in {
            "1", "true", "yes", "on",
        }
        if discover_dependencies:
            dependency_interval = float(os.getenv("TEMPORALRCA_DEPENDENCY_INTERVAL_SECONDS", "15"))
            if dsn := os.getenv("MONITORED_DATABASE_URL"):
                dependencies.append(Dependency(
                    "postgresql", "postgresql", dsn,
                    interval_seconds=dependency_interval, services=[service_name],
                ))
            if dsn := os.getenv("REDIS_URL"):
                dependencies.append(Dependency(
                    "redis", "redis", dsn,
                    interval_seconds=dependency_interval, services=[service_name],
                ))

    return Config(
        # Deployment can keep one image/config for local and remote Compose
        # workloads while supplying the VM1 URL at runtime.
        central_server=os.getenv("TEMPORALRCA_SERVER_URL", raw["central_server"]).rstrip("/"),
        enrollment_token=enrollment,
        credential=credential,
        installation_id=installation_id,
        host_name=host_name,
        host_attributes=host_attributes,
        state_dir=Path(raw.get("state_dir", "/var/lib/temporalrca-agent")),
        proc_root=Path(raw.get("proc_root", "/proc")),
        collection_interval_seconds=float(os.getenv("TEMPORALRCA_COLLECTION_INTERVAL_SECONDS",
                                                        raw.get("collection_interval_seconds", 1))),
        discovery_interval_seconds=float(raw.get("discovery_interval_seconds", 5)),
        max_monitored_processes=int(raw.get("max_monitored_processes", 25)),
        spool_max_bytes=int(raw.get("spool_max_bytes", 512 * 1024 * 1024)),
        spool_max_age_seconds=int(raw.get("spool_max_age_seconds", 86400)),
        docker_socket=Path(os.getenv("TEMPORALRCA_DOCKER_SOCKET", raw.get("docker_socket", "/var/run/docker.sock"))),
        docker_collection_enabled=docker_enabled,
        docker_collection_interval_seconds=float(os.getenv("TEMPORALRCA_DOCKER_COLLECTION_INTERVAL_SECONDS",
                                                            raw.get("docker_collection_interval_seconds", 1))),
        services=services,
        logs=logs,
        metrics=metrics,
        dependencies=dependencies,
    )
