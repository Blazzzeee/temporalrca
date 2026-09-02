<script lang="ts">
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { api, PROCESS_METRIC_NAMES, SYSTEM_METRIC_NAMES } from '$lib/api/client';
  import type { Entity, MetricSeries } from '$lib/types/api';
  import StatePanel from '$lib/components/StatePanel.svelte';
  import StatusMark from '$lib/components/StatusMark.svelte';
  import MetricChart from '$lib/components/MetricChart.svelte';
  import DependencyDashboard from '$lib/components/DependencyDashboard.svelte';
  import { correlationCursor } from '$lib/state/cursor';
  import { parseDashboardState } from '$lib/state/url';
  import { dashboardNow } from '$lib/state/clock';
  import { formatMetric, formatRange, formatTime, isStale } from '$lib/utils/format';

  let entity: Entity | undefined;
  let metrics: MetricSeries[] = [];
  let loading = true;
  let error = '';
  let mounted = false;
  let showAllMetrics = false;
  let loadedKey = '';
  let parentHost: Entity | undefined;
  let navigationEntities: Entity[] = [];
  $: range = parseDashboardState($page.url, $dashboardNow);
  $: routeKind = $page.params.kind || '';
  $: resourceId = $page.params.id || '';
  $: kind = ({ hosts: 'host', services: 'service', processes: 'process', containers: 'container', dependencies: 'dependency' } as Record<string, string>)[routeKind] || routeKind;
  $: queryKey = [resourceId, range.start.toISOString(), range.end.toISOString()].join('|');
  $: hostTelemetry = kind === 'host';
  $: groups = metricGroups(metrics);
  $: containerMetrics = metrics.filter(item => item.name.startsWith('container.'));
  $: containerGroups = containerMetricGroups(containerMetrics);
  $: containerGroupedCount = Object.values(containerGroups).reduce((total, items) => total + items.length, 0);
  $: resourceGroups = entity ? [{ key: 'services', label: 'Services', items: entity.children?.filter(item => item.kind === 'service') || [] }, { key: 'processes', label: 'Processes', items: entity.children?.filter(item => item.kind === 'process') || [] }, { key: 'containers', label: 'Containers', items: entity.children?.filter(item => item.kind === 'container') || [] }, { key: 'dependencies', label: 'Dependencies', items: entity.children?.filter(item => item.kind === 'dependency') || [] }] : [];
  $: dependencyTelemetry = kind === 'dependency';
  $: processTelemetry = kind === 'process';
  $: dependencyKind = entity?.dependency_type || '';
  // Container resources have a dedicated four-panel view below. Keep their
  // process streams in the generic detail list, but do not render the same
  // container.* streams a second time (which inflated a container page to
  // nearly thirty charts and made it appear to load without settling).
  $: detailMetrics = kind === 'container' ? metrics.filter(item => !item.name.startsWith('container.')) : metrics;
  $: visibleMetrics = showAllMetrics ? detailMetrics : detailMetrics.slice(0, hostTelemetry ? 32 : 36);

  async function load() {
    loadedKey = queryKey; loading = true; error = '';
    try {
      const [nextEntity, nextNavigationEntities] = await Promise.all([api.entity(kind, resourceId), api.entities()]);
      entity = nextEntity;
      navigationEntities = nextNavigationEntities;
      parentHost = kind === 'host' ? nextEntity : nextNavigationEntities.find(item => item.kind === 'host' && item.id === nextEntity.host_id);
      const names = kind === 'host' ? SYSTEM_METRIC_NAMES : kind === 'process' ? PROCESS_METRIC_NAMES : undefined;
      metrics = await api.metrics(entity.id, range.start.toISOString(), range.end.toISOString(), 300, undefined, names, kind as Entity['kind']);
    } catch (e) { error = e instanceof Error ? e.message : 'Unable to read resource'; }
    finally { loading = false; }
  }
  function metricGroups(items: MetricSeries[]) {
    const result: Record<string, MetricSeries[]> = { cpu: [], memory: [], network: [], disk: [], process: [], other: [] };
    for (const item of items) {
      const group = item.name.startsWith('system.cpu') || item.name.startsWith('system.load') ? 'cpu' : item.name.startsWith('system.memory') ? 'memory' : item.name.startsWith('system.network') ? 'network' : item.name.startsWith('system.disk') ? 'disk' : item.name.startsWith('process.') ? 'process' : 'other';
      result[group].push(item);
    }
    return result;
  }
  function value(name: string, mode: 'first' | 'sum' = 'first') {
    const values = metrics.filter(item => item.name === name).map(item => item.buckets.at(-1)?.last).filter((item): item is number => item != null && Number.isFinite(item));
    return values.length ? mode === 'sum' ? values.reduce((sum, item) => sum + item, 0) : values[0] : null;
  }
  function bytes(valueToFormat: number | null) { return valueToFormat == null ? '—' : formatMetric(valueToFormat, 'bytes'); }
  function rate(valueToFormat: number | null) { return valueToFormat == null ? '—' : valueToFormat < 1024 ? `${valueToFormat.toFixed(0)} B/s` : `${formatMetric(valueToFormat, 'bytes')}/s`; }
  function titleFor(name: string) { return name.replace(/^(system|process|container|dependency)\./, '').replace(/^demo_/, '').replaceAll('.', ' / ').replaceAll('_', ' '); }
  function containerMetricGroups(items: MetricSeries[]) {
    const result: Record<string, MetricSeries[]> = { cpu: [], memory: [], network: [], disk: [] };
    for (const item of items) {
      const name = item.name.toLowerCase();
      const group = name.includes('cpu') || name.includes('load') ? 'cpu' : name.includes('memory') || name.includes('ram') ? 'memory' : name.includes('network') || name.includes('net') ? 'network' : name.includes('disk') || name.includes('block') || name.includes('io') ? 'disk' : '';
      if (group) result[group].push(item);
    }
    return result;
  }
  function primaryContainerMetric(group: string, series: MetricSeries[]) {
    const preferred: Record<string, string> = {
      cpu: 'container.cpu.utilization',
      memory: 'container.memory.usage',
      network: 'container.network.rx_bytes.rate',
      disk: 'container.block_io.read_bytes.rate'
    };
    return series.find(item => item.name === preferred[group]) || series[0];
  }
  function insight(series: MetricSeries[]) {
    const values = series.flatMap(item => item.buckets.map(bucket => bucket.average ?? bucket.last)).filter((item): item is number => item != null && Number.isFinite(item));
    if (!values.length) return 'No samples in range';
    const peak = Math.max(...values), average = values.reduce((sum, item) => sum + item, 0) / values.length;
    return `avg ${formatMetric(average, series[0]?.unit)} · peak ${formatMetric(peak, series[0]?.unit)}`;
  }
  $: if (mounted && queryKey !== loadedKey) load();
  onMount(() => { mounted = true; load(); });
</script>

{#if loading}<StatePanel state="loading" title="Opening resource…" />
{:else if error}<StatePanel state="error" message={error} retry={load} />
{:else if entity}
  <nav class="crumb mono" aria-label="Resource breadcrumb"><a href="/">CONTROL PLANE</a><span>/</span>{#if kind !== 'host' && parentHost}<a href={`/host/${parentHost.id}`}>{parentHost.name}</a><span>/</span>{/if}<span>{kind.toUpperCase()}</span><span>/</span><strong>{entity.name}</strong></nav>
  <header class="page-heading"><div><div class="title-line"><span class="eyebrow">{kind === 'host' ? `${entity.labels?.node_kind || 'machine'} node` : `${kind} recorder`}</span><StatusMark status={entity.health} /></div><h1 class="display">{entity.name}</h1><p>{entity.labels?.runtime || entity.labels?.os || entity.labels?.platform || 'Resource telemetry'} · {entity.labels?.service ? `${entity.labels.service} service runtime` : entity.labels?.address || entity.labels?.hostname || 'identity metadata unavailable'}</p></div><a class="button primary" href={`/timeline?selected=${entity.id}`}>Open in timeline →</a></header>

  <section class="facts panel" aria-label="Resource facts"><div><span class="eyebrow">LAST FRAME</span><b class="mono" class:stale={isStale(entity.last_seen)}>{formatTime(entity.last_seen)} {isStale(entity.last_seen) ? '· STALE' : ''}</b></div><div><span class="eyebrow">STATE</span><b>{entity.active ? 'Active' : 'Inactive'}</b></div><div><span class="eyebrow">HOST LINK</span><b class="mono">{parentHost?.name || entity.host_id?.slice(0, 12) || (kind === 'host' ? 'Self' : '—')}</b></div><div><span class="eyebrow">STREAMS</span><b>{metrics.length}</b></div>{#if entity.agent_id || parentHost?.agent_id}<div><span class="eyebrow">AGENT</span><b class="mono">{(entity.agent_id || parentHost?.agent_id)?.slice(0, 12)}</b></div>{/if}{#if entity.command}<div><span class="eyebrow">COMMAND</span><b class="mono command">{entity.command}</b></div>{/if}</section>

  {#if dependencyTelemetry}
    <section class="section-heading"><div><span class="eyebrow">Dependency telemetry / {dependencyKind || 'external system'}</span><h2>{dependencyKind === 'redis' ? 'Redis & queue pulse' : dependencyKind === 'postgresql' ? 'PostgreSQL activity' : 'Dependency activity'}</h2><p>Operational gauges and counters across the full selected time range.</p></div><span class="mono range">{formatRange(range.start, range.end)}</span></section>
    {#if metrics.length}<DependencyDashboard dependencyId={entity.id} kind={dependencyKind} {metrics} />{:else}<StatePanel state="empty" title="No dependency samples in range" message="Choose a wider range or verify this dependency adapter." />{/if}
  {/if}

  {#if hostTelemetry}
    <section class="section-heading"><div><span class="eyebrow">System telemetry / host frame</span><h2>System health</h2><p>Core host signals are grouped by operating-system subsystem.</p></div><span class="mono range">{formatRange(range.start, range.end)}</span></section>
    {#if metrics.length}<section class="signal-grid" aria-label="Host system telemetry"><article class="signal-card panel"><header><div><span class="eyebrow">CPU</span><h3>Utilization & load</h3></div><strong>{formatMetric(value('system.cpu.utilization'), 'percent')}</strong></header>{#if groups.cpu[0]}<MetricChart series={groups.cpu.find(item => item.name === 'system.cpu.utilization') || groups.cpu[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">1m {formatMetric(value('system.load1'), '')} · 5m {formatMetric(value('system.load5'), '')} · 15m {formatMetric(value('system.load15'), '')}</p></article><article class="signal-card panel"><header><div><span class="eyebrow">MEMORY / RAM</span><h3>Capacity</h3></div><strong>{bytes(value('system.memory.available'))}</strong></header>{#if groups.memory[0]}<MetricChart series={groups.memory.find(item => item.name === 'system.memory.available') || groups.memory[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">available of {bytes(value('system.memory.total'))} total</p></article><article class="signal-card panel"><header><div><span class="eyebrow">NETWORK</span><h3>Throughput</h3></div><strong>{rate(value('system.network.rx_bytes.rate', 'sum'))}</strong></header>{#if groups.network[0]}<MetricChart series={groups.network.find(item => item.name === 'system.network.rx_bytes.rate') || groups.network[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">in · {rate(value('system.network.tx_bytes.rate', 'sum'))} out · {value('system.network.rx_errors.rate', 'sum') ?? '—'} errors</p></article><article class="signal-card panel"><header><div><span class="eyebrow">DISK I/O</span><h3>Read / write</h3></div><strong>{rate(value('system.disk.sectors_read.rate', 'sum') == null ? null : (value('system.disk.sectors_read.rate', 'sum') || 0) * 512)}</strong></header>{#if groups.disk[0]}<MetricChart series={groups.disk.find(item => item.name === 'system.disk.sectors_read.rate') || groups.disk[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">read · {rate(value('system.disk.sectors_written.rate', 'sum') == null ? null : (value('system.disk.sectors_written.rate', 'sum') || 0) * 512)} write · {value('system.disk.io_in_progress', 'sum') ?? '—'} pending</p></article></section>{:else}<StatePanel state="empty" title="No system samples in range" message="The host is enrolled, but no system frames are available for this time window." />{/if}
  {/if}

  {#if kind === 'container'}
    <section class="section-heading"><div><span class="eyebrow">Machine observability / container node</span><h2>Container system health</h2><p>Container-level CPU, memory, network and block I/O signals when the collector reports them.</p></div><span class="mono range">parent {parentHost?.name || entity.host_id?.slice(0, 12) || 'unresolved'} · agent {(entity.agent_id || parentHost?.agent_id)?.slice(0, 12) || 'unresolved'}</span></section>
    {#if containerMetrics.length && containerGroupedCount}<section class="container-signal-grid" aria-label="Container machine telemetry">{#each [['cpu','CPU'],['memory','Memory / RAM'],['network','Network'],['disk','Block / disk I/O']] as group}{#if containerGroups[group[0]].length}{@const series = primaryContainerMetric(group[0], containerGroups[group[0]])}<article class="container-signal panel"><header><div><span class="eyebrow">{group[1]}</span><h3>{titleFor(series.name)}</h3></div><strong>{formatMetric(series.buckets.at(-1)?.last, series.unit === 'By' ? 'bytes' : series.unit)}</strong></header><MetricChart {series} cursorTime={$correlationCursor} height={220}/><p class="note">{insight([series])}</p></article>{/if}{/each}</section>{:else}<StatePanel state="empty" title="No container system samples" message="This node is enrolled, but no container.* telemetry is available in the selected window." />{/if}
  {/if}

  {#if kind === 'container' && resourceGroups.find(group => group.key === 'processes')?.items.length}
    <section class="section-heading lower"><div><span class="eyebrow">Container membership</span><h2>Processes in this container</h2><p>Processes are discovered by the node agent from Linux cgroups.</p></div></section>
    <section class="resource-grid container-processes">{#each resourceGroups.filter(group => group.key === 'processes') as resource}<article class="resource-card panel"><header><div><span class="eyebrow">{resource.key}</span><h3>{resource.label}</h3></div><strong>{resource.items.length}</strong></header><div class="resource-list">{#each resource.items as item}<a href={`/process/${item.id}`}><StatusMark status={item.health} /><span>{item.name}</span><span class="mono">{item.active ? 'ACTIVE' : 'INACTIVE'}</span></a>{/each}</div></article>{/each}</section>
  {/if}

  {#if processTelemetry}
    <section class="section-heading lower"><div><span class="eyebrow">Process telemetry / runtime</span><h2>Process performance</h2><p>CPU, memory, I/O, threads, descriptors, and faults from the selected PID.</p></div><span class="mono range">PID {entity.pid || '—'}</span></section>
    <section class="process-summary panel" aria-label="Process metric summary"><div><span>CPU</span><strong>{formatMetric(value('process.cpu.utilization'), 'percent')}</strong></div><div><span>RSS</span><strong>{bytes(value('process.memory.rss'))}</strong></div><div><span>VIRTUAL</span><strong>{bytes(value('process.memory.virtual'))}</strong></div><div><span>THREADS</span><strong>{formatMetric(value('process.threads'), '1')}</strong></div><div><span>DESCRIPTORS</span><strong>{formatMetric(value('process.file_descriptors'), '1')}</strong></div><div><span>MAJOR FAULTS</span><strong>{formatMetric(value('process.faults.major'), '1')}</strong></div></section>
  {/if}

  {#if !dependencyTelemetry}<section class="section-heading lower"><div><span class="eyebrow">{hostTelemetry ? 'Resource map / host inventory' : `${kind} telemetry`}</span><h2>{hostTelemetry ? 'Services, processes & dependencies' : 'Metric streams'}</h2><p>{hostTelemetry ? 'Drill into each attached resource to follow its own signal lanes.' : 'The latest samples and synchronized charts for this resource.'}</p></div></section>{/if}
  {#if hostTelemetry}
    <section class="resource-grid">{#each resourceGroups as resource}<article class="resource-card panel"><header><div><span class="eyebrow">{resource.key}</span><h3>{resource.label}</h3></div><strong>{resource.items.length}</strong></header>{#if resource.items.length}<div class="resource-list">{#each resource.items as item}<a href={`/${resource.key}/${item.id}`}><StatusMark status={item.health} /><span>{item.name}</span><span class="mono">{item.active ? 'ACTIVE' : 'INACTIVE'}</span></a>{/each}</div>{:else}<p class="note">No {resource.label.toLowerCase()} attached.</p>{/if}</article>{/each}</section>
  {:else if !dependencyTelemetry && detailMetrics.length}<section class="metric-list">{#each visibleMetrics as metric}<article class="metric-card panel"><header><div><span class="eyebrow">{metric.id.slice(0, 10)}</span><h3>{titleFor(metric.name)}</h3><span class="stream-attrs">{metric.attributes && Object.values(metric.attributes).length ? Object.values(metric.attributes).join(' / ') : 'aggregate stream'} · {metric.unit}</span></div><strong class="mono">{formatMetric(metric.buckets.at(-1)?.last, metric.unit === 'By' ? 'bytes' : metric.unit)}</strong></header><MetricChart series={metric} cursorTime={$correlationCursor} height={220}/></article>{/each}</section>{#if !showAllMetrics && detailMetrics.length > visibleMetrics.length}<button class="button show-all" on:click={() => showAllMetrics = true}>Show all {detailMetrics.length} metric streams</button>{/if}{:else if !dependencyTelemetry}<StatePanel state="empty" title="No samples in range" message="Choose a wider range or verify this resource's collector." />{/if}
{/if}

<style>
  .crumb{font-size:10px;color:#8c949d;margin-bottom:20px}.crumb a{color:var(--brand-300);text-decoration:none}.page-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:24px}.title-line{display:flex;align-items:center;gap:12px}.page-heading h1{font-size:40px;line-height:1;margin:8px 0}.page-heading p{margin:0;color:var(--muted);font-size:13px}.facts{display:grid;grid-template-columns:repeat(6,1fr);margin-bottom:32px}.facts div{padding:16px 20px;border-right:1px solid var(--line);display:flex;flex-direction:column;gap:8px;min-width:0}.facts div:last-child{border:0}.facts b{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.facts .stale{color:var(--amber)}.command{font-size:10px}.section-heading{display:flex;align-items:flex-end;justify-content:space-between;margin:0 0 12px}.section-heading h2{font:600 24px 'Barlow Condensed';margin:6px 0}.section-heading p{margin:0;color:var(--muted);font-size:13px}.range{font-size:10px;color:#8294a6}.signal-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:32px}.signal-card{padding:16px;min-width:0}.signal-card header,.resource-card header,.metric-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.signal-card h3,.resource-card h3,.metric-card h3{font-size:15px;margin:6px 0}.signal-card header strong,.metric-card header strong{font:16px 'IBM Plex Mono';color:var(--cyan);white-space:nowrap}.note{font:10px 'IBM Plex Mono';color:#a3a3a3;margin:8px 0 0}.signal-card .note,.container-signal .note{font-size:12px;margin-top:10px}.lower{margin-top:8px}.resource-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.resource-card{padding:16px;min-width:0}.resource-card header{margin-bottom:14px}.resource-card header strong{font:24px 'Barlow Condensed'}.resource-list a{display:grid;grid-template-columns:76px minmax(0,1fr) auto;align-items:center;gap:8px;border-top:1px solid var(--line);padding:11px 0;color:var(--frost);font-size:12px;text-decoration:none}.resource-list a:hover span:nth-child(2){color:var(--brand-300)}.resource-list a .status{font-size:9px}.resource-list a .mono{font-size:9px;color:#a3a3a3}.metric-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.metric-card{padding:16px;min-width:0}.metric-card header{margin-bottom:8px}.stream-attrs{display:block;color:#a3a3a3;font:10px 'IBM Plex Mono';margin-top:6px}.metric-card header strong{font-size:14px}@media(max-width:1000px){.facts{grid-template-columns:repeat(3,1fr)}.resource-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.page-heading{align-items:flex-start;flex-direction:column}.page-heading h1{font-size:34px}.facts{grid-template-columns:repeat(2,1fr)}.facts div{padding:14px}.range{display:none}.signal-grid,.resource-grid,.metric-list{grid-template-columns:1fr}}
  .signal-card,.container-signal,.metric-card{padding:20px}.signal-card header,.container-signal header,.metric-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:12px}.signal-card .eyebrow,.container-signal .eyebrow,.metric-card .eyebrow{font-size:14px}.signal-card h3,.container-signal h3,.metric-card h3{margin:8px 0 0;font-size:20px;line-height:1.25}.signal-card header strong,.container-signal header strong,.metric-card header strong{color:var(--cyan);font:600 24px 'IBM Plex Mono',monospace;white-space:nowrap}.signal-card .note,.container-signal .note{margin-top:16px;color:#c2c2c2;font:16px/1.5 'IBM Plex Mono',monospace}.metric-card .stream-attrs{margin-top:8px;font-size:14px;line-height:1.45}
  .crumb{display:flex;align-items:center;gap:8px;font-size:13px}.crumb strong{max-width:360px;overflow:hidden;color:var(--frost);font-weight:600;text-overflow:ellipsis;white-space:nowrap}
  .process-summary{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));margin-bottom:24px}.process-summary div{min-width:0;padding:16px;border-right:1px solid var(--line)}.process-summary div:last-child{border:0}.process-summary span{color:var(--muted);font:9px 'IBM Plex Mono';letter-spacing:.07em}.process-summary strong{display:block;overflow:hidden;margin-top:8px;color:var(--cyan);font:600 16px 'IBM Plex Mono';text-overflow:ellipsis;white-space:nowrap}.show-all{display:block;margin:18px auto 0}@media(max-width:1000px){.process-summary{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.process-summary{grid-template-columns:repeat(2,1fr)}}
</style>
