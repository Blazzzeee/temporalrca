
# Software Requirements Specification

## Multi-Layer Cloud Observability and Telemetry Platform

## 1. Purpose

The system shall provide a lightweight observability platform for distributed cloud applications. It will collect and centralize telemetry from multiple layers, including:

* host/system metrics,
* application-level logs and metrics,
* process-level `/proc` telemetry,
* infrastructure dependencies such as databases and message queues.

The collected data shall be visualized through an interactive web dashboard and later consumed by the Probable Temporal Root Cause Analysis component.

---

## 2. System Architecture

The platform shall consist of:

### Reporting Agent

A lightweight agent deployed on each monitored VM.

Responsibilities:

* collect system telemetry,
* collect `/proc` process telemetry,
* stream application logs,
* collect application and dependency metrics,
* normalize telemetry into a common format,
* batch and transmit telemetry to the central server,
* automatically associate telemetry with registered hosts, services and processes.

The agent shall be easy to register with new applications and machines with minimal configuration.

### Central Server

Implemented using **FastAPI**.

Responsibilities:

* receive telemetry streams from agents,
* register agents, hosts and services,
* validate and normalize incoming telemetry,
* store telemetry in PostgreSQL,
* expose APIs for querying telemetry,
* provide data to the dashboard,
* maintain host → process → application relationships.

### Database

**PostgreSQL** shall be used as the centralized telemetry database for the initial system.

### Web Dashboard

Implemented using **Svelte**.

The dashboard shall provide interactive visualization and exploration of all collected telemetry.

---

## 3. Deployment/Test Environment

The evaluation environment shall contain multiple Linux VMs representing a small distributed cloud environment.

At minimum:

* 3 VMs,
* multiple services,
* at least one database,
* at least one message queue,
* multiple concurrently executing processes.

The environment shall contain both:

### Isolated services

Services running using Docker containers.

Example:

```text
VM 1
 ├── Docker: Service A
 └── Docker: Redis
```

### Non-isolated services

Multiple normal Linux processes running concurrently on the same VM.

Example:

```text
VM 2
 ├── Worker A
 ├── Worker B
 ├── Background processor
 └── Reporting Agent
```

This allows the system to observe both container-isolated and shared-host resource behaviour.

---

## 4. Autonomous Workloads

Participating applications shall operate without continuous user interaction.

The test environment should contain at least **2–3 concurrently active workloads at any time**.

Suitable workloads include:

* background task processors,
* message producers and consumers,
* scheduled jobs,
* data processing pipelines,
* database workloads,
* queue-based workers,
* file processing applications,
* synthetic workload generators.

Applications requiring a human to continuously interact with a web UI shall not be the primary workload.

Workloads should continuously generate:

```text
CPU activity
memory activity
disk I/O
network activity
database operations
message queue operations
application logs
```

so that meaningful telemetry is continuously available.

---

# 5. Telemetry Requirements

## 5.1 System-Level Telemetry

The reporting agent shall collect host-level information including:

* CPU utilization,
* per-core CPU activity,
* load average,
* memory usage,
* swap activity,
* disk I/O,
* network traffic,
* network errors/drops,
* process counts,
* context switches.

Linux interfaces such as the following may be used:

```text
/proc/stat
/proc/meminfo
/proc/loadavg
/proc/diskstats
/proc/net/*
```

Typical collection interval:

**1 second**

---

## 5.2 Process-Level Telemetry

For monitored processes the agent shall collect:

* PID,
* PPID,
* process name,
* CPU usage,
* RSS,
* virtual memory,
* page faults,
* bytes read/written,
* process state,
* number of threads,
* file descriptor count,
* context switches,
* process start time.

Sources shall primarily include:

```text
/proc/<pid>/stat
/proc/<pid>/status
/proc/<pid>/io
/proc/<pid>/fd
```

The system shall maintain:

```text
Host
   ↓
Process
   ↓
Service/Application
```

relationships.

---

# 5.3 Application Logs

Applications shall emit structured logs where possible.

The logging model shall be compatible with standard structured telemetry conventions such as OpenTelemetry.

Each log should support fields such as:

```text
timestamp
severity
service
host
PID
event type
message
attributes
```

Logs shall be streamed continuously rather than periodically polled.

High-verbosity logging shall be supported for controlled experiments.

---

# 5.4 Application Metrics

Applications may expose metrics including:

* request/job counts,
* processing duration,
* error counts,
* active operations,
* completed operations,
* internal queue size.

Applications should expose these using a common telemetry mechanism such as:

* OTLP,
* OpenMetrics/Prometheus-compatible endpoints,
* reporting-agent adapters.

---

# 5.5 Database and Message Queue Telemetry

The platform shall support external infrastructure components through a **pluggable adapter/receiver architecture**.

Examples include:

```text
PostgreSQL
Redis
RabbitMQ
Kafka
MySQL
MongoDB
```

The central system shall not contain database-specific logic.

Adapters shall translate dependency-specific telemetry into the common telemetry representation.

Supported generic concepts should include:

```text
connections
operations rate
latency
errors
queue depth
consumer lag
memory usage
storage usage
locks/waits
throughput
```

Original vendor-specific metric names shall also be retained.

---

# 6. Common Telemetry Model

All telemetry shall use a common envelope containing information such as:

```text
event_id
timestamp
observed_timestamp

host_id
service_id
process_id
container_id

source_type
metric/event name
value
unit

attributes
```

`source_type` shall include:

```text
system
process
application
dependency
```

This common representation will later be consumed by the RCA system.

---

# 7. Agent Registration

A new reporting agent shall be able to register with the central FastAPI server.

Registration shall establish:

```text
agent
 ↓
host
 ↓
services
 ↓
processes
```

The registration process should require minimal configuration.

Example configuration:

```text
central_server
host_name
services
process discovery rules
log sources
dependency adapters
```

Service/PID mappings should be discovered automatically wherever practical.

---

# 8. Dashboard Requirements

The dashboard is a core requirement of Part 1.

It shall provide an intuitive visual representation of collected telemetry rather than simply exposing raw tables.

## 8.1 Infrastructure Overview

Display:

* connected hosts,
* running services,
* running processes,
* infrastructure dependencies,
* current resource utilization.

---

## 8.2 Host Dashboard

For each VM display graphs for:

* CPU,
* memory,
* disk activity,
* network activity,
* system load.

The page shall also show currently active monitored processes.

---

## 8.3 Process/Service Dashboard

Display:

* process CPU,
* RSS,
* I/O,
* page faults,
* threads,
* file descriptors,
* application metrics,
* application logs.

Logs and process metrics should be viewable on a synchronized timeline.

---

## 8.4 Dependency Dashboard

Display metrics for databases and message queues.

Examples:

```text
PostgreSQL
    connections
    latency
    transactions
    locks

Redis
    memory
    clients
    operations
    queue-related metrics

Kafka/RabbitMQ
    queue depth
    throughput
    consumer lag
```

---

## 8.5 Unified Timeline

The dashboard shall provide a synchronized graph view across telemetry layers.

Example:

```text
Time ────────────────────────────────────────────>

Host CPU          ───────████████────────────

Process RSS       ─────────██████████─────────

Database latency  ───────────████████─────────

Queue depth       ─────────────██████████─────

Application logs  ───────────────X──X──X──────
```

Users shall be able to compare multiple telemetry streams over the same time interval.

This view is particularly important because it forms the visual basis for the later temporal RCA system.

---

# 9. Dashboard Interaction

Graphs shall support:

* time-range selection,
* zooming,
* synchronized timelines,
* host filtering,
* service filtering,
* process filtering,
* telemetry-layer filtering,
* viewing logs associated with a selected time period,
* viewing processes associated with a host/service.

The interface should make relationships between:

```text
host → process → service → dependency
```

easy to understand.

---

# 10. Telemetry Transport

Agents shall send telemetry to the FastAPI server using an efficient streaming or batched transport mechanism.

The system should support:

* batching,
* retry,
* temporary local buffering,
* connection recovery,
* timestamp preservation.

Loss of connectivity to the central collector should not immediately result in telemetry loss.

---

# 11. Performance Requirements

The reporting agent shall remain lightweight.

The project shall evaluate:

* CPU overhead,
* memory overhead,
* network bandwidth,
* telemetry ingestion latency,
* data loss,
* sustainable log throughput.

Experiments should compare different sampling intervals such as:

```text
500 ms
1 second
2 seconds
5 seconds
```

The default target shall be approximately **1-second sampling for important system and process metrics**.

---

# 12. Fault and Workload Generation

The environment shall support controlled workload and failure generation.

Examples include:

* CPU saturation,
* memory pressure,
* high disk I/O,
* process termination,
* network delay,
* database slowdown,
* queue backlog,
* concurrent-process resource contention.

Injected events shall be recorded as ground truth for later RCA evaluation.

---

# 13. Technology Stack

### Backend

```text
Python
FastAPI
PostgreSQL
```

### Reporting Agent

```text
Python
Linux /proc
structured log collectors
dependency adapters
```

### Frontend

```text
Svelte
interactive time-series graphing
```

### Deployment

```text
Linux VMs
Docker for isolated workloads
normal Linux processes for non-isolated workloads
```

---

# 14. Out of Scope for Part 1

Part 1 shall not attempt to implement:

* machine-learning anomaly detection,
* causal discovery,
* root-cause prediction,
* GNNs,
* LLM-based log analysis,
* distributed tracing as a mandatory requirement,
* production-scale multi-tenancy,
* enterprise RBAC,
* a full replacement for Prometheus/Grafana.

These may be considered extensions where appropriate.

---

# 15. Part 1 Output

The final output of Part 1 shall be:

```text
Distributed autonomous workloads
              ↓
Lightweight reporting agents
              ↓
System + /proc + application + dependency telemetry
              ↓
Central FastAPI telemetry server
              ↓
PostgreSQL
              ↓
Interactive Svelte observability dashboard
              ↓
Normalized temporal telemetry dataset
              ↓
Input for Part 2: Probable Temporal RCA
```

The primary objective is to establish a reliable, extensible and visually useful multi-layer observability foundation upon which the probable temporal RCA system can operate.
