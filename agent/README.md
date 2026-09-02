# TemporalRCA reporting agent

The agent is a Python 3.12 asyncio daemon. It discovers process instances using
the boot ID, PID, and `/proc` start time; collects host/process/application and
dependency telemetry; commits every event to a SQLite WAL spool; and sends gzip
batches to the central API.

Install with `pip install ./agent`. Copy `config.example.toml`, set
`TEMPORALRCA_ENROLLMENT_TOKEN` for the first run, then validate with:

```shell
temporalrca-agent --config /etc/temporalrca-agent/config.toml check-config
```

The systemd assets create a dedicated unprivileged account with journal access.
PostgreSQL and Redis support use the `postgres` and `redis` optional extras.
The Docker image is useful for development; bind-mount host `/proc` at a separate
read-only path and set `proc_root` to that path when host telemetry is required.
