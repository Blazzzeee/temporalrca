# TemporalRCA server

The FastAPI service persists inventory and telemetry in PostgreSQL. Schema setup is
performed separately with `alembic upgrade head`; `temporalrca-maintenance` runs as
an advisory-lock-protected worker in its own Compose service.

Required production environment variables:

- `TEMPORALRCA_DATABASE_URL`
- `TEMPORALRCA_ENROLLMENT_TOKEN`
- `TEMPORALRCA_GROUND_TRUTH_TOKEN`
- `TEMPORALRCA_CREDENTIAL_PEPPER`

The API is served on port 8000. Liveness, readiness and Prometheus-compatible
platform instrumentation are exposed at `/health/live`, `/health/ready`, and
`/metrics`; no separate metrics database is required.
