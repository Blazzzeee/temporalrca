# Dependency telemetry convention

This is the vendor-neutral Part 1 contract for dependency metrics. Every
dependency stream uses the existing metric envelope (`name`, `value`/buckets,
`unit`, timestamps, and `attributes`) and a normalized `dependency.*` name.
Collectors may retain the source name in `vendor_metric_name`; dashboards and
alerts must use the normalized name and attributes.

## Identity and namespaces

- `dependency.system` is required and identifies the backend: `postgresql`,
  `redis`, `kafka`, or `rabbitmq`.
- Database streams use the `db` namespace. `db.system` is the database
  technology, `db.namespace` is the logical database/tenant scope, and
  `db.name` is the server-reported database name when available. Database
  selectors should key on `db.namespace` (falling back to `db.name` only when
  no namespace is supplied).
- Messaging streams use `messaging.system`,
  `messaging.destination.name`, and `messaging.destination.kind`. Destination
  kind is normally `queue`, `topic`, or `stream`; a Redis stream/list adapter
  should report its equivalent kind rather than a vendor-prefixed value.

Attributes are strings, stable for the lifetime of a stream, and should be
present on both totals and rates. A dependency-wide aggregate may omit the
database or destination attributes.

## Normalized queue and stream signals

For each destination, adapters may report the following names (units are shown
in parentheses):

| Name | Meaning |
| --- | --- |
| `dependency.queue.depth` (`1`) | Items currently waiting or retained. |
| `dependency.messaging.produced` (`1`) | Monotonic produced total. |
| `dependency.messaging.produced.rate` (`1/s`) | Produced throughput. |
| `dependency.messaging.consumed` (`1`) | Monotonic consumed total. |
| `dependency.messaging.consumed.rate` (`1/s`) | Consumed throughput. |
| `dependency.messaging.processing.latency` (`s`) | Processing latency; report the aggregation (for example, `cumulative_mean`, `interval_mean`, or `p95`) in `aggregation`. |
| `dependency.messaging.failures` (`1`) | Monotonic failed-processing total. |
| `dependency.messaging.failures.rate` (`1/s`) | Failed-processing throughput. |
| `dependency.messaging.oldest_item.age` (`s`) | Age of the oldest currently waiting or retained item. |

Totals are preferred as cumulative counters and rates are per-second derived
streams. If a backend only exposes a gauge or interval count, set the metric's
temporality/aggregation metadata rather than presenting it as a counter.

For Redis lists, depth and oldest age describe the waiting queue. For the
non-destructive Redis Stream used by the demo, they describe retained entries;
produced/consumed rates and processing latency describe live flow.

## Database and backend signals

PostgreSQL and Redis use the same envelope and identity rules. Their existing
backend-specific signals may remain normalized under `dependency.*` (for
example, connectivity, operations, memory, transactions, storage, and locks),
but must carry `dependency.system` and the relevant `db.*` attributes.

Kafka and RabbitMQ adapters are **out of scope for Part 1**. If introduced in a
future part, they must emit this schema—including the `messaging.*` identity
attributes and normalized produced/consumed, latency, failure, depth, and
oldest-item signals—rather than adding vendor-specific dashboard contracts.
