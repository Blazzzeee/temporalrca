from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import SessionLocal
from .models import (
    ContainerInstance, Dependency, Experiment, Host, InventorySnapshot, LogEvent, MetricDefinition,
    MetricSample, MetricSeries, ProcessInstance, ServiceDependency,
    ServiceInstance, TimelineEvent,
)

EXPORT_SCHEMA_VERSION = "1.0"


def _value(value: Any) -> Any:
    if isinstance(value, uuid.UUID): return str(value)
    if isinstance(value, (dict, list)): return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return value


def _rows(items) -> list[dict[str, Any]]:
    return [{column.name: _value(getattr(item, column.name)) for column in item.__table__.columns} for item in items]


def _arrow_type(column) -> pa.DataType:
    python_type = getattr(column.type, "python_type", str)
    if python_type is datetime: return pa.timestamp("us", tz="UTC")
    if python_type is float: return pa.float64()
    if python_type is int: return pa.int64()
    if python_type is bool: return pa.bool_()
    return pa.string()


def _write_parquet(path: Path, rows: list[dict[str, Any]], model) -> None:
    if rows:
        table = pa.Table.from_pylist(rows)
    else:
        table = pa.table({column.name: pa.array([], type=_arrow_type(column)) for column in model.__table__.columns})
    pq.write_table(table, path, compression="zstd", use_dictionary=True)


async def export_experiment(session: AsyncSession, experiment_id: uuid.UUID, output_dir: Path) -> Path:
    experiment = await session.get(Experiment, experiment_id)
    if experiment is None:
        raise ValueError("experiment not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / f"experiment-{experiment_id}"
    work.mkdir(exist_ok=True)

    metrics = list((await session.execute(select(MetricSample).where(MetricSample.experiment_id == experiment_id).order_by(MetricSample.timestamp, MetricSample.event_id))).scalars())
    logs = list((await session.execute(select(LogEvent).where(LogEvent.experiment_id == experiment_id).order_by(LogEvent.timestamp, LogEvent.event_id))).scalars())
    events = list((await session.execute(select(TimelineEvent).where(TimelineEvent.experiment_id == experiment_id).order_by(TimelineEvent.timestamp, TimelineEvent.event_id))).scalars())
    series_ids = {x.series_id for x in metrics}
    series = list((await session.execute(select(MetricSeries).where(MetricSeries.id.in_(series_ids)))).scalars()) if series_ids else []
    definition_ids = {x.metric_definition_id for x in series}
    definitions = list((await session.execute(select(MetricDefinition).where(MetricDefinition.id.in_(definition_ids)))).scalars()) if definition_ids else []
    host_ids = {x.host_id for x in logs + events} | {x.host_id for x in series}
    services = list((await session.execute(select(ServiceInstance).where(ServiceInstance.host_id.in_(host_ids)))).scalars()) if host_ids else []
    processes = list((await session.execute(select(ProcessInstance).where(ProcessInstance.host_id.in_(host_ids)))).scalars()) if host_ids else []
    containers = list((await session.execute(select(ContainerInstance).where(ContainerInstance.host_id.in_(host_ids)))).scalars()) if host_ids else []
    dependencies = list((await session.execute(select(Dependency).where(Dependency.host_id.in_(host_ids)))).scalars()) if host_ids else []
    service_ids = {x.id for x in services}
    links = list((await session.execute(select(ServiceDependency).where(ServiceDependency.service_id.in_(service_ids)))).scalars()) if service_ids else []
    hosts = list((await session.execute(select(Host).where(Host.id.in_(host_ids)))).scalars()) if host_ids else []
    snapshots = list((await session.execute(select(InventorySnapshot).where(InventorySnapshot.host_id.in_(host_ids)).order_by(InventorySnapshot.observed_at))).scalars()) if host_ids else []

    datasets = {
        "metrics.parquet": (metrics, MetricSample), "metric_series.parquet": (series, MetricSeries),
        "metric_definitions.parquet": (definitions, MetricDefinition), "logs.parquet": (logs, LogEvent),
        "lifecycle_ground_truth.parquet": (events, TimelineEvent), "hosts.parquet": (hosts, Host),
        "services.parquet": (services, ServiceInstance), "processes.parquet": (processes, ProcessInstance),
        "containers.parquet": (containers, ContainerInstance), "dependencies.parquet": (dependencies, Dependency),
        "service_dependencies.parquet": (links, ServiceDependency),
        "inventory_history.parquet": (snapshots, InventorySnapshot),
    }
    checksums: dict[str, str] = {}
    for filename, (items, model) in datasets.items():
        target = work / filename
        _write_parquet(target, _rows(items), model)
        checksums[filename] = hashlib.sha256(target.read_bytes()).hexdigest()
    timestamps = [x.timestamp for x in metrics + logs + events]
    manifest = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "experiment_id": str(experiment.id), "experiment_name": experiment.name,
        "status": experiment.status,
        "time_range": {
            "start": (min(timestamps) if timestamps else experiment.started_at).isoformat(),
            "end": (max(timestamps) if timestamps else experiment.ended_at or experiment.started_at).isoformat(),
        },
        "configuration": experiment.configuration,
        "files": checksums,
    }
    manifest_path = work / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    bundle = output_dir / f"experiment-{experiment_id}.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        export_files = [work / name for name in sorted(datasets)] + [manifest_path]
        for file in export_files:
            info = zipfile.ZipInfo(file.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.read_bytes())
    return bundle


async def _run(experiment_id: uuid.UUID, output_dir: Path) -> None:
    async with SessionLocal() as session:
        print(await export_experiment(session, experiment_id, output_dir))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_id", type=uuid.UUID)
    parser.add_argument("--output", type=Path, default=Path(get_settings().export_directory))
    args = parser.parse_args()
    asyncio.run(_run(args.experiment_id, args.output))
