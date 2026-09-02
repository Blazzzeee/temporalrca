import { describe, expect, it } from 'vitest';
import type { MetricSeries } from '$lib/types/api';
import { dependencyDimensions, filterDependencyMetrics, groupDependencyMetrics } from './dashboard';

const metric = (id: number, name: string, attributes: Record<string, unknown> = {}): MetricSeries => ({
  id: String(id), name, unit: '1', entity_id: 'dependency-1', attributes,
  buckets: [{ timestamp: '2026-09-03T00:00:00Z', min: 1, max: 1, average: 1, last: 1, count: 1 }]
});

describe('dependency dashboard model', () => {
  it('keeps global PostgreSQL streams while filtering the selected database', () => {
    const metrics = [
      metric(1, 'dependency.connectivity.latency'),
      metric(2, 'dependency.rows.inserted.rate', { 'db.namespace': 'workload' }),
      metric(3, 'dependency.rows.inserted.rate', { database: 'postgres' })
    ];
    expect(dependencyDimensions(metrics, 'database')).toEqual(['workload', 'postgres']);
    expect(filterDependencyMetrics(metrics, 'postgresql', 'workload').map(item => item.id)).toEqual(['1', '2']);
  });

  it('groups and retains every stream without a fixed series cutoff', () => {
    const metrics = Array.from({ length: 40 }, (_, index) => metric(index, `dependency.rows.metric_${index}`, { database: 'workload' }));
    const grouped = groupDependencyMetrics(metrics, 'postgresql');
    expect(grouped.flatMap(group => group.metrics)).toHaveLength(40);
  });

  it('filters only queue-dimensional Redis streams', () => {
    const metrics = [
      metric(1, 'dependency.operations.rate'),
      metric(2, 'dependency.queue.depth', { queue: 'jobs' }),
      metric(3, 'dependency.messaging.produced.rate', { 'messaging.destination.name': 'events' }),
      metric(4, 'dependency.messaging.processing.latency', { 'messaging.destination.name': 'events' })
    ];
    expect(dependencyDimensions(metrics, 'queue')).toEqual(['events', 'jobs']);
    expect(filterDependencyMetrics(metrics, 'redis', 'all', 'events').map(item => item.id)).toEqual(['1', '3', '4']);
    expect(groupDependencyMetrics(metrics, 'redis').find(group => group.key === 'queues')?.metrics).toHaveLength(3);
  });
});
