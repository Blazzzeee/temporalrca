# TemporalRCA shared telemetry contracts

Versioned Pydantic wire models shared by the reporting agent and central server.
The server treats an authenticated agent as authoritative for `host_id`; a host
identifier in an event is only an assertion and must match that identity.

See [DEPENDENCY_TELEMETRY.md](DEPENDENCY_TELEMETRY.md) for the shared,
vendor-neutral PostgreSQL/Redis dependency telemetry convention and the future
Kafka/RabbitMQ adapter boundary.
