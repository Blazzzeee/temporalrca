<script lang="ts">
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { api, PROCESS_METRIC_NAMES, SYSTEM_METRIC_NAMES } from '$lib/api/client';
  import type { Entity, MetricSeries } from '$lib/types/api';
  import StatePanel from '$lib/components/StatePanel.svelte';
  import StatusMark from '$lib/components/StatusMark.svelte';
  import MetricChart from '$lib/components/MetricChart.svelte';
  import { correlationCursor } from '$lib/state/cursor';
  import { parseDashboardState } from '$lib/state/url';
  import { formatMetric, formatTime, isStale } from '$lib/utils/format';

  let entity: Entity | undefined;
  let metrics: MetricSeries[] = [];
  let loading = true;
  let error = '';
  let mounted = false;
  let loadedKey = '';
  $: range = parseDashboardState($page.url);
  $: routeKind = $page.params.kind || '';
  $: resourceId = $page.params.id || '';
  $: kind = ({ hosts: 'host', services: 'service', processes: 'process', containers: 'container', dependencies: 'dependency' } as Record<string, string>)[routeKind] || routeKind;
  $: queryKey = [resourceId, range.start.toISOString(), range.end.toISOString()].join('|');
  $: hostTelemetry = kind === 'host';
  $: groups = metricGroups(metrics);
  $: resourceGroups = entity ? [{ key: 'services', label: 'Services', items: entity.children?.filter(item => item.kind === 'service') || [] }, { key: 'processes', label: 'Processes', items: entity.children?.filter(item => item.kind === 'process') || [] }, { key: 'containers', label: 'Containers', items: entity.children?.filter(item => item.kind === 'container') || [] }, { key: 'dependencies', label: 'Dependencies', items: entity.children?.filter(item => item.kind === 'dependency') || [] }] : [];
  $: visibleMetrics = metrics.slice(0, hostTelemetry ? 32 : 24);

  async function load() {
    loadedKey = queryKey; loading = true; error = '';
    try {
      entity = await api.entity(kind, resourceId);
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
  function titleFor(name: string) { return name.replace(/^(system|process)\./, '').replaceAll('.', ' / '); }
  $: if (mounted && queryKey !== loadedKey) load();
  onMount(() => { mounted = true; load(); });
</script>

{#if loading}<StatePanel state="loading" title="Opening resource…" />
{:else if error}<StatePanel state="error" message={error} retry={load} />
{:else if entity}
  <div class="crumb mono"><a href="/">CONTROL PLANE</a> / <span>{kind.toUpperCase()}</span> / {entity.id.slice(0, 12)}</div>
  <header class="page-heading"><div><div class="title-line"><span class="eyebrow">{kind} recorder</span><StatusMark status={entity.health} /></div><h1 class="display">{entity.name}</h1><p>{entity.labels?.os || entity.labels?.platform || 'Resource telemetry'} · {entity.labels?.address || entity.labels?.hostname || 'identity metadata unavailable'}</p></div><a class="button primary" href={`/timeline?selected=${entity.id}`}>Open in timeline →</a></header>

  <section class="facts panel" aria-label="Resource facts"><div><span class="eyebrow">LAST FRAME</span><b class="mono" class:stale={isStale(entity.last_seen)}>{formatTime(entity.last_seen)} {isStale(entity.last_seen) ? '· STALE' : ''}</b></div><div><span class="eyebrow">STATE</span><b>{entity.active ? 'Active' : 'Inactive'}</b></div><div><span class="eyebrow">HOST LINK</span><b class="mono">{entity.host_id?.slice(0, 12) || (kind === 'host' ? 'Self' : '—')}</b></div><div><span class="eyebrow">STREAMS</span><b>{metrics.length}</b></div>{#if entity.agent_id}<div><span class="eyebrow">AGENT</span><b class="mono">{entity.agent_id.slice(0, 12)}</b></div>{/if}{#if entity.command}<div><span class="eyebrow">COMMAND</span><b class="mono command">{entity.command}</b></div>{/if}</section>

  {#if hostTelemetry}
    <section class="section-heading"><div><span class="eyebrow">System telemetry / host frame</span><h2>System health</h2><p>Core host signals are grouped by operating-system subsystem.</p></div><span class="mono range">{formatTime(range.start)} — {formatTime(range.end)}</span></section>
    {#if metrics.length}<section class="signal-grid" aria-label="Host system telemetry"><article class="signal-card panel"><header><div><span class="eyebrow">CPU</span><h3>Utilization & load</h3></div><strong>{formatMetric(value('system.cpu.utilization'), 'percent')}</strong></header>{#if groups.cpu[0]}<MetricChart series={groups.cpu.find(item => item.name === 'system.cpu.utilization') || groups.cpu[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">1m {formatMetric(value('system.load1'), '')} · 5m {formatMetric(value('system.load5'), '')} · 15m {formatMetric(value('system.load15'), '')}</p></article><article class="signal-card panel"><header><div><span class="eyebrow">MEMORY / RAM</span><h3>Capacity</h3></div><strong>{bytes(value('system.memory.available'))}</strong></header>{#if groups.memory[0]}<MetricChart series={groups.memory.find(item => item.name === 'system.memory.available') || groups.memory[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">available of {bytes(value('system.memory.total'))} total</p></article><article class="signal-card panel"><header><div><span class="eyebrow">NETWORK</span><h3>Throughput</h3></div><strong>{rate(value('system.network.rx_bytes.rate', 'sum'))}</strong></header>{#if groups.network[0]}<MetricChart series={groups.network.find(item => item.name === 'system.network.rx_bytes.rate') || groups.network[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">in · {rate(value('system.network.tx_bytes.rate', 'sum'))} out · {value('system.network.rx_errors.rate', 'sum') ?? '—'} errors</p></article><article class="signal-card panel"><header><div><span class="eyebrow">DISK I/O</span><h3>Read / write</h3></div><strong>{rate(value('system.disk.sectors_read.rate', 'sum') == null ? null : (value('system.disk.sectors_read.rate', 'sum') || 0) * 512)}</strong></header>{#if groups.disk[0]}<MetricChart series={groups.disk.find(item => item.name === 'system.disk.sectors_read.rate') || groups.disk[0]} cursorTime={$correlationCursor} height={240}/>{/if}<p class="note">read · {rate(value('system.disk.sectors_written.rate', 'sum') == null ? null : (value('system.disk.sectors_written.rate', 'sum') || 0) * 512)} write · {value('system.disk.io_in_progress', 'sum') ?? '—'} pending</p></article></section>{:else}<StatePanel state="empty" title="No system samples in range" message="The host is enrolled, but no system frames are available for this time window." />{/if}
  {/if}

  <section class="section-heading lower"><div><span class="eyebrow">{hostTelemetry ? 'Resource map / host inventory' : `${kind} telemetry`}</span><h2>{hostTelemetry ? 'Services, processes & dependencies' : 'Metric streams'}</h2><p>{hostTelemetry ? 'Drill into each attached resource to follow its own signal lanes.' : 'The latest samples and synchronized charts for this resource.'}</p></div></section>
  {#if hostTelemetry}
    <section class="resource-grid">{#each resourceGroups as resource}<article class="resource-card panel"><header><div><span class="eyebrow">{resource.key}</span><h3>{resource.label}</h3></div><strong>{resource.items.length}</strong></header>{#if resource.items.length}<div class="resource-list">{#each resource.items as item}<a href={`/${resource.key}/${item.id}`}><StatusMark status={item.health} /><span>{item.name}</span><span class="mono">{item.active ? 'ACTIVE' : 'INACTIVE'}</span></a>{/each}</div>{:else}<p class="note">No {resource.label.toLowerCase()} attached.</p>{/if}</article>{/each}</section>
  {:else if metrics.length}<section class="metric-list">{#each visibleMetrics as metric}<article class="metric-card panel"><header><div><span class="eyebrow">{metric.id.slice(0, 10)}</span><h3>{titleFor(metric.name)}</h3><span class="stream-attrs">{metric.attributes && Object.values(metric.attributes).length ? Object.values(metric.attributes).join(' / ') : 'aggregate stream'} · {metric.unit}</span></div><strong class="mono">{formatMetric(metric.buckets.at(-1)?.last, metric.unit === 'By' ? 'bytes' : metric.unit)}</strong></header><MetricChart series={metric} cursorTime={$correlationCursor} height={220}/></article>{/each}</section>{:else}<StatePanel state="empty" title="No samples in range" message="Choose a wider range or verify this resource's collector." />{/if}
{/if}

<style>
  .crumb{font-size:10px;color:#8c949d;margin-bottom:20px}.crumb a{color:var(--brand-300);text-decoration:none}.page-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:24px}.title-line{display:flex;align-items:center;gap:12px}.page-heading h1{font-size:40px;line-height:1;margin:8px 0}.page-heading p{margin:0;color:var(--muted);font-size:13px}.facts{display:grid;grid-template-columns:repeat(6,1fr);margin-bottom:32px}.facts div{padding:16px 20px;border-right:1px solid var(--line);display:flex;flex-direction:column;gap:8px;min-width:0}.facts div:last-child{border:0}.facts b{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.facts .stale{color:var(--amber)}.command{font-size:10px}.section-heading{display:flex;align-items:flex-end;justify-content:space-between;margin:0 0 12px}.section-heading h2{font:600 24px 'Barlow Condensed';margin:6px 0}.section-heading p{margin:0;color:var(--muted);font-size:13px}.range{font-size:10px;color:#8294a6}.signal-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:32px}.signal-card{padding:16px;min-width:0}.signal-card header,.resource-card header,.metric-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.signal-card h3,.resource-card h3,.metric-card h3{font-size:15px;margin:6px 0}.signal-card header strong,.metric-card header strong{font:16px 'IBM Plex Mono';color:var(--cyan);white-space:nowrap}.note{font:10px 'IBM Plex Mono';color:#81909e;margin:8px 0 0}.lower{margin-top:8px}.resource-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.resource-card{padding:16px;min-width:0}.resource-card header{margin-bottom:14px}.resource-card header strong{font:24px 'Barlow Condensed'}.resource-list a{display:grid;grid-template-columns:76px minmax(0,1fr) auto;align-items:center;gap:8px;border-top:1px solid rgba(48,54,61,.65);padding:11px 0;color:var(--frost);font-size:12px;text-decoration:none}.resource-list a:hover span:nth-child(2){color:var(--brand-300)}.resource-list a .status{font-size:9px}.resource-list a .mono{font-size:9px;color:#81909e}.metric-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.metric-card{padding:16px;min-width:0}.metric-card header{margin-bottom:8px}.stream-attrs{display:block;color:#81909e;font:10px 'IBM Plex Mono';margin-top:6px}.metric-card header strong{font-size:14px}@media(max-width:1000px){.facts{grid-template-columns:repeat(3,1fr)}.resource-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.page-heading{align-items:flex-start;flex-direction:column}.page-heading h1{font-size:34px}.facts{grid-template-columns:repeat(2,1fr)}.facts div{padding:14px}.range{display:none}.signal-grid,.resource-grid,.metric-list{grid-template-columns:1fr}}
</style>
