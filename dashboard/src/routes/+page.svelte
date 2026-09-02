<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { api, SYSTEM_METRIC_NAMES } from '$lib/api/client';
  import type { Entity, FleetSummary, MetricSeries } from '$lib/types/api';
  import StatusMark from '$lib/components/StatusMark.svelte';
  import StatePanel from '$lib/components/StatePanel.svelte';
  import MetricChart from '$lib/components/MetricChart.svelte';
  import { parseDashboardState } from '$lib/state/url';
  import { dashboardNow } from '$lib/state/clock';
  import { formatMetric, formatRange, formatTime, isStale } from '$lib/utils/format';

  let summary: FleetSummary | undefined;
  let allEntities: Entity[] = [];
  let hosts: Entity[] = [];
  let aggregateTelemetry: MetricSeries[] = [];
  let telemetry: Record<string, MetricSeries[]> = {};
  let loading = true;
  let refreshing = false;
  let error = '';
  let refreshTimer: ReturnType<typeof setInterval> | undefined;
  let resourceQuery = '';
  let showHistorical = false;
  let mounted = false;
  let loadedKey = '';
  $: range = parseDashboardState($page.url, $dashboardNow);
  $: chartRange = range;
  $: currentHosts = hosts.filter(host => !isStale(host.last_seen));
  // Overview is fleet-scoped and must not silently select the first host.
  // System metrics are explicitly selected through the node picker.
  $: selectedId = $page.url.searchParams.get('host') || '';
  $: view = $page.url.searchParams.get('view') || 'overview';
  $: loadKey = [view, selectedId, range.start.toISOString(), range.end.toISOString()].join('|');
  $: selectedHost = selectedId ? hosts.find(host => host.id === selectedId) : undefined;
  $: selectedMetrics = view === 'overview' ? aggregateTelemetry : selectedHost ? (telemetry[selectedHost.id] || []) : [];
  $: selectedGroups = groupMetrics(selectedMetrics);
  $: dependencies = allEntities.filter(item => item.kind === 'dependency' && item.active);
  $: postgresDependencies = dependencies.filter(item => /postgres/i.test(item.dependency_type || item.name));
  $: redisDependencies = dependencies.filter(item => /redis/i.test(item.dependency_type || item.name));
  $: postgresDependency = postgresDependencies.find(item => /workload[-_ ]?postgres/i.test(item.name)) || postgresDependencies[0];
  $: redisDependency = redisDependencies.find(item => /workload[-_ ]?redis/i.test(item.name)) || redisDependencies[0];
  $: liveAgentCount = new Set(currentHosts.map(host => host.agent_id).filter(Boolean)).size || currentHosts.length;
  // Embedded workload agents publish a host row for their container while the
  // machine agent also publishes Docker inventory.  Use one canonical row in
  // Fleet (machine hosts + their Docker containers) to avoid showing every
  // workload twice. Keep the embedded rows in `hosts` for service/process
  // relationships and explicit system navigation.
  $: machineHosts = currentHosts.filter(host => host.labels?.node_kind !== 'container');
  $: canonicalHosts = machineHosts.length ? machineHosts : currentHosts;
  $: dockerNodes = allEntities.filter(item => item.kind === 'container' && item.active && canonicalHosts.some(host => host.id === item.host_id));
  $: nodeCount = canonicalHosts.length + dockerNodes.length;
  $: topNodes = [...canonicalHosts, ...dockerNodes].sort((a, b) => Number(b.id === selectedId) - Number(a.id === selectedId) || Date.parse(b.last_seen) - Date.parse(a.last_seen) || a.name.localeCompare(b.name));
  $: activeServices = allEntities.filter(item => item.kind === 'service' && item.active).sort((a, b) => a.name.localeCompare(b.name));
  $: activeProcesses = allEntities.filter(item => item.kind === 'process' && item.active).sort((a, b) => a.name.localeCompare(b.name));

  async function load() {
    loadedKey = loadKey;
    if (summary) refreshing = true; else loading = true; error = '';
    try {
      const [nextEntities, nextAggregate] = await Promise.all([
        api.entities(),
        view === 'overview' ? api.metricsAggregate(chartRange.start.toISOString(), chartRange.end.toISOString(), 180, undefined, SYSTEM_METRIC_NAMES) : Promise.resolve([])
      ]);
      allEntities = nextEntities; hosts = nextEntities.filter(entity => entity.kind === 'host'); aggregateTelemetry = nextAggregate;
      // The global catalog is intentionally not fetched for Home. It is a
      // large discovery payload and previously blocked the first paint.
      const nextSummary: FleetSummary = {
        hosts: hosts.length,
        services: nextEntities.filter(entity => entity.kind === 'service').length,
        processes: nextEntities.filter(entity => entity.kind === 'process').length,
        containers: nextEntities.filter(entity => entity.kind === 'container').length,
        dependencies: nextEntities.filter(entity => entity.kind === 'dependency').length,
        healthy: hosts.filter(host => host.health === 'healthy').length,
        degraded: hosts.filter(host => host.health === 'degraded').length,
        offline: hosts.filter(host => host.health === 'offline').length,
        ingestion_delay_ms: Math.max(0, ...hosts.map(host => Date.now() - Date.parse(host.last_seen)))
      };
      hosts = hosts.filter(host => !isStale(host.last_seen));
      summary = { ...nextSummary, hosts: hosts.length, healthy: hosts.filter(host => host.health === 'healthy').length, degraded: hosts.filter(host => host.health === 'degraded').length, offline: hosts.filter(host => host.health === 'offline').length, ingestion_delay_ms: Math.max(0, ...hosts.map(host => Date.now() - Date.parse(host.last_seen))) };
      if (view === 'system') {
        const requestedHost = $page.url.searchParams.get('host');
        if (requestedHost && hosts.some(host => host.id === requestedHost)) await loadHostTelemetry(requestedHost);
      }
    } catch (e) { error = e instanceof Error ? e.message : 'Unable to read fleet telemetry'; }
    finally { loading = false; refreshing = false; }
  }
  function groupMetrics(items: MetricSeries[]) {
    const result: Record<string, MetricSeries[]> = { cpu: [], memory: [], network: [], disk: [] };
    for (const item of items) {
      const group = item.name.startsWith('system.cpu') || item.name.startsWith('system.load') ? 'cpu' : item.name.startsWith('system.memory') ? 'memory' : item.name.startsWith('system.network') ? 'network' : item.name.startsWith('system.disk') ? 'disk' : '';
      if (group && result[group].length < 8) result[group].push(item);
    }
    return result;
  }
  function metricValue(items: MetricSeries[], name: string, mode: 'last' | 'sum' = 'last') {
    const values = items.filter(item => item.name === name).map(item => item.buckets.at(-1)?.last).filter((value): value is number => value != null && Number.isFinite(value));
    return values.length ? (mode === 'sum' ? values.reduce((sum, value) => sum + value, 0) : values[0]) : null;
  }
  function bytes(value: number | null) { return value == null ? '—' : formatMetric(value, 'bytes'); }
  function rate(value: number | null) { return value == null ? '—' : value < 1024 ? `${value.toFixed(0)} B/s` : `${formatMetric(value, 'bytes')}/s`; }
  function testAgent(host: Entity) { return /test|demo|fixture|sample/i.test(`${host.name} ${JSON.stringify(host.labels || {})}`); }
  function hostProcesses(host: Entity) { return allEntities.filter(item => item.kind === 'process' && item.host_id === host.id); }
  function hostServices(host: Entity) { return allEntities.filter(item => item.kind === 'service' && item.host_id === host.id); }
  function hostDependencies(host: Entity) { return allEntities.filter(item => item.kind === 'dependency' && item.host_id === host.id); }
  function hostContainers(host: Entity) { return allEntities.filter(item => item.kind === 'container' && item.host_id === host.id); }
  function serviceProcesses(service: Entity) { return allEntities.filter(item => item.kind === 'process' && item.parent_id === service.id); }
  function serviceDependencies(service: Entity) { return allEntities.filter(item => item.kind === 'dependency' && item.related_ids?.includes(service.id)); }
  function unassignedProcesses(host: Entity) { return hostProcesses(host).filter(item => !item.parent_id); }
  function matchesResource(item: Entity) { const query = resourceQuery.trim().toLowerCase(); return !query || `${item.name} ${item.command || ''} ${item.pid || ''}`.toLowerCase().includes(query); }
  function visibleResources(items: Entity[]) { return items.filter(item => (showHistorical || item.active) && matchesResource(item)); }
  function activeCount(items: Entity[]) { return items.filter(item => item.active).length; }
  function nodeParent(node: Entity) { return node.kind === 'host' ? null : hosts.find(host => host.id === node.host_id); }
  function nodeType(node: Entity) { return node.kind === 'container' || node.labels?.node_kind === 'container' ? 'CONTAINER' : 'MACHINE'; }
  function nodeAgent(node: Entity) { return (node.kind === 'host' ? node.agent_id : nodeParent(node)?.agent_id) || 'unresolved'; }
  function nodeHref(node: Entity) { return `/${node.kind === 'host' ? 'host' : 'container'}/${node.id}`; }
  function dashboardHref(nextView: string, hostId: string | undefined = '') {
    const params = new URLSearchParams($page.url.searchParams);
    params.set('view', nextView);
    if (hostId) params.set('host', hostId); else params.delete('host');
    return `/?${params.toString()}`;
  }
  function nodeServices(node: Entity) { const workloadHost = nodeWorkloadHost(node); return workloadHost ? activeCount(hostServices(workloadHost)) : 0; }
  function nodeProcesses(node: Entity) { return node.kind === 'host' ? activeCount(hostProcesses(node)) : activeCount(allEntities.filter(item => item.kind === 'process' && item.container_id === node.id)); }
  function nodeWorkloadHost(node: Entity) {
    if (node.kind === 'host') return node;
    const shortName = node.name.replace(/^temporalrca-/, '').replace(/-1$/, '');
    return hosts.find(host => host.name === shortName || host.labels?.service === shortName);
  }
  function nodeProcessEntities(node: Entity) {
    return node.kind === 'host' ? hostProcesses(node) : allEntities.filter(item => item.kind === 'process' && item.container_id === node.id);
  }
  function selectSystemNode(id: string) {
    const params = new URLSearchParams($page.url.searchParams);
    params.set('view', 'system');
    params.set('host', id);
    void goto(`${$page.url.pathname}?${params.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
    void loadHostTelemetry(id);
  }
  function selectTopologyNode(id: string) {
    const params = new URLSearchParams($page.url.searchParams);
    params.set('view', 'topology');
    if (id) params.set('host', id); else params.delete('host');
    void goto(`${$page.url.pathname}?${params.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
  }
  async function loadHostTelemetry(id: string) {
    telemetry = { ...telemetry, [id]: await api.metrics(id, chartRange.start.toISOString(), chartRange.end.toISOString(), 180, undefined, SYSTEM_METRIC_NAMES, 'host') };
  }
  $: if (mounted && loadKey !== loadedKey) void load();
  onMount(() => { mounted = true; void load(); refreshTimer = setInterval(load, 15000); return () => { if (refreshTimer) clearInterval(refreshTimer); }; });
</script>

<div class="page-heading"><div><span class="eyebrow">Operations / control plane</span><h1 class="display">Fleet recorder</h1><p>One view of every enrolled agent, host and signal being recorded.</p></div><div class="heading-actions"><span class="freshness mono">{summary ? `UPDATED ${formatTime(new Date())}` : 'CONNECTING'}</span><button class="button" on:click={load} disabled={loading||refreshing} aria-label="Refresh fleet data">{refreshing?'Refreshing…':'↻ Refresh'}</button></div></div>

{#if loading}<StatePanel state="loading" title="Reading fleet state…" message="Loading inventory and the latest system frames." />
{:else if error}<StatePanel state="error" message={error} retry={load} />
{:else if !summary || hosts.length === 0}<StatePanel state="empty" title="No agents enrolled" message="Enroll an agent to begin recording hosts, processes and system telemetry." />
{:else}
  {#if view === 'overview' || view === 'system' || view === 'topology'}
    <section class="kpi-strip panel" aria-label="Fleet node summary"><div class="kpi-primary"><span class="eyebrow">RECORDER HEALTH</span><strong class:attention={summary.offline > 0 || summary.degraded > 0}>{summary.offline > 0 ? 'ACTION NEEDED' : summary.degraded > 0 ? 'CHECK SIGNALS' : 'ALL SYSTEMS NOMINAL'}</strong><span class="subtle">{summary.healthy} healthy · {summary.degraded} degraded · {summary.offline} offline</span></div><div class="kpi"><span class="eyebrow">NODES</span><b>{nodeCount}</b><span class="subtle">machines and reporting containers</span></div><div class="kpi"><span class="eyebrow">AGENTS</span><b>{liveAgentCount}</b><span class="subtle">live reporting identities</span></div><div class="kpi"><span class="eyebrow">INGESTION DELAY</span><b class:attention={summary.ingestion_delay_ms > 45000}>{formatMetric(summary.ingestion_delay_ms / 1000, 'seconds')}</b><span class="subtle">event → recorder</span></div></section>
  {:else if view === 'dependencies'}
    <section class="kpi-strip panel" aria-label="Dependency summary"><div class="kpi-primary"><span class="eyebrow">DEPENDENCY TELEMETRY</span><strong>{dependencies.length ? 'SIGNALS ACTIVE' : 'AWAITING SIGNALS'}</strong><span class="subtle">generalized database and messaging schema</span></div><div class="kpi"><span class="eyebrow">POSTGRESQL</span><b>{postgresDependencies.length}</b><span class="subtle">database resources</span></div><div class="kpi"><span class="eyebrow">REDIS</span><b>{redisDependencies.length}</b><span class="subtle">cache and queue resources</span></div></section>
  {:else if view === 'workloads'}
    <section class="kpi-strip panel" aria-label="Workload summary"><div class="kpi-primary"><span class="eyebrow">APPLICATION TELEMETRY</span><strong>{activeServices.length ? 'WORKLOADS ACTIVE' : 'AWAITING SIGNALS'}</strong><span class="subtle">producer, consumer, event and scheduled jobs</span></div><div class="kpi"><span class="eyebrow">SERVICES</span><b>{activeServices.length}</b><span class="subtle">active workload identities</span></div><div class="kpi"><span class="eyebrow">PROCESSES</span><b>{activeProcesses.length}</b><span class="subtle">active runtime processes</span></div></section>
  {/if}

  <nav class="dashboard-tabs" aria-label="Agent dashboard views"><a href={dashboardHref('overview')} aria-current={view==='overview' ? 'page' : undefined} class:active={view==='overview'}>Overview</a><a href={dashboardHref('dependencies')} aria-current={view==='dependencies' ? 'page' : undefined} class:active={view==='dependencies'}>PostgreSQL & Redis</a><a href={dashboardHref('workloads')} aria-current={view==='workloads' ? 'page' : undefined} class:active={view==='workloads'}>Services & processes</a><a href={dashboardHref('system', selectedHost?.id)} aria-current={view==='system' ? 'page' : undefined} class:active={view==='system'}>Node metrics</a><a href={dashboardHref('topology')} aria-current={view==='topology' ? 'page' : undefined} class:active={view==='topology'}>Resource topology</a></nav>

  {#if view==='overview'}
  <section class="section-heading"><div><span class="eyebrow">Fleet / reporting machines and containers</span><h2>Live nodes</h2></div><span class="section-meta mono">SHOWING {topNodes.length} OF {nodeCount} LIVE NODES</span></section>
  <section class="inventory panel" aria-label="Top nodes and their agents"><div class="table-head"><span>Node</span><span>Type / state</span><span>Parent host</span><span>Agent</span><span>Services</span><span>Processes</span><span>Last frame</span></div>
    {#each topNodes as node}{@const parent = nodeParent(node)}<a class="inventory-row node-row" href={nodeHref(node)}><div class="identity"><span class="agent-dot" class:offline={node.health === 'offline'} aria-hidden="true"></span><div><strong>{node.name}</strong><span class="mono">{nodeType(node)} NODE{testAgent(parent || node) ? ' · TEST' : ''}</span></div>{#if testAgent(parent || node)}<span class="tag">TEST</span>{/if}</div><div class="state-cell"><span class="node-type">{nodeType(node)}</span><StatusMark status={node.health} /></div><span class="parent-host">{parent?.name || '—'}</span><span class="mono agent-id">{nodeAgent(node).slice(0, 12)}</span><span class="count">{nodeServices(node)}</span><span class="count">{nodeProcesses(node) ?? '—'}</span><span class="last-frame mono" class:stale={isStale(node.last_seen)}>{isStale(node.last_seen) ? 'STALE · ' : ''}{formatTime(node.last_seen)}</span></a>{/each}
  </section>
  {/if}

  {#if view==='dependencies'}
  <section class="section-heading dependency-heading"><div><span class="eyebrow">Dependency pulse / grouped resources</span><h2>PostgreSQL & Redis</h2><p>Open a dependency for its database, queue and stream telemetry.</p></div><span class="section-meta mono">{dependencies.length} ACTIVE RESOURCES</span></section>
  <section class="dependency-overview" aria-label="PostgreSQL and Redis dependencies">
    <article class="dependency-overview-card panel"><header><div><span class="eyebrow">DATABASE</span><h3>PostgreSQL</h3></div><strong>{postgresDependencies.length}</strong></header>{#if postgresDependency}<div class="dependency-links"><a href={`/dependency/${postgresDependency.id}`}><span class="status-cell"><StatusMark status={postgresDependency.health}/>{postgresDependency.name}</span><small>{postgresDependency.host_id ? (hosts.find(host => host.id === postgresDependency.host_id)?.name || 'attached host') : 'attached resource'} →</small></a></div><span class="subtle dependency-count">Representative resource · {postgresDependencies.length} PostgreSQL streams enrolled</span>{:else}<span class="subtle">No PostgreSQL dependency is currently enrolled.</span>{/if}</article>
    <article class="dependency-overview-card panel"><header><div><span class="eyebrow">CACHE / QUEUES</span><h3>Redis</h3></div><strong>{redisDependencies.length}</strong></header>{#if redisDependency}<div class="dependency-links"><a href={`/dependency/${redisDependency.id}`}><span class="status-cell"><StatusMark status={redisDependency.health}/>{redisDependency.name}</span><small>{redisDependency.host_id ? (hosts.find(host => host.id === redisDependency.host_id)?.name || 'attached host') : 'attached resource'} →</small></a></div><span class="subtle dependency-count">Representative resource · {redisDependencies.length} Redis streams enrolled</span>{:else}<span class="subtle">No Redis dependency is currently enrolled.</span>{/if}</article>
  </section>
  {/if}

  {#if view==='workloads'}
  <section class="section-heading"><div><span class="eyebrow">Application telemetry / fleet-wide</span><h2>Services & processes</h2><p>Application streams stay visible without selecting an arbitrary node.</p></div><a class="quiet-link" href="/activity">Open workload charts →</a></section>
  <section class="application-overview panel" aria-label="Application and process telemetry"><div class="application-services">{#each activeServices as service}<a href={`/service/${service.id}`}><StatusMark status={service.health}/><span><strong>{service.name}</strong><small>{activeCount(serviceProcesses(service))} process · {serviceDependencies(service).length} dependencies</small></span><b>Service metrics →</b></a>{/each}</div><div class="process-overview"><header><span class="eyebrow">PROCESS METRICS</span><strong>{activeProcesses.length} active</strong></header>{#each activeProcesses.slice(0, 12) as process}<a href={`/process/${process.id}`}><span>{process.name}</span><small class="mono">PID {process.pid || '—'}</small><b>CPU · RAM · I/O →</b></a>{/each}</div></section>
  {/if}

  {#if view==='overview'||view==='system'}
  <section class="section-heading telemetry-heading"><div><span class="eyebrow">{view==='overview' ? 'System telemetry / fleet aggregate' : 'System telemetry / selected node'}</span><h2>{selectedHost?.name || (view==='overview' ? 'Fleet' : 'Node')} signals</h2><p>{view==='overview' ? 'Average capacity and utilization across live nodes; throughput is summed.' : 'Choose a node to inspect its system streams.'}</p></div><div class="telemetry-controls">{#if view==='system'}<label class="node-picker"><span>Node</span><select value={selectedId} on:change={(event) => selectSystemNode(event.currentTarget.value)} aria-label="Choose node for system metrics"><option value="">Choose a node</option>{#each hosts as host}<option value={host.id}>{host.name} · {host.health}</option>{/each}</select></label>{/if}<span class="range-label mono">{formatRange(chartRange.start, chartRange.end)}</span></div></section>
  {#if selectedMetrics.length && (view==='overview' || selectedHost)}
    <section class="signal-grid" aria-label={`System telemetry for ${selectedHost?.name || 'the fleet'}`}><article class="signal-card panel"><div class="signal-title"><div><span class="eyebrow">CPU</span><h3>Utilization</h3></div><strong class="metric-value">{formatMetric(metricValue(selectedMetrics, 'system.cpu.utilization'), 'percent')}</strong></div>{#if selectedGroups.cpu[0]}<MetricChart series={selectedGroups.cpu.find(item => item.name === 'system.cpu.utilization') || selectedGroups.cpu[0]} height={240}/>{/if}<span class="signal-note">load {formatMetric(metricValue(selectedMetrics, 'system.load1'), '')} / 1m · {formatMetric(metricValue(selectedMetrics, 'system.load5'), '')} / 5m</span></article><article class="signal-card panel"><div class="signal-title"><div><span class="eyebrow">MEMORY / RAM</span><h3>Available capacity</h3></div><strong class="metric-value">{bytes(metricValue(selectedMetrics, 'system.memory.available'))}</strong></div>{#if selectedGroups.memory[0]}<MetricChart series={selectedGroups.memory.find(item => item.name === 'system.memory.available') || selectedGroups.memory[0]} height={240}/>{/if}<span class="signal-note">of {bytes(metricValue(selectedMetrics, 'system.memory.total'))} total</span></article><article class="signal-card panel"><div class="signal-title"><div><span class="eyebrow">NETWORK</span><h3>Traffic</h3></div><strong class="metric-value">{rate(metricValue(selectedMetrics, 'system.network.rx_bytes.rate', 'sum'))}</strong></div>{#if selectedGroups.network[0]}<MetricChart series={selectedGroups.network.find(item => item.name === 'system.network.rx_bytes.rate') || selectedGroups.network[0]} height={240}/>{/if}<span class="signal-note">in · {rate(metricValue(selectedMetrics, 'system.network.tx_bytes.rate', 'sum'))} out</span></article><article class="signal-card panel"><div class="signal-title"><div><span class="eyebrow">DISK I/O</span><h3>Read / write throughput</h3></div><strong class="metric-value">{rate(metricValue(selectedMetrics, 'system.disk.sectors_read.rate', 'sum') == null ? null : (metricValue(selectedMetrics, 'system.disk.sectors_read.rate', 'sum') || 0) * 512)}</strong></div>{#if selectedGroups.disk[0]}<MetricChart series={selectedGroups.disk.find(item => item.name === 'system.disk.sectors_read.rate') || selectedGroups.disk[0]} height={240}/>{/if}<span class="signal-note">read · {rate(metricValue(selectedMetrics, 'system.disk.sectors_written.rate', 'sum') == null ? null : (metricValue(selectedMetrics, 'system.disk.sectors_written.rate', 'sum') || 0) * 512)} write</span></article></section>
    {#if view==='system'}<section class="system-detail panel"><header><div><span class="eyebrow">Signal catalog / selected node</span><h3>System-level streams</h3></div><span class="mono subtle">{selectedMetrics.length} representative series</span></header><div class="stream-list">{#each selectedMetrics as metric}<div><span class="stream-name">{metric.name}</span><span class="chip">{metric.attributes && Object.values(metric.attributes).length ? Object.values(metric.attributes).join(' / ') : 'aggregate'}</span><strong class="mono">{formatMetric(metric.buckets.at(-1)?.last, metric.unit === 'By' ? 'bytes' : metric.unit)}</strong></div>{/each}</div></section>{/if}
  {:else}<StatePanel state="empty" title="No system samples in this range" message="Inventory is present, but this node has not reported system metrics for the selected window." />{/if}
  {/if}

  {#if view==='topology'}
  <section class="section-heading lower-heading"><div><span class="eyebrow">Resource map / fleet</span><h2>{selectedHost ? `What ${selectedHost.name} runs` : 'All live nodes'}</h2><p>{selectedHost ? 'Showing an explicitly selected node.' : 'No node is selected automatically.'}</p></div><div class="telemetry-controls"><label class="node-picker"><span>Node scope</span><select value={selectedId} on:change={(event) => selectTopologyNode(event.currentTarget.value)} aria-label="Choose a node for resource topology"><option value="">All nodes</option>{#each hosts as host}<option value={host.id}>{host.name}</option>{/each}</select></label><a class="quiet-link" href="/timeline">Open unified timeline →</a></div></section>
  {#if selectedHost}<section class="node-map panel" aria-label={`Resource nodes on ${selectedHost.name}`}>
    <header class="node-root"><div class="node-icon">A</div><div><span class="eyebrow">AGENT</span><h3 class="mono">{selectedHost.agent_id?.slice(0, 12) || 'unresolved'}</h3></div><div class="host-branch"><span>HOST</span><strong>{selectedHost.name}</strong></div><div class="node-counts mono"><span>{activeCount(hostServices(selectedHost))} services</span><span>{activeCount(hostProcesses(selectedHost))} processes</span><span>{activeCount(hostContainers(selectedHost))} containers</span><span>{activeCount(hostDependencies(selectedHost))} dependencies</span></div></header>
    <div class="resource-toolbar"><label><span class="sr-only">Filter resources</span><input bind:value={resourceQuery} placeholder="Filter processes, services, PID…" /></label><button class:active={showHistorical} on:click={() => showHistorical = !showHistorical}>{showHistorical ? 'Showing history' : 'Live resources'} · {showHistorical ? 'hide inactive' : 'show all recorded'}</button></div>
    <div class="node-groups">
      <div class="node-column"><div class="column-title"><span>Services</span><b>{visibleResources(hostServices(selectedHost)).length} visible</b></div>{#each visibleResources(hostServices(selectedHost)) as service}<article class="service-node"><a class="service-title" href={`/service/${service.id}`}><StatusMark status={service.health}/><strong>{service.name}</strong><span>Inspect →</span></a><div class="child-list">{#each visibleResources(serviceProcesses(service)) as process}<a href={`/process/${process.id}`}><span class="branch">└</span><span>{process.name}</span><small class="mono">PID {process.pid || '—'}</small></a>{/each}{#if visibleResources(serviceProcesses(service)).length===0}<span class="empty-child">No matched live process</span>{/if}</div>{#if serviceDependencies(service).length}<div class="service-links"><span>USES</span>{#each serviceDependencies(service) as dependency}<a href={`/dependency/${dependency.id}`}>{dependency.name}</a>{/each}</div>{/if}</article>{/each}</div>
      <div class="node-column"><div class="column-title"><span>Processes</span><b>{visibleResources(unassignedProcesses(selectedHost)).length} visible</b></div><p class="column-note">Host processes not assigned to a configured service.</p><div class="flat-nodes">{#each visibleResources(unassignedProcesses(selectedHost)) as process}<a href={`/process/${process.id}`}><StatusMark status={process.health}/><span><strong>{process.name}</strong><small class="command">{process.command || 'command unavailable'}</small></span><small class="mono">PID {process.pid || '—'}</small></a>{/each}</div></div>
      <div class="node-column"><div class="resource-section"><div class="column-title"><span>Containers</span><b>{visibleResources(hostContainers(selectedHost)).length} visible</b></div><div class="flat-nodes">{#each visibleResources(hostContainers(selectedHost)) as container}<a href={`/container/${container.id}`}><StatusMark status={container.health}/><span>{container.name}</span><small>container</small></a>{/each}</div></div><div class="resource-section"><div class="column-title"><span>Dependencies</span><b>{visibleResources(hostDependencies(selectedHost)).length} visible</b></div><div class="flat-nodes">{#each visibleResources(hostDependencies(selectedHost)) as dependency}<a href={`/dependency/${dependency.id}`}><StatusMark status={dependency.health}/><span>{dependency.name}</span><small>dependency</small></a>{/each}</div></div></div>
    </div>
  </section>{:else}
  <section class="all-node-topology panel" aria-label="All fleet resources"><header><div><span class="eyebrow">Fleet-wide resource map</span><h3>All live nodes</h3><p>Processes remain directly navigable even when no node is selected.</p></div><span class="mono subtle">{topNodes.length} nodes · {allEntities.filter(item => item.kind === 'process' && item.active).length} active processes</span></header><div class="all-node-grid">{#each topNodes as node}{@const nodeProcessesList = nodeProcessEntities(node)}<article class="all-node-card"><a class="all-node-title" href={nodeHref(node)}><StatusMark status={node.health}/><span><strong>{node.name}</strong><small>{nodeType(node)} · {nodeServices(node)} services · {nodeProcessesList.length} processes</small></span><b>Open →</b></a>{#if nodeProcessesList.length}<div class="all-node-processes">{#each nodeProcessesList as process}<a href={`/process/${process.id}`}><span>{process.name}</span><small class="mono">PID {process.pid || '—'}</small></a>{/each}</div>{:else}<p class="empty-child">No active process records</p>{/if}</article>{/each}</div></section>{/if}
  {/if}
{/if}

<style>
  .dashboard-tabs{display:flex;gap:4px;margin:-16px 0 28px;padding:4px;width:max-content;border:1px solid var(--border);border-radius:8px;background:var(--card)}.dashboard-tabs a{padding:8px 13px;border-radius:5px;color:var(--muted-foreground);font-size:12px;font-weight:600;text-decoration:none}.dashboard-tabs a:hover{color:var(--foreground);background:var(--secondary)}.dashboard-tabs a.active{color:var(--primary-foreground);background:var(--primary)}
  .page-heading,.section-heading,.signal-title,.system-detail header,.inventory-row,.table-head,.resource-card header,.heading-actions{display:flex;align-items:center;justify-content:space-between}.page-heading{align-items:flex-end;margin-bottom:24px}.page-heading h1{font-size:40px;line-height:1;margin:8px 0}.page-heading p,.telemetry-heading p{margin:0;color:var(--muted);font-size:13px}.heading-actions{gap:16px}.freshness,.range-label,.section-meta{font-size:10px;color:#a3a3a3;letter-spacing:.06em}.kpi-strip{display:grid;grid-template-columns:1.55fr repeat(5,1fr);margin-bottom:32px}.kpi-strip>div{padding:20px;border-right:1px solid var(--line);min-width:0}.kpi-strip>div:last-child{border:0}.kpi-primary{display:flex;flex-direction:column;gap:6px}.kpi-primary strong{color:var(--cyan);font:600 20px 'Barlow Condensed';letter-spacing:.06em}.kpi-primary strong.attention,.attention{color:var(--amber)}.kpi b{display:block;margin:6px 0 2px;font:600 28px 'Barlow Condensed';color:var(--frost)}.section-heading{margin:0 0 12px}.section-heading h2{font:600 24px 'Barlow Condensed';margin:6px 0 0;letter-spacing:.03em}.inventory{overflow:hidden;margin-bottom:32px}.table-head,.inventory-row{display:grid;grid-template-columns:minmax(250px,2.2fr) minmax(150px,1.25fr) .6fr .65fr .85fr 135px;gap:16px;padding:14px 20px}.table-head{font:10px 'IBM Plex Mono';letter-spacing:.1em;text-transform:uppercase;color:#a3a3a3;background:var(--muted-surface);border-bottom:1px solid var(--line)}.inventory-row{justify-content:initial;color:var(--frost);text-decoration:none;border-bottom:1px solid var(--line);transition:background .15s}.inventory-row:last-child{border-bottom:0}.inventory-row:hover,.inventory-row.selected{background:#1f1f1f}.identity{display:flex;align-items:center;gap:10px;min-width:0}.identity>div{display:flex;flex-direction:column;min-width:0}.identity strong{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.identity .mono{font-size:9px;color:#a3a3a3;margin-top:4px}.agent-dot{width:8px;height:8px;flex:0 0 8px;border-radius:50%;background:var(--cyan);box-shadow:0 0 0 4px var(--cyan-soft)}.agent-dot.offline{background:var(--coral);box-shadow:0 0 0 4px rgba(239,68,68,.1)}.tag,.chip{border:1px solid rgba(245,158,11,.35);border-radius:4px;padding:3px 6px;color:var(--amber);font:9px 'IBM Plex Mono';letter-spacing:.07em}.state-cell{display:flex;align-items:center;gap:8px;min-width:0}.agent-version{font-size:10px;color:#8f8f8f;overflow:hidden;text-overflow:ellipsis}.count{font:14px 'IBM Plex Mono';color:#e5e5e5}.last-frame{font-size:10px;color:#a3a3a3}.last-frame.stale{color:var(--amber)}.telemetry-heading{align-items:flex-end}.signal-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:16px}.signal-card{padding:16px;min-width:0}.signal-title{align-items:flex-start;margin-bottom:8px}.signal-title h3,.resource-card h3,.system-detail h3{font-size:14px;margin:6px 0 0}.metric-value{font:16px 'IBM Plex Mono';color:var(--cyan);white-space:nowrap}.signal-note{display:block;margin-top:8px;color:#a3a3a3;font:12px 'IBM Plex Mono'}.system-detail{margin-bottom:32px}.system-detail header{padding:16px 20px;border-bottom:1px solid var(--line)}.stream-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.stream-list>div{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:11px 20px;border-bottom:1px solid var(--line);min-width:0}.stream-name{font:11px 'IBM Plex Mono';color:#e5e5e5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.stream-list strong{font-size:11px;color:var(--cyan);white-space:nowrap}.lower-heading{margin-top:4px}.quiet-link,.more-link{color:var(--brand-300);font-size:12px;text-decoration:none}.node-map{overflow:hidden}.node-root{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line);background:var(--muted-surface)}.node-root h3{margin:4px 0 0;font-size:15px}.node-icon{width:32px;height:32px;display:grid;place-items:center;border-radius:7px;background:var(--brand-500);color:#111;font-weight:700}.node-counts{margin-left:auto;display:flex;gap:14px;color:#a3a3a3;font-size:10px}.node-groups{display:grid;grid-template-columns:1.25fr 1fr 1fr}.node-column{min-width:0;padding:16px;border-right:1px solid var(--line)}.node-column:last-child{border:0}.column-title{display:flex;justify-content:space-between;margin-bottom:12px;color:#a3a3a3;font-size:11px}.column-title b{font:12px 'IBM Plex Mono';color:var(--frost)}.service-node{border:1px solid var(--line);border-radius:6px;margin-bottom:10px;background:var(--background)}.service-title,.child-list a,.flat-nodes a,.static-node{display:grid;align-items:center;gap:8px;color:var(--frost);text-decoration:none}.service-title{grid-template-columns:74px minmax(0,1fr) auto;padding:10px}.service-title>span:last-child{font-size:10px;color:var(--brand-300)}.child-list{border-top:1px solid var(--line);padding:5px 10px 8px}.child-list a{grid-template-columns:14px minmax(0,1fr) auto;padding:5px 0;font-size:11px}.branch{color:#5c5c5c}.child-list small,.flat-nodes small{color:#8f8f8f;font-size:9px}.flat-nodes a,.static-node{grid-template-columns:74px minmax(0,1fr) auto;padding:9px 0;border-bottom:1px solid var(--line);font-size:11px}.static-node small{color:#8f8f8f}.empty-child,.more-child{display:block;padding:5px 0;color:#8f8f8f;font-size:10px}.more-child{font-family:'IBM Plex Mono'}.resource-card{padding:16px;min-width:0}@media(max-width:1100px){.kpi-strip{grid-template-columns:repeat(3,1fr)}.kpi-primary{grid-column:span 3}.signal-grid{grid-template-columns:repeat(2,1fr)}.node-groups{grid-template-columns:1fr}.node-column{border-right:0;border-bottom:1px solid var(--line)}.table-head,.inventory-row{grid-template-columns:minmax(220px,2fr) minmax(130px,1.1fr) .5fr .6fr .8fr 115px;gap:10px;padding-left:14px;padding-right:14px}}@media(max-width:760px){.page-heading{align-items:flex-start;gap:16px}.page-heading h1{font-size:34px}.heading-actions{align-items:flex-end;flex-direction:column;gap:8px}.freshness{display:none}.kpi-strip{grid-template-columns:repeat(2,1fr)}.kpi-primary{grid-column:span 2}.kpi-strip>div{padding:16px}.table-head{display:none}.inventory-row{display:grid;grid-template-columns:minmax(190px,1fr) auto auto;padding:16px}.inventory-row .state-cell{grid-column:2}.inventory-row .count{display:none}.last-frame{grid-column:2/4}.signal-grid{grid-template-columns:1fr}.telemetry-heading{align-items:flex-start;gap:12px;flex-direction:column}.range-label{display:none}.stream-list{grid-template-columns:1fr}.system-detail header{align-items:flex-start;gap:8px;flex-direction:column}.section-meta{display:none}.node-counts{display:none}}@media(max-width:450px){.page-heading{flex-direction:column}.heading-actions{align-items:flex-start}.kpi-primary strong{font-size:18px}}
  .dashboard-tabs{max-width:100%;overflow-x:auto}.kpi-strip{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.kpi-primary{min-width:250px}.dependency-links{grid-template-columns:1fr}
  .signal-card{padding:20px}.signal-card .eyebrow{font-size:14px}.signal-title{margin-bottom:12px;gap:20px}.signal-title h3{margin-top:8px;font-size:20px;line-height:1.25}.metric-value{font-size:24px;font-weight:600}.signal-note{margin-top:16px;color:#c2c2c2;font-size:16px;line-height:1.5}.system-detail header{padding:20px 24px}.system-detail h3{font-size:20px}.system-detail .eyebrow{font-size:14px}.stream-list>div{padding:14px 24px}.stream-name{font-size:16px}.stream-list .chip{font-size:14px}.stream-list strong{font-size:18px}
  .telemetry-controls{display:flex;align-items:flex-end;gap:12px}.node-picker{display:flex;min-width:280px;max-width:360px;flex-direction:column;gap:6px}.node-picker>span{color:var(--muted-foreground);font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}.node-picker select{width:100%;height:40px;padding:0 12px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--foreground);font:14px Inter,sans-serif;cursor:pointer}.node-picker select:hover{border-color:var(--border-strong)}.node-picker select:focus-visible{border-color:var(--primary);outline:2px solid var(--primary);outline-offset:2px}
  .resource-toolbar{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border);background:var(--muted)}
  .resource-toolbar label{flex:1}.resource-toolbar input{width:100%;padding:9px 11px;border:1px solid var(--border);border-radius:6px;background:var(--background);color:var(--foreground);font:12px Inter,sans-serif}
  .resource-toolbar button{padding:9px 11px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--muted-foreground);font-size:11px;cursor:pointer}.resource-toolbar button:hover,.resource-toolbar button.active{border-color:var(--primary);color:var(--foreground)}
  .column-note{min-height:28px;margin:-5px 0 8px;color:var(--muted-foreground);font-size:10px;line-height:1.4}.resource-section+.resource-section{margin-top:22px;padding-top:18px;border-top:1px solid var(--border)}
  .service-links{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:8px 10px;border-top:1px solid var(--border)}.service-links>span{color:var(--muted-foreground);font:9px 'IBM Plex Mono'}.service-links a{padding:3px 6px;border:1px solid var(--border);border-radius:5px;color:var(--foreground);font-size:10px;text-decoration:none}.service-links a:hover{border-color:var(--primary)}
  .service-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:32px}.service-summary{display:block;padding:16px;color:var(--foreground);text-decoration:none}.service-summary>div{display:flex;align-items:center;justify-content:space-between}.service-summary h3{margin:14px 0 6px;font-size:15px}.service-summary p{margin:0 0 16px;color:var(--muted-foreground);font-size:11px}.service-summary>span{color:var(--primary);font-size:11px}.service-summary:hover{border-color:var(--primary)}
  .dependency-heading{margin-top:30px}.dependency-overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:32px}.dependency-overview-card{min-width:0;padding:17px}.dependency-overview-card header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px}.dependency-overview-card h3{margin:5px 0 0;font:600 20px 'Barlow Condensed'}.dependency-overview-card header>strong{color:var(--cyan);font:600 22px 'IBM Plex Mono'}.dependency-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line)}.dependency-links a{display:flex;align-items:center;justify-content:space-between;gap:12px;min-width:0;padding:11px 12px;color:var(--foreground);background:var(--surface-raised);text-decoration:none}.dependency-links a:hover{background:var(--surface-hover)}.dependency-links .status-cell{display:flex;align-items:center;gap:8px;min-width:0;overflow:hidden;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.dependency-links small{color:var(--muted);font:9px 'IBM Plex Mono';white-space:nowrap}
  .all-node-topology{overflow:hidden;margin-bottom:32px}.all-node-topology>header{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;padding:17px 20px;border-bottom:1px solid var(--line)}.all-node-topology h3{margin:5px 0 0;font-size:17px}.all-node-topology p{margin:4px 0 0;color:var(--muted);font-size:11px}.all-node-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line)}.all-node-card{min-width:0;background:var(--surface-raised)}.all-node-title{display:flex;align-items:center;gap:10px;padding:13px 15px;color:var(--foreground);text-decoration:none}.all-node-title>span{display:flex;min-width:0;flex:1;flex-direction:column;gap:4px}.all-node-title strong{overflow:hidden;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.all-node-title small{color:var(--muted);font:9px 'IBM Plex Mono'}.all-node-title b{color:var(--primary);font-size:10px;white-space:nowrap}.all-node-title:hover{background:var(--surface-hover)}.all-node-processes{max-height:280px;overflow:auto;border-top:1px solid var(--line)}.all-node-processes a{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 15px 8px 39px;color:var(--foreground);font-size:10px;text-decoration:none}.all-node-processes a:hover{background:var(--surface-hover);color:var(--primary)}.all-node-processes small{color:var(--muted);font-size:9px}
  .host-branch{display:flex;align-items:center;gap:8px;margin-left:10px;padding-left:20px;border-left:1px solid var(--border)}.host-branch span{color:var(--muted-foreground);font:9px 'IBM Plex Mono'}.host-branch strong{font-size:13px}
  .table-head,.node-row{grid-template-columns:minmax(220px,2.1fr) 1fr minmax(130px,1.1fr) minmax(100px,.9fr) .55fr .65fr 135px}.node-type{font:9px 'IBM Plex Mono';color:var(--muted-foreground);letter-spacing:.08em}.node-row .agent-id{font-size:10px;color:#a9bac7}.parent-host{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#b6c3ce;font-size:11px}
  .flat-nodes span{min-width:0}.flat-nodes span strong,.flat-nodes .command{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.flat-nodes .command{max-width:260px;margin-top:3px}
  .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
  @media(max-width:1100px){.table-head,.node-row{grid-template-columns:minmax(190px,2fr) 1fr minmax(110px,1fr) minmax(85px,.8fr) .5fr .6fr 110px}}
  @media(max-width:760px){.resource-toolbar{align-items:stretch;flex-direction:column}.telemetry-controls{width:100%;align-items:stretch}.node-picker{width:100%;min-width:0;max-width:none}.kpi-strip{grid-template-columns:repeat(2,1fr)}.service-overview,.dependency-overview{grid-template-columns:1fr}.dependency-links,.all-node-grid{grid-template-columns:1fr}.all-node-topology>header{align-items:flex-start;flex-direction:column}.node-row{grid-template-columns:minmax(180px,1fr) auto auto}.node-row .parent-host,.node-row .agent-id,.node-row .count{display:none}.node-row .last-frame{grid-column:2/4}.node-row .state-cell{grid-column:2}}
  .dependency-count{display:block;margin-top:10px;font-size:10px}
  .application-overview{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(320px,.75fr);overflow:hidden;margin-bottom:32px}.application-services{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line)}.application-services a,.process-overview a{display:flex;align-items:center;gap:10px;min-width:0;padding:11px 13px;color:var(--foreground);background:var(--surface-raised);text-decoration:none}.application-services a:hover,.process-overview a:hover{background:var(--surface-hover)}.application-services a>span{display:flex;min-width:0;flex:1;flex-direction:column;gap:3px}.application-services strong{overflow:hidden;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.application-services small{color:var(--muted);font-size:9px}.application-services b,.process-overview b{color:var(--primary);font-size:9px;font-weight:600;white-space:nowrap}.process-overview{border-left:1px solid var(--line)}.process-overview header{display:flex;align-items:center;justify-content:space-between;padding:12px 13px;border-bottom:1px solid var(--line)}.process-overview header strong{font:11px 'IBM Plex Mono'}.process-overview a{display:grid;grid-template-columns:minmax(0,1fr) auto auto;border-bottom:1px solid var(--line);font-size:10px}.process-overview a:last-child{border-bottom:0}.process-overview small{color:var(--muted);font-size:9px}
  @media(max-width:1000px){.application-overview{grid-template-columns:1fr}.process-overview{border-top:1px solid var(--line);border-left:0}}
  @media(max-width:700px){.application-services{grid-template-columns:1fr}.process-overview a{grid-template-columns:minmax(0,1fr) auto}.process-overview b{display:none}}
</style>
