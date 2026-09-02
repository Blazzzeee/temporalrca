<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { api } from '$lib/api/client';
  import type { Entity, MetricSeries } from '$lib/types/api';
  import MetricChart from '$lib/components/MetricChart.svelte';
  import StatePanel from '$lib/components/StatePanel.svelte';
  import StatusMark from '$lib/components/StatusMark.svelte';
  import { correlationCursor } from '$lib/state/cursor';
  import { parseDashboardState } from '$lib/state/url';
  import { dashboardNow } from '$lib/state/clock';
  import { formatMetric, formatRange } from '$lib/utils/format';

  let metrics: MetricSeries[] = [], services: Entity[] = [], loading = true, refreshing = false, error = '';
  let timer: ReturnType<typeof setInterval> | undefined;
  let mounted = false, loadedKey = '', showAllLanes = false;
  let loadedRange: { start: Date; end: Date } | undefined;
  $: state = parseDashboardState($page.url, $dashboardNow);
  $: queryKey = [state.live, state.start.toISOString(), state.end.toISOString()].join('|');
  $: queues = queueSummary();
  $: sortedLanes = [...metrics].sort((a, b) => laneRank(a) - laneRank(b));
  $: lanes = showAllLanes ? sortedLanes : sortedLanes.slice(0, 18);
  $: activeServices = services.filter(item => item.active && /producer|consumer|cron|event|database|file/i.test(item.name));

  function liveRange() {
    return state.live ? parseDashboardState($page.url, new Date()) : state;
  }
  async function load() {
    loadedKey = queryKey;
    if (metrics.length) refreshing = true; else loading = true;
    error = '';
    const range = liveRange();
    loadedRange = range;
    try {
      const [nextMetrics, nextServices] = await Promise.all([
        api.activityMetrics(range.start.toISOString(), range.end.toISOString(), 600),
        api.entities('service')
      ]);
      services = nextServices;
      // Keep application streams visible even when a service has just rotated
      // or its inventory heartbeat is briefly behind the metric commit.  The
      // old active-service join made the workload page look empty while the
      // API was returning valid application telemetry.
      metrics = nextMetrics;
    } catch (e) { error = e instanceof Error ? e.message : 'Unable to read workload activity'; }
    finally { loading = false; refreshing = false; }
  }
  function matching(name: string, key?: string, value?: string) {
    return metrics.filter(item => item.name === name && (!key || String(item.attributes?.[key] || '') === value));
  }
  function total(name: string, key?: string, value?: string) {
    return matching(name, key, value).reduce((sum, item) => sum + (item.buckets.at(-1)?.last || 0), 0);
  }
  function queueSummary() {
    const result = new Map<string, number>();
    for (const item of matching('demo_queue_depth')) {
      const queue = String(item.attributes?.queue || 'queue');
      result.set(queue, Math.max(result.get(queue) || 0, item.buckets.at(-1)?.last || 0));
    }
    for (const item of matching('demo_event_stream_depth')) result.set(String(item.attributes?.stream || 'event stream'), item.buckets.at(-1)?.last || 0);
    return [...result.entries()].map(([name, depth]) => ({ name, depth }));
  }
  function laneRank(item: MetricSeries) {
    const order = ['demo_queue_depth', 'demo_event_stream_depth', 'demo_jobs_in_flight', 'demo_jobs_total', 'demo_events_total', 'demo_cron_runs_total', 'demo_database_operations_total'];
    return Math.max(0, order.indexOf(item.name));
  }
  function laneTitle(item: MetricSeries) {
    const label = item.name.replace(/^demo_/, '').replaceAll('_', ' ');
    const detail = [item.attributes?.role, item.attributes?.status, item.attributes?.job_type, item.attributes?.queue, item.attributes?.event_type, item.attributes?.schedule].filter(Boolean).join(' · ');
    return detail ? `${label} — ${detail}` : label;
  }
  function serviceRole(name: string) {
    if (name.includes('producer')) return 'PRODUCE';
    if (name.includes('consumer')) return 'CONSUME';
    if (name.includes('cron')) return 'SCHEDULE';
    if (name.includes('event')) return 'EVENT';
    if (name.includes('database')) return 'DATABASE';
    return 'FILE I/O';
  }
  $: if (mounted && queryKey !== loadedKey) void load();
  onMount(() => { mounted = true; void load(); timer = setInterval(load, 15_000); return () => timer && clearInterval(timer); });
</script>

<svelte:head><title>Workload activity · Temporal RCA</title></svelte:head>

<header class="page-heading"><div><span class="eyebrow">Autonomous workload / activity pulse</span><h1 class="display">Jobs in motion</h1><p>Producer, consumer, scheduled, event-driven, database, and file work across one recorder window.</p></div><button class="button" on:click={load} disabled={loading || refreshing}>{refreshing ? 'Refreshing…' : '↻ Refresh activity'}</button></header>

{#if loading}<StatePanel state="loading" title="Reading workload pulse…" message="Aligning job, event, cron, queue, and database signals." />
{:else if error}<StatePanel state="error" message={error} retry={load} />
{:else if !metrics.length}<StatePanel state="empty" title="No workload metrics yet" message="Start the expanded Compose workloads and wait for the agent's first scrape." />
{:else}
  <section class="flow-strip panel" aria-label="Workload flow summary">
    <div class="flow-step"><span>PRODUCED</span><strong>{formatMetric(total('demo_jobs_total', 'status', 'queued'), '1')}</strong><small>jobs admitted</small></div><i>→</i>
    <div class="flow-step"><span>IN FLIGHT</span><strong>{formatMetric(total('demo_jobs_in_flight'), '1')}</strong><small>executing now</small></div><i>→</i>
    <div class="flow-step"><span>COMPLETED</span><strong>{formatMetric(total('demo_jobs_total', 'status', 'ok'), '1')}</strong><small>consumer results</small></div>
    <div class="flow-step events"><span>EVENTS</span><strong>{formatMetric(total('demo_events_total', 'status', 'processed'), '1')}</strong><small>stream messages handled</small></div>
    <div class="flow-step cron"><span>SCHEDULED</span><strong>{formatMetric(total('demo_cron_runs_total', 'status', 'ok'), '1')}</strong><small>cron runs completed</small></div>
  </section>

  <section class="section-heading"><div><span class="eyebrow">Queue pressure</span><h2>Backlog by channel</h2></div><span class="mono range">{formatRange(loadedRange?.start || state.start, loadedRange?.end || state.end)}</span></section>
  <section class="queue-grid">{#each queues as queue}<article class="queue-card panel" class:loaded={queue.depth > 5}><span class="queue-rail" aria-hidden="true"></span><div><span class="eyebrow">{queue.name.includes('events') ? 'REDIS STREAM' : 'REDIS LIST'}</span><h3>{queue.name}</h3></div><strong>{formatMetric(queue.depth, '1')}</strong><small>{queue.depth ? 'items waiting or retained' : 'channel clear'}</small></article>{/each}</section>

  <section class="section-heading"><div><span class="eyebrow">Process inventory</span><h2>{activeServices.length} active workload services</h2></div></section>
  <section class="service-tape panel">{#each activeServices as service}<a href={`/service/${service.id}`}><StatusMark status={service.health}/><span>{service.name}</span><small>{serviceRole(service.name)}</small></a>{/each}</section>

  <section class="section-heading lanes-heading"><div><span class="eyebrow">Long-range telemetry</span><h2>Activity lanes</h2><p>Representative counters and gauges; use the shared cursor to correlate changes.</p></div><span class="mono range">{lanes.length} of {metrics.length} streams</span></section>
  <section class="lane-grid">{#each lanes as metric}<article class="lane-card panel"><header><div><span class="eyebrow">{String(metric.attributes?.collector || 'APPLICATION')}</span><h3>{laneTitle(metric)}</h3></div><strong>{formatMetric(metric.buckets.at(-1)?.last, metric.unit)}</strong></header><MetricChart series={metric} cursorTime={$correlationCursor} height={190}/></article>{/each}</section>
  {#if !showAllLanes && sortedLanes.length > lanes.length}<button class="button show-all" on:click={() => showAllLanes = true}>Show all {sortedLanes.length} application streams</button>{/if}
{/if}

<style>
  .page-heading,.section-heading,.lane-card header{display:flex;align-items:flex-end;justify-content:space-between;gap:20px}.page-heading{margin-bottom:20px}.page-heading h1{margin:6px 0 4px;font-size:42px}.page-heading p,.section-heading p{margin:0;color:var(--muted);font-size:13px}.flow-strip{display:grid;grid-template-columns:1fr auto 1fr auto 1fr 1fr 1fr;align-items:stretch;overflow:hidden;margin-bottom:30px}.flow-step{position:relative;padding:18px 20px}.flow-step span{color:var(--muted);font:9px 'IBM Plex Mono';letter-spacing:.09em}.flow-step strong{display:block;margin:9px 0 4px;color:var(--cyan);font:600 25px 'IBM Plex Mono'}.flow-step small{color:var(--muted);font-size:10px}.flow-strip>i{align-self:center;color:var(--brand-300);font-style:normal}.flow-step.events{border-left:1px solid var(--line)}.flow-step.cron{border-left:1px solid var(--line)}.section-heading{margin:0 0 11px}.section-heading h2{margin:5px 0 0;font:600 25px 'Barlow Condensed'}.range{color:var(--muted);font-size:9px}.queue-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:30px}.queue-card{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px 12px;overflow:hidden;padding:16px}.queue-rail{position:absolute;inset:0 auto 0 0;width:3px;background:var(--cyan)}.queue-card.loaded .queue-rail{background:var(--amber)}.queue-card h3{overflow:hidden;margin:6px 0 0;font:12px 'IBM Plex Mono';text-overflow:ellipsis;white-space:nowrap}.queue-card>strong{color:var(--cyan);font:24px 'IBM Plex Mono'}.queue-card.loaded>strong{color:var(--amber)}.queue-card>small{grid-column:1/-1;color:var(--muted);font-size:9px}.service-tape{display:flex;flex-wrap:wrap;gap:1px;overflow:hidden;margin-bottom:30px;padding:1px}.service-tape a{display:grid;grid-template-columns:74px minmax(110px,1fr) auto;align-items:center;gap:8px;flex:1 1 245px;padding:11px 13px;color:var(--frost);background:var(--surface-raised);text-decoration:none}.service-tape a:hover{background:var(--surface-hover)}.service-tape a>span{font-size:11px}.service-tape a>small{color:var(--brand-300);font:8px 'IBM Plex Mono';letter-spacing:.08em}.lanes-heading{margin-top:4px}.lane-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.lane-card{min-width:0;padding:17px}.lane-card header{align-items:flex-start;margin-bottom:8px}.lane-card h3{max-width:520px;margin:6px 0 0;font-size:13px;line-height:1.4}.lane-card header>strong{color:var(--cyan);font:14px 'IBM Plex Mono';white-space:nowrap}.show-all{display:block;margin:18px auto 0}@media(max-width:1100px){.flow-strip{grid-template-columns:1fr auto 1fr auto 1fr}.flow-step.events{grid-column:1/3;border-top:1px solid var(--line);border-left:0}.flow-step.cron{grid-column:3/6;border-top:1px solid var(--line)}.queue-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:760px){.page-heading{align-items:flex-start;flex-direction:column}.page-heading h1{font-size:35px}.flow-strip{grid-template-columns:1fr}.flow-strip>i{display:none}.flow-step,.flow-step.events,.flow-step.cron{grid-column:auto;border-top:1px solid var(--line);border-left:0}.queue-grid,.lane-grid{grid-template-columns:1fr}.range{display:none}}
</style>
