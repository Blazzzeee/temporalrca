import type { MetricSeries } from '$lib/types/api';

export type DependencyMetricGroup = {
  key: string;
  label: string;
  description: string;
  metrics: MetricSeries[];
};

const POSTGRES_GROUPS = [
  { key: 'connections', label: 'Connections', description: 'Reachability and active database sessions.', match: (name: string) => name.includes('connectivity') || name === 'dependency.connections' },
  { key: 'transactions', label: 'Transactions', description: 'Commit and rollback counters with derived rates.', match: (name: string) => name.includes('.transactions.') },
  { key: 'rows', label: 'Row activity', description: 'Rows scanned, fetched, inserted, updated, and deleted.', match: (name: string) => name.includes('.rows.') },
  { key: 'cache', label: 'Cache & storage', description: 'Block access, temporary data, and database footprint.', match: (name: string) => name.includes('.blocks.') || name.includes('.temporary.') || name.includes('.storage.') },
  { key: 'contention', label: 'Contention & errors', description: 'Locks, waits, deadlocks, and conflicts.', match: (name: string) => name.includes('.locks') || name.includes('.errors') }
];

const REDIS_GROUPS = [
  { key: 'queues', label: 'Queues & streams', description: 'Depth, throughput, processing latency, failures, and oldest-item age.', match: (name: string) => name.includes('.queue.') || name.includes('.messaging.') },
  { key: 'traffic', label: 'Commands & traffic', description: 'Command throughput and network movement.', match: (name: string) => name.includes('.operations') || name.includes('.network.') || name.includes('connectivity') },
  { key: 'resources', label: 'Clients & memory', description: 'Connected clients and Redis memory pressure.', match: (name: string) => name === 'dependency.connections' || name.includes('.memory.') },
  { key: 'reliability', label: 'Reliability', description: 'Rejected clients, errors, key churn, and persistence state.', match: (name: string) => name.includes('.errors') || name.includes('.connections.rejected') || name.includes('.keys.') || name.includes('.persistence.') },
  { key: 'replication', label: 'Replication', description: 'Node role and replication progress.', match: (name: string) => name.includes('.replication.') }
];

export function dependencyDimensions(metrics: MetricSeries[], key: 'database' | 'queue'): string[] {
  const values = metrics
    .map(metric => key === 'queue'
      ? metric.attributes?.['messaging.destination.name'] || metric.attributes?.queue
      : metric.attributes?.['db.namespace'] || metric.attributes?.['db.name'] || metric.attributes?.database)
    .filter((value): value is string => typeof value === 'string' && value.length > 0);
  return [...new Set(values)].sort((a, b) => {
    if (a === 'workload') return -1;
    if (b === 'workload') return 1;
    return a.localeCompare(b);
  });
}

export function filterDependencyMetrics(
  metrics: MetricSeries[],
  kind: string,
  selectedDatabase = 'all',
  selectedQueue = 'all'
): MetricSeries[] {
  return metrics.filter(metric => {
    if (kind === 'postgresql' && selectedDatabase !== 'all') {
      const database = metric.attributes?.['db.namespace'] || metric.attributes?.['db.name'] || metric.attributes?.database;
      if (database && database !== selectedDatabase) return false;
    }
    if (kind === 'redis' && selectedQueue !== 'all') {
      const queue = metric.attributes?.['messaging.destination.name'] || metric.attributes?.queue;
      if (queue && queue !== selectedQueue) return false;
    }
    return true;
  });
}

export function groupDependencyMetrics(metrics: MetricSeries[], kind: string): DependencyMetricGroup[] {
  const definitions = kind === 'postgresql' ? POSTGRES_GROUPS : kind === 'redis' ? REDIS_GROUPS : [
    { key: 'connectivity', label: 'Connectivity', description: 'Dependency reachability and response time.', match: (name: string) => name.includes('connectivity') },
  ];
  const remaining = new Set(metrics);
  const result = definitions.map(definition => {
    const matching = metrics.filter(metric => remaining.has(metric) && definition.match(metric.name));
    matching.forEach(metric => remaining.delete(metric));
    return { key: definition.key, label: definition.label, description: definition.description, metrics: matching };
  }).filter(group => group.metrics.length > 0);
  if (remaining.size) result.push({ key: 'other', label: 'Other signals', description: 'Additional dependency metrics reported by the adapter.', metrics: [...remaining] });
  return result;
}
