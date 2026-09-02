# TemporalRCA observability platform

TemporalRCA is a self-contained multi-layer telemetry platform for a three-host research lab. Its Linux agent collects host, process, application, dependency, lifecycle, and log signals; the FastAPI service stores and queries them in partitioned PostgreSQL; and the Svelte flight-recorder dashboard correlates them on one timeline.

Prometheus is not part of the deployment. Workloads expose the standard OpenMetrics text format and the TemporalRCA agent scrapes it directly. TemporalRCA owns the transport, PostgreSQL storage, query engine, retention, rollups, dashboard, and Parquet exports. The API's `/metrics` endpoint exposes only platform health counters in a Prometheus-compatible format.

## Run everything with Compose

Docker with Compose is the only local prerequisite. Copy `.env.example` to `.env` and replace its development secrets, then run:

```bash
docker compose up --build --wait
```

On Linux, set `DOCKER_GID` in `.env` to the output of
`stat -c '%g' /var/run/docker.sock`. This lets the non-root host agent collect
Docker CPU, memory, network, and block-I/O samples; cgroup-based inventory alone
does not provide those measurements.

Open `http://localhost:8080`. The API is also available on `http://localhost:8000`; Caddy is the normal frontend/API entry point. The default project includes:

- the migration job, FastAPI server, maintenance worker, dashboard, and Caddy;
- a dedicated platform PostgreSQL database;
- separately monitored PostgreSQL and Redis instances;
- steady, burst, and priority producers with dedicated consumers;
- Redis list queues, a Redis event stream, periodic database analytics, cron-style report and cleanup jobs, and continuous file processing;
- a host/process/application/dependency reporting agent with persistent SQLite spool.

The dashboard opens in a rolling 24-hour live window, provides 1-hour through 7-day presets, and includes a **Workloads** view that aligns job throughput, in-flight work, queue pressure, events, cron runs, database operations, and the active service inventory. PostgreSQL and Redis dependency pages expose the full collected metric catalog. Redis destinations include depth, produced/consumed rates, processing latency, failures, and oldest-item age. Dependency samples use vendor-neutral concepts and retain their original vendor metric name; Kafka and RabbitMQ adapters remain outside Part 1 as specified in `plan.md`.

The database migration must finish successfully before the API starts, and health checks gate all downstream services. Stop the lab with `docker compose down`. Add `--volumes` only when you intentionally want to delete all local telemetry and credentials.

## Compose-operated checks and tools

Once the test profiles are built, the complete suite is run without host Python or Node tooling:

```bash
docker compose --profile test run --rm server-test
docker compose --profile test run --rm agent-test
docker compose --profile test run --rm dashboard-test
docker compose --profile test run --rm demo-test
```

Run the bounded example fault scenarios with:

```bash
docker compose --profile experiments run --rm experiment-runner
```

The experiment runner accepts only the scenarios listed in `plan.md`, caps duration and resource values, records start/end/failure ground truth, and always executes cleanup for reversible faults. Replace the example host with an inventory host visible to the API before running it.

Generate browser API declarations from the running FastAPI OpenAPI document using the dashboard code-generation Compose service described by `docker compose --profile tools config`.

## Three-VM deployment and evaluation

The `deployment/ansible` playbook targets three existing Ubuntu 24.04 VMs: VM1 hosts the central stack, producer, and monitored dependencies; VM2 runs three ordinary systemd workers; VM3 runs containerized consumers and continuous file processing. Every VM receives the hardened host agent.

Use a containerized Ansible invocation from the repository root so the deployment also needs no host package installation:

```bash
docker compose --profile deployment run --rm ansible \
  ansible-playbook -i deployment/ansible/inventory.yml deployment/ansible/site.yml
```

See `deployment/acceptance/README.md` for the outage recovery and export procedure. The benchmark harness runs the four planned sample intervals for three repetitions and writes a machine-readable CSV.

## Repository layout

- `shared/`: versioned telemetry and API contracts
- `server/`: FastAPI, PostgreSQL schema/migrations, ingestion, queries, maintenance, and export
- `agent/`: asyncio Linux agent, collectors, dependency adapters, spool, and packaging
- `dashboard/`: SvelteKit flight-recorder interface
- `demo-workload/`: autonomous producers, consumers, event handlers, cron jobs, database analytics, and file processing
- `deployment/`: Ansible, experiments, acceptance, and performance harnesses
- `compose.yml`: complete local runtime and containerized developer tooling

`plan.md` is the implementation source of truth; `srs.md` contains the underlying requirements.
