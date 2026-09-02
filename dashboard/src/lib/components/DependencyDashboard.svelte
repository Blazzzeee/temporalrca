<script lang="ts">
  import type { MetricSeries } from '$lib/types/api';
  import MetricChart from './MetricChart.svelte';
  import { correlationCursor } from '$lib/state/cursor';
  import { formatMetric } from '$lib/utils/format';
  import { dependencyDimensions, filterDependencyMetrics, groupDependencyMetrics } from '$lib/dependencies/dashboard';

  export let dependencyId: string;
  export let kind: string;
  export let metrics: MetricSeries[] = [];

  let selectedDatabase = 'all';
  let selectedQueue = 'all';
  let selectionOwner = '';
  $: databaseOptions = dependencyDimensions(metrics, 'database');
  $: queueOptions = dependencyDimensions(metrics, 'queue');
  $: if (dependencyId !== selectionOwner) {
    selectionOwner = dependencyId;
    selectedDatabase = databaseOptions.includes('workload') ? 'workload' : 'all';
    selectedQueue = 'all';
  }
  $: scopedMetrics = filterDependencyMetrics(metrics, kind, selectedDatabase, selectedQueue);
  $: groups = groupDependencyMetrics(scopedMetrics, kind);
  $: queueCards = queueOptions.map(queue => {
    const queueMetrics = metrics.filter(metric => (metric.attributes?.['messaging.destination.name'] || metric.attributes?.queue) === queue);
    const find = (name: string) => queueMetrics.find(metric => metric.name === name && metric.attributes?.['messaging.destination.kind']) || queueMetrics.find(metric => metric.name === name);
    const depthSeries = find('dependency.queue.depth');
    return {
      queue,
      depth: depthSeries?.buckets.at(-1)?.last ?? null,
      type: String(depthSeries?.attributes?.['messaging.destination.kind'] || depthSeries?.attributes?.queue_type || 'queue').replace('redis_', ''),
      produced: find('dependency.messaging.produced.rate')?.buckets.at(-1)?.last ?? null,
      consumed: find('dependency.messaging.consumed.rate')?.buckets.at(-1)?.last ?? null,
      latency: find('dependency.messaging.processing.latency')?.buckets.at(-1)?.last ?? null,
      failures: find('dependency.messaging.failures')?.buckets.at(-1)?.last ?? null,
      oldestAge: find('dependency.messaging.oldest_item.age')?.buckets.at(-1)?.last ?? null
    };
  });

  function matching(name: string) { return scopedMetrics.filter(metric => metric.name === name); }
  function latestSum(...names: string[]) {
    const values = names.flatMap(name => matching(name)).map(metric => metric.buckets.at(-1)?.last).filter((value): value is number => value != null && Number.isFinite(value));
    return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
  }
  function latest(name: string) { return matching(name)[0]?.buckets.at(-1)?.last ?? null; }
  function cacheHitRatio() {
    const hit = latestSum('dependency.blocks.hit.rate') ?? latestSum('dependency.blocks.hit');
    const read = latestSum('dependency.blocks.read.rate') ?? latestSum('dependency.blocks.read');
    return hit == null || read == null || hit + read === 0 ? null : hit / (hit + read) * 100;
  }
  function display(value: number | null, unit = '1') { return formatMetric(value, unit === 'By' ? 'bytes' : unit); }
  function metricTitle(name: string) { return name.replace(/^dependency\./, '').replaceAll('.', ' / ').replaceAll('_', ' '); }
  function dimensionLabel(metric: MetricSeries) {
    const dimensions = [metric.attributes?.['db.namespace'] || metric.attributes?.['db.name'] || metric.attributes?.database, metric.attributes?.['messaging.destination.name'] || metric.attributes?.queue, metric.attributes?.role, metric.attributes?.derived === 'counter_rate' ? 'derived rate' : null].filter(Boolean);
    return dimensions.length ? dimensions.join(' · ') : 'dependency-wide';
  }
  function chooseQueue(queue: string) { selectedQueue = selectedQueue === queue ? 'all' : queue; }
</script>

<section class="scope-rack panel" aria-label="Dependency metric scope">
  <div class="scope-intro">
    <span class="eyebrow">Signal scope</span>
    <strong>{kind === 'postgresql' ? 'Database lens' : kind === 'redis' ? 'Redis channel lens' : 'Dependency lens'}</strong>
    <small>Global signals remain visible while dimensional streams follow this selection.</small>
  </div>
  {#if kind === 'postgresql' && databaseOptions.length}
    <label><span>Database <b>{databaseOptions.length}</b></span><select bind:value={selectedDatabase} aria-label="Select PostgreSQL database"><option value="all">All databases</option>{#each databaseOptions as database}<option value={database}>{database}</option>{/each}</select></label>
  {:else if kind === 'redis' && queueOptions.length}
    <label><span>Queue or stream <b>{queueOptions.length}</b></span><select bind:value={selectedQueue} aria-label="Select Redis queue or stream"><option value="all">All channels</option>{#each queueOptions as queue}<option value={queue}>{queue}</option>{/each}</select></label>
  {/if}
  <div class="scope-count"><span>VISIBLE / TOTAL</span><strong>{scopedMetrics.length}<i>/</i>{metrics.length}</strong><small>No series limit applied</small></div>
</section>

{#if kind === 'postgresql'}
  <section class="summary-grid postgres-summary" aria-label="PostgreSQL summary">
    <article class="summary-cell panel"><span>RESPONSE</span><strong>{display(latest('dependency.connectivity.latency'), 'seconds')}</strong><small>agent connection latency</small></article>
    <article class="summary-cell panel"><span>SESSIONS</span><strong>{display(latestSum('dependency.connections'))}</strong><small>{selectedDatabase === 'all' ? 'all database backends' : `${selectedDatabase} backends`}</small></article>
    <article class="summary-cell panel"><span>TRANSACTIONS</span><strong>{display(latestSum('dependency.transactions.committed.rate', 'dependency.transactions.rolled_back.rate'), '1/s')}</strong><small>commits + rollbacks</small></article>
    <article class="summary-cell panel"><span>CACHE HIT</span><strong>{display(cacheHitRatio(), '%')}</strong><small>blocks served from cache</small></article>
    <article class="summary-cell panel"><span>WAITING LOCKS</span><strong>{display(latestSum('dependency.locks.waiting'))}</strong><small>{display(latestSum('dependency.locks'))} locks observed</small></article>
    <article class="summary-cell panel"><span>FOOTPRINT</span><strong>{display(latestSum('dependency.storage.size'), 'By')}</strong><small>{selectedDatabase === 'all' ? 'combined database size' : selectedDatabase}</small></article>
  </section>
{:else if kind === 'redis'}
  <section class="summary-grid redis-summary" aria-label="Redis summary">
    <article class="summary-cell panel"><span>RESPONSE</span><strong>{display(latest('dependency.connectivity.latency'), 'seconds')}</strong><small>agent connection latency</small></article>
    <article class="summary-cell panel"><span>COMMAND RATE</span><strong>{display(latestSum('dependency.operations.rate'), '1/s')}</strong><small>{display(latestSum('dependency.operations'))} lifetime commands</small></article>
    <article class="summary-cell panel"><span>CLIENTS</span><strong>{display(latestSum('dependency.connections'))}</strong><small>connected now</small></article>
    <article class="summary-cell panel"><span>MEMORY</span><strong>{display(latestSum('dependency.memory.used'), 'By')}</strong><small>{display(latestSum('dependency.memory.rss'), 'By')} RSS</small></article>
    <article class="summary-cell panel"><span>NETWORK</span><strong>{display(latestSum('dependency.network.received.rate', 'dependency.network.sent.rate'), 'By/s')}</strong><small>combined ingress + egress</small></article>
    <article class="summary-cell panel"><span>ERROR RATE</span><strong>{display(latestSum('dependency.errors.rate', 'dependency.connections.rejected.rate', 'dependency.messaging.failures.rate'), '1/s')}</strong><small>server + processing failures</small></article>
  </section>

  {#if queueCards.length}
    <section class="queue-rack panel" aria-label="Redis queue summary">
      <header><div><span class="eyebrow">Message flow</span><h3>Queue and stream pressure</h3></div><button class="clear-filter" class:visible={selectedQueue !== 'all'} on:click={() => selectedQueue = 'all'}>Show all channels</button></header>
      <div class="queue-cards">{#each queueCards as card}<button class:selected={selectedQueue === card.queue} class:loaded={(card.depth || 0) > 5} on:click={() => chooseQueue(card.queue)} aria-pressed={selectedQueue === card.queue}><i aria-hidden="true"></i><span>{card.type.replaceAll('_', ' ')}</span><strong>{display(card.depth)}<small> depth</small></strong><b>{card.queue}</b><dl><div><dt>IN</dt><dd>{display(card.produced, '1/s')}</dd></div><div><dt>OUT</dt><dd>{display(card.consumed, '1/s')}</dd></div><div><dt>PROCESS</dt><dd>{display(card.latency, 'seconds')}</dd></div><div><dt>FAILED</dt><dd>{display(card.failures)}</dd></div><div><dt>OLDEST</dt><dd>{display(card.oldestAge, 'seconds')}</dd></div></dl></button>{/each}</div>
    </section>
  {/if}
{/if}

<section class="group-index" aria-label="Metric stream groups">{#each groups as group}<a href={`#dependency-${group.key}`}><span>{group.label}</span><strong>{group.metrics.length}</strong></a>{/each}</section>

{#each groups as group}
  <section class="metric-group" id={`dependency-${group.key}`}>
    <header class="group-heading"><div><span class="eyebrow">{kind} / {group.key}</span><h3>{group.label}</h3><p>{group.description}</p></div><strong>{group.metrics.length} {group.metrics.length === 1 ? 'stream' : 'streams'}</strong></header>
    <div class="dependency-streams">{#each group.metrics as metric}<article class="stream-card panel"><header><div><span class="stream-scope">{dimensionLabel(metric)}</span><h4>{metricTitle(metric.name)}</h4></div><strong>{display(metric.buckets.at(-1)?.last ?? null, metric.unit)}</strong></header><MetricChart series={metric} cursorTime={$correlationCursor} height={190}/><footer><span>{metric.unit || 'unitless'}</span><span>{metric.buckets.length} samples</span><span>{String(metric.attributes?.vendor_metric_name || 'normalized')}</span></footer></article>{/each}</div>
  </section>
{/each}

<style>
  .scope-rack{display:grid;grid-template-columns:minmax(260px,1.4fr) minmax(240px,1fr) minmax(170px,.6fr);gap:1px;overflow:hidden;margin-bottom:12px;background:var(--line)}.scope-rack>*{min-width:0;padding:17px 20px;background:var(--card)}.scope-intro{display:flex;flex-direction:column;gap:6px}.scope-intro strong{font-size:17px}.scope-intro small{color:var(--muted);font-size:12px;line-height:1.45}.scope-rack label{display:flex;flex-direction:column;justify-content:center;gap:7px}.scope-rack label>span{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;font-weight:600}.scope-rack label b{color:var(--frost);font:11px 'IBM Plex Mono'}.scope-rack select{width:100%;height:40px;border:1px solid var(--line-strong);border-radius:5px;padding:0 10px;background:var(--background);color:var(--frost);font:12px 'IBM Plex Mono';cursor:pointer}.scope-count{display:flex;flex-direction:column;justify-content:center}.scope-count>span,.summary-cell>span{color:var(--muted);font:9px 'IBM Plex Mono';letter-spacing:.09em}.scope-count strong{margin:6px 0 3px;font:600 24px 'IBM Plex Mono'}.scope-count strong i{margin:0 5px;color:var(--muted);font-size:13px;font-style:normal}.scope-count small{color:var(--success);font-size:9px}.summary-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-bottom:12px}.summary-cell{position:relative;overflow:hidden;padding:15px}.summary-cell:before{position:absolute;content:"";inset:0 auto 0 0;width:2px;background:var(--brand-300)}.redis-summary .summary-cell:before{background:var(--info)}.summary-cell strong{display:block;overflow:hidden;margin:10px 0 5px;font:600 19px 'IBM Plex Mono';text-overflow:ellipsis;white-space:nowrap}.summary-cell small{color:var(--muted);font-size:9px}.queue-rack{overflow:hidden;margin-bottom:12px}.queue-rack>header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--line)}.queue-rack h3{margin:4px 0 0;font-size:15px}.clear-filter{visibility:hidden;border:0;background:none;color:var(--brand-300);font-size:11px;cursor:pointer}.clear-filter.visible{visibility:visible}.queue-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1px;background:var(--line)}.queue-cards button{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 12px;min-width:0;border:0;padding:15px 16px;text-align:left;background:var(--card);color:var(--frost);cursor:pointer}.queue-cards button:hover,.queue-cards button.selected{background:var(--surface-hover)}.queue-cards button.selected{box-shadow:inset 0 0 0 1px var(--brand-300)}.queue-cards button>i{position:absolute;inset:0 auto 0 0;width:3px;background:var(--success)}.queue-cards button.loaded>i{background:var(--warning)}.queue-cards button>span{color:var(--muted);font:8px 'IBM Plex Mono';letter-spacing:.08em;text-transform:uppercase}.queue-cards button>strong{font:600 22px 'IBM Plex Mono'}.queue-cards button>strong small{color:var(--muted);font-size:8px;font-weight:400;text-transform:uppercase}.queue-cards button>b{grid-column:1/-1;overflow:hidden;font:500 11px 'IBM Plex Mono';text-overflow:ellipsis;white-space:nowrap}.queue-cards dl{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));grid-column:1/-1;gap:8px;margin:8px 0 0;padding-top:10px;border-top:1px solid var(--line)}.queue-cards dl div{min-width:0}.queue-cards dt{color:var(--muted);font:7px 'IBM Plex Mono';letter-spacing:.05em}.queue-cards dd{overflow:hidden;margin:4px 0 0;color:var(--frost);font:9px 'IBM Plex Mono';text-overflow:ellipsis;white-space:nowrap}.group-index{display:flex;gap:6px;overflow:auto;margin:22px 0 28px;padding-bottom:3px}.group-index a{display:flex;align-items:center;gap:10px;min-width:max-content;border:1px solid var(--line);border-radius:5px;padding:8px 10px;color:var(--muted);background:var(--card);font-size:10px;text-decoration:none}.group-index a:hover{border-color:var(--line-strong);color:var(--frost)}.group-index strong{color:var(--frost);font:10px 'IBM Plex Mono'}.metric-group{scroll-margin-top:80px;margin-bottom:34px}.group-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-bottom:11px}.group-heading h3{margin:5px 0 2px;font:600 25px 'Barlow Condensed';letter-spacing:.02em}.group-heading p{margin:0;color:var(--muted);font-size:12px}.group-heading>strong{color:var(--muted);font:10px 'IBM Plex Mono'}.dependency-streams{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.stream-card{min-width:0;padding:17px}.stream-card>header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:8px}.stream-card h4{margin:6px 0 0;font-size:15px;font-weight:600}.stream-card header>strong{color:var(--frost);font:600 15px 'IBM Plex Mono';white-space:nowrap}.stream-scope{color:var(--info);font:9px 'IBM Plex Mono';letter-spacing:.05em;text-transform:uppercase}.stream-card footer{display:flex;gap:12px;overflow:hidden;margin-top:11px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font:8px 'IBM Plex Mono';text-transform:uppercase}.stream-card footer span:last-child{overflow:hidden;margin-left:auto;text-overflow:ellipsis;white-space:nowrap}@media(max-width:1150px){.summary-grid{grid-template-columns:repeat(3,1fr)}.scope-rack{grid-template-columns:1.2fr 1fr}.scope-count{grid-column:1/-1}.dependency-streams{grid-template-columns:1fr}}@media(max-width:700px){.scope-rack{grid-template-columns:1fr}.scope-count{grid-column:auto}.summary-grid{grid-template-columns:repeat(2,1fr)}.group-heading{align-items:flex-start;flex-direction:column}.queue-cards{grid-template-columns:1fr}.queue-cards dl{grid-template-columns:repeat(3,1fr)}}@media(max-width:440px){.summary-grid{grid-template-columns:1fr}}
</style>
