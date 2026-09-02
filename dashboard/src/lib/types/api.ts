export type Health = 'healthy' | 'degraded' | 'offline' | 'unknown';
export type EntityKind = 'host' | 'service' | 'process' | 'container' | 'dependency';
export interface FleetSummary { hosts: number; services: number; processes: number; containers: number; dependencies: number; healthy: number; degraded: number; offline: number; ingestion_delay_ms: number; }
export interface Entity { id: string; name: string; kind: EntityKind; health: Health; host_id?: string; agent_id?: string; parent_id?: string; container_id?: string; dependency_type?: string; related_ids?: string[]; pid?: number; active: boolean; last_seen: string; labels?: Record<string,string>; attributes?: Record<string, unknown>; command?: string; children?: Entity[]; }
export interface MetricBucket { timestamp: string; min: number | null; max: number | null; average: number | null; last: number | null; count: number; }
export interface MetricSeries { id: string; name: string; unit: string; entity_id: string; entity_name?: string; source_type?: string; attributes?: Record<string, unknown>; buckets: MetricBucket[]; }
export interface MetricCatalogEntry { id: string; name: string; unit: string; series_count: number; }
export interface TimelineEvent { id: string; timestamp: string; observed_timestamp?: string; entity_id?: string; signal_type: 'log'|'lifecycle'|'ground-truth'|'collector-health'; severity?: 'debug'|'info'|'warning'|'error'|'critical'; message: string; type?: string; end_timestamp?: string; attributes?: Record<string, unknown>; }
export interface Experiment { id: string; name: string; status: 'planned'|'running'|'completed'|'failed'; started_at?: string; ended_at?: string; scenario?: string; }
export interface CollectorHealth { name: string; status: Health; message?: string; updated_at: string; agent_id?: string; agent_version?: string; spool_bytes?: number; spool_events?: number; }
export interface CursorPage<T> { items: T[]; next_cursor: string | null; }
export interface LiveWatermark { type: 'commit'|'inventory'; timestamp: string; sequence?: number; }
