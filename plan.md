# Multi-Layer Observability Platform Implementation

## Summary

Build the complete Part 1 platform as a greenfield monorepo, delivered in five working milestones:

1. Shared telemetry contracts, PostgreSQL schema, FastAPI foundation, and Docker Compose.
2. Registration, ingestion, querying, rollups, retention, and Parquet export.
3. Linux reporting agent, application collectors, and PostgreSQL/Redis adapters.
4. Svelte "flight recorder" dashboard.
5. Three-VM deployment automation, autonomous workloads, fault injection, and performance evaluation.

Each milestone will remain runnable and tested. The implementation targets Ubuntu 24.04/systemd, three monitored hosts with up to 25 monitored processes each, and one-second default sampling.

## Core Architecture and Data Flow

- Use Python 3.12 for the FastAPI server and agent, PostgreSQL with native partitioning, SvelteKit/TypeScript for the dashboard, and Caddy as the frontend/API reverse proxy.
- Organize the repository into server, agent, dashboard, demo-workload, deployment, and shared-contract packages. Generate frontend API types from FastAPI's OpenAPI schema.
- Model inventory as:
  - agent -> host;
  - host -> service instances -> process instances;
  - unassigned processes directly under a host;
  - logical services linked to PostgreSQL/Redis dependencies.
- Identify process instances by host, boot ID, PID, and `/proc` start time so PID reuse cannot merge unrelated processes.
- Normalize incoming telemetry into a versioned envelope with:
  - `event_id`, `timestamp`, `observed_timestamp`, and agent sequence;
  - host, service, process, container, and dependency references;
  - `source_type`: system, process, application, or dependency;
  - `signal_type`: metric, log, lifecycle, collector-health, or ground-truth;
  - metric name/value/unit or log severity/type/message;
  - bounded structured attributes.
- Store scalar metrics as definitions, stable series, and timestamped samples. Store logs and fault/experiment events separately while preserving the common envelope fields.
- Use hourly PostgreSQL partitions for raw metrics/logs and daily partitions for rollups. Retain:
  - raw metrics and log messages for at least 24 hours;
  - five-minute metric aggregates and log severity counts for seven days;
  - inventory history and experiment ground truth until explicit deletion.
- Five-minute aggregates contain count, sum, minimum, maximum, average, and last value. Maintenance reprocesses a recent window for late data and drops raw partitions only after their rollups are complete.
- Export RCA datasets by experiment ID as a reproducible Parquet bundle containing metrics, logs, inventory relationships, lifecycle events, ground truth, and a manifest with schema version, time range, configuration, and checksums.

## Server and Agent Interfaces

### FastAPI API

- `POST /api/v1/agents/register`: use a deployment enrollment token to register an installation and return stable agent/host IDs plus a one-time agent credential.
- `PUT /api/v1/agents/me/inventory`: idempotently reconcile discovered services, containers, processes, and dependencies. Missing resources become inactive after a lease rather than being deleted immediately.
- `POST /api/v1/agents/me/heartbeat`: update connectivity, collector health, spool usage, and agent version.
- `POST /api/v1/telemetry/batches`: accept gzip JSON batches, limited to 500 normal events per agent batch and 2,000 events/2 MB compressed at the server boundary.
  - A batch receipt and payload digest make retries deterministic.
  - Event receipts prevent duplication across different batches.
  - Valid events commit atomically; permanent per-event validation failures are returned explicitly.
  - The authenticated agent determines the host identity, preventing cross-host spoofing.
- Read APIs provide topology/resource details, metric catalog and series discovery, ranged metric queries, cursor-paginated logs/events, log histograms, experiment runs, and collector health.
- Metric queries accept entity/series filters, time bounds, aggregation, and `max_points`. They return aligned `min/max/average/last/count` buckets without interpolating missing data.
- `GET /api/v1/live` provides server-sent commit and inventory watermarks. REST remains authoritative; the dashboard refetches only its active range after each watermark and falls back to bounded polling after disconnection.
- Ground-truth write and agent endpoints require scoped bearer credentials. Dashboard reads remain unauthenticated inside the trusted lab network. Caddy handles TLS when a hostname/certificate is configured; local development uses HTTP.
- Provide liveness/readiness endpoints, structured server logs, Prometheus-compatible platform metrics, Alembic migrations, and a separate advisory-lock-protected maintenance worker.

### Reporting agent

- Implement an asyncio daemon with independent registration, discovery, collection, normalization, spool, and sending tasks so a failed adapter cannot delay the one-second host/process loop.
- Collect the SRS host metrics directly from `/proc/stat`, `meminfo`, `loadavg`, `diskstats`, and `/proc/net/dev`, computing rates from monotonic deltas while retaining useful raw counters.
- Reconcile configured processes every five seconds and collect CPU, RSS, virtual memory, faults, I/O, state, threads, descriptors, context switches, PPID, and start time.
- Support ordered service discovery rules for systemd unit, executable, command-line regex, PID file, and container cgroup. First matching service rule wins; all association changes generate lifecycle events.
- Stream journald and newline-delimited files, persisting journal cursors or inode/device/offset state. Handle rotation, truncation, partial lines, late file creation, malformed JSON, and configurable line/rate limits.
- Add an OpenMetrics scraper for application metrics. Direct OTLP/gRPC ingestion is deferred.
- Implement a pluggable dependency-adapter protocol entirely in the agent:
  - PostgreSQL: connectivity latency, transactions, rows/blocks, connections, waits, locks, deadlocks, temporary data, and database size; optional `pg_stat_statements` aggregates without query text.
  - Redis: latency, clients, memory, operations, network traffic, rejected/evicted/expired keys, persistence/replication state, errors, and configured list queue depths.
  - Preserve vendor metric names alongside normalized concepts; the central server contains no PostgreSQL/Redis-specific ingestion logic.
- Buffer every event in SQLite WAL before transmission. Default spool limits are 512 MiB or 24 hours.
  - Send every second or at 500 events/1 MiB.
  - Retry timeouts, `408`, `429`, and `5xx` with jittered exponential backoff.
  - Delete only acknowledged records.
  - Quarantine permanent rejections and emit explicit gap/loss events if capacity forces eviction.
- Install as a hardened `temporalrca-agent` systemd service using a dedicated account, journal access, and narrowly scoped `/proc` capabilities. Unavailable metrics are reported as collector-health errors rather than false zeroes.

## Dashboard, Demonstration, and Deployment

- Build a desktop-first, responsive SvelteKit dashboard using Tailwind, accessible primitives, TanStack Query, and a locally wrapped `uPlot` chart component.
- Apply the frontend-design skill's "dark flight recorder" direction:
  - ground the website's design tokens, component language, interaction states, and concise UI copy in Cloudflare's palette and design system, using Cloudflare orange as the primary brand/action accent while preserving accessible semantic signal colors;
  - recorder-black and navy surfaces, frost text, cyan signals, amber warnings, and coral faults;
  - Barlow Condensed headings, Inter controls/body text, and IBM Plex Mono telemetry;
  - a shared vertical correlation cursor as the signature interaction, aligning metric values, logs, lifecycle events, and injected faults across every lane.
- Provide fleet overview, host, service, process, dependency, unified timeline, and experiment views.
- The unified timeline includes a Host -> Service -> Process resource tree, linked dependencies, stacked telemetry lanes, synchronized brush/zoom, log markers, ground-truth intervals, and a contextual inspector.
- Store time range, timezone, filters, selected streams, and live/paused state in the URL. Default to a live 15-minute range; zooming pauses follow mode until "Return to live" is selected.
- Treat samples older than ten seconds as stale at the default interval. Show ingestion delay using event versus observed timestamps.
- Supply accessible chart summaries, keyboard navigation, shape/icon equivalents for semantic colors, reduced-motion handling, explicit empty/error/retry states, and tablet/mobile inspector/filter drawers.
- Package the central server, dashboard, platform PostgreSQL, migration job, maintenance worker, and Caddy in Docker Compose.
- Provide Ansible automation for three existing Ubuntu VMs without tying deployment to a cloud provider:
  - VM 1: containerized producer/API plus separate monitored PostgreSQL and Redis;
  - VM 2: multiple non-container systemd workers;
  - VM 3: containerized consumers and a continuous file-processing workload;
  - all VMs run the host agent and maintain at least two to three concurrently active workloads.
- Add a YAML experiment runner with allowlisted, bounded, reversible scenarios: CPU/memory pressure, disk I/O, worker termination, network delay, PostgreSQL lock/slowdown, Redis backlog, and process contention. Every scenario records start/end/failure ground truth and runs cleanup.
- Keep direct OTLP, RabbitMQ/Kafka/MySQL/MongoDB adapters, tracing, user accounts/RBAC, cloud provisioning, anomaly detection, and RCA inference outside Part 1.

## Verification and Acceptance

- Unit-test `/proc` parsing and deltas, counter resets, PID reuse, disappearing/permission-denied processes, discovery precedence, log rotation/cursors, adapter normalization, configuration, spool recovery, retry policy, batch deduplication, and time-bucket selection.
- Run PostgreSQL/Redis integration tests for registration, credential rotation, inventory leases, partition boundaries, concurrent duplicate delivery, partial rejection, late/out-of-order data, rollup idempotency, retention, query isolation, sparse gaps, and Parquet schema fidelity.
- Use Vitest and Testing Library for URL state, range math, unit formatting, live/stale behavior, keyboard operation, synchronized cursor behavior, and loading/empty/error states.
- Use Playwright for fleet drill-down, cross-layer timeline correlation, shared range changes, live pause/resume, SSE disconnect/backfill, log filtering, dependency metrics, fault markers, shareable URLs, responsive layouts, accessibility checks, and one stable visual baseline.
- Run an end-to-end three-VM acceptance scenario proving:
  - all hosts, services, dependencies, and 25 processes per host can register and remain correctly related;
  - system, process, application, dependency, and log telemetry arrive at one-second resolution;
  - a ten-minute central outage recovers with no unaccounted loss;
  - injected faults appear against synchronized telemetry with matching ground truth;
  - an experiment exports successfully as a self-contained Parquet bundle.
- Benchmark 500 ms, 1 s, 2 s, and 5 s sampling with fixed workloads, three repetitions, and recorded CPU, RSS, bandwidth, ingestion latency, spool growth, loss, recovery time, and log throughput.
- Reference targets on the selected three-host environment are: agent p95 CPU below 5% of one core, RSS below 150 MiB, connected ingestion p95 below five seconds, zero loss while within spool capacity, and sustained aggregate ingestion of 1,000 structured log events/second during the load test.
