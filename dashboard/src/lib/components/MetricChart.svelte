<script lang="ts">
  import { afterUpdate, onDestroy, onMount } from 'svelte';
  import { page } from '$app/stores';
  import { Chart, Filler, LinearScale, LineController, LineElement, PointElement, Tooltip } from 'chart.js';
  import type { MetricSeries } from '$lib/types/api';
  import { closestCursorIndex, correlationCursor, moveCursorIndex } from '$lib/state/cursor';
  import { formatMetric, formatTime } from '$lib/utils/format';
  import { parseDashboardState } from '$lib/state/url';
  import { dashboardNow } from '$lib/state/clock';

  Chart.register(Filler, LinearScale, LineController, LineElement, PointElement, Tooltip);

  export let series: MetricSeries;
  export let height = 150;
  export let cursorTime: number | null = null;
  export let onRange: ((start: number, end: number) => void) | undefined = undefined;
  export let rangeStart: string | number | Date | undefined = undefined;
  export let rangeEnd: string | number | Date | undefined = undefined;

  let canvas: HTMLCanvasElement;
  let chart: Chart<'line'> | undefined;
  let resizeObserver: ResizeObserver | undefined;
  let summary = '';
  let cursorValue = '';
  let cursorLabel = '';
  let cursorVisible = false;
  let dragStart: number | undefined;
  let renderedKey = '';
  $: insight = metricInsight();
  $: dashboardRange = parseDashboardState($page.url, $dashboardNow);
  $: resolvedRangeStart = rangeStart ?? dashboardRange.start;
  $: resolvedRangeEnd = rangeEnd ?? dashboardRange.end;

  const dateLabel = (value: number) => formatTime(value, 'local');
  const values = () => series.buckets.map((bucket) => ({ x: Date.parse(bucket.timestamp), y: bucket.average ?? bucket.last }));
  const colorFor = () => series.name.startsWith('system.') ? '#d4d4d4' : '#a3a3a3';
  const chartKey = () => `${series.id}:${series.buckets.length}:${series.buckets.at(-1)?.timestamp || ''}:${new Date(resolvedRangeStart).getTime()}:${new Date(resolvedRangeEnd).getTime()}`;

  const cursorPlugin = {
    id: 'correlation-cursor',
    afterDatasetsDraw(instance: Chart) {
      if (cursorTime == null || !instance.chartArea || !instance.scales.x) return;
      const points = values();
      if (!points.length) return;
      const index = closestCursorIndex(points.map((point) => point.x), cursorTime);
      const x = instance.scales.x.getPixelForValue(points[index].x);
      if (!Number.isFinite(x)) return;
      const context = instance.ctx;
      context.save(); context.strokeStyle = '#fafafa'; context.lineWidth = 1; context.setLineDash([4, 4]);
      context.beginPath(); context.moveTo(x, instance.chartArea.top); context.lineTo(x, instance.chartArea.bottom); context.stroke(); context.restore();
    }
  };

  const sparseSamplePlugin = {
    id: 'sparse-sample-marker',
    afterDatasetsDraw(instance: Chart) {
      const points = values().filter((point) => point.y != null && Number.isFinite(point.y as number));
      if (points.length !== 1 || !instance.chartArea || !instance.scales.x || !instance.scales.y) return;
      const x = instance.scales.x.getPixelForValue(points[0].x);
      const y = instance.scales.y.getPixelForValue(points[0].y as number);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const context = instance.ctx;
      context.save();
      context.strokeStyle = '#f7f7f8'; context.fillStyle = '#171717'; context.lineWidth = 2;
      context.beginPath(); context.moveTo(Math.max(instance.chartArea.left, x - 22), y); context.lineTo(Math.min(instance.chartArea.right, x + 22), y); context.stroke();
      context.beginPath(); context.arc(x, y, 6, 0, Math.PI * 2); context.fill(); context.stroke();
      context.restore();
    }
  };

  function updateSummary() {
    const points = values();
    const valid = points.filter((point) => point.y != null && Number.isFinite(point.y as number));
    summary = `${series.name}. ${valid.length} samples. Latest ${formatMetric(valid.at(-1)?.y, series.unit)}.`;
  }

  function metricInsight() {
    const valid = values().map((point) => point.y).filter((value): value is number => value != null && Number.isFinite(value));
    if (!valid.length) return { min: null, average: null, max: null, change: null, trend: 'No data' };
    const first = valid[0];
    const last = valid.at(-1) as number;
    const change = first === 0 ? null : ((last - first) / Math.abs(first)) * 100;
    return { min: Math.min(...valid), average: valid.reduce((sum, value) => sum + value, 0) / valid.length, max: Math.max(...valid), change, trend: change == null || Math.abs(change) < 0.5 ? 'Flat' : change > 0 ? 'Rising' : 'Falling' };
  }

  function chartConfig() {
    const line = colorFor();
    const styles = getComputedStyle(document.documentElement);
    const text = styles.getPropertyValue('--muted').trim() || '#8f9aa5';
    const grid = styles.getPropertyValue('--line').trim() || '#30363d';
    const points = values();
    const onlyTimestamp = points.length === 1 ? points[0].x : undefined;
    const requestedMin = new Date(resolvedRangeStart).getTime();
    const requestedMax = new Date(resolvedRangeEnd).getTime();
    const xMin = Number.isFinite(requestedMin) ? requestedMin : onlyTimestamp == null ? points[0]?.x : onlyTimestamp - 30_000;
    const xMax = Number.isFinite(requestedMax) ? requestedMax : onlyTimestamp == null ? points.at(-1)?.x : onlyTimestamp + 30_000;
    return {
      type: 'line' as const,
      data: { datasets: [{ label: series.name, data: points, borderColor: line, backgroundColor: `${line}16`, borderWidth: 2, pointRadius: points.length < 3 ? 5 : 0, pointBackgroundColor: '#171717', pointBorderColor: '#f7f7f8', pointBorderWidth: 2, pointHoverRadius: 6, pointHoverBackgroundColor: line, pointHoverBorderColor: '#f7f7f8', fill: true, tension: 0.22, spanGaps: false }] },
      plugins: [cursorPlugin, sparseSamplePlugin],
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { intersect: false, mode: 'index' as const },
        layout: { padding: { top: 9, right: 10, bottom: 3, left: 2 } },
        plugins: {
          legend: { display: false },
          tooltip: { displayColors: false, padding: 14, backgroundColor: 'var(--card)', borderColor: '#464d55', borderWidth: 1, titleColor: '#d4d4d4', bodyColor: '#f7f7f8', titleFont: { family: 'IBM Plex Mono', size: 16 }, bodyFont: { family: 'IBM Plex Mono', size: 20, weight: 'bold' as const }, callbacks: { title: (items: any[]) => items.length ? dateLabel(Number(items[0].parsed.x)) : '', label: (context: any) => `${formatMetric(context.parsed.y, series.unit)}` } }
        },
        scales: {
          x: { type: 'linear' as const, min: xMin, max: xMax, grid: { color: `${grid}99`, drawTicks: false }, border: { display: false }, ticks: { color: text, maxTicksLimit: 5, padding: 9, font: { family: 'IBM Plex Mono', size: 16, weight: 600 }, callback: (value: string | number) => dateLabel(Number(value)) } },
          y: { grid: { color: `${grid}99`, drawTicks: false }, border: { display: false }, ticks: { color: text, maxTicksLimit: 4, padding: 10, font: { family: 'IBM Plex Mono', size: 16, weight: 600 }, callback: (value: string | number) => formatMetric(Number(value), series.unit) } }
        },
        onHover: (_event: unknown, elements: any[]) => { const point = elements[0]; if (!point) { cursorVisible = false; return; } const bucket = series.buckets[point.index]; if (!bucket) return; correlationCursor.set(Date.parse(bucket.timestamp)); cursorValue = formatMetric(bucket.average ?? bucket.last, series.unit); cursorLabel = dateLabel(Date.parse(bucket.timestamp)); cursorVisible = true; }
      }
    };
  }

  function createChart() {
    if (!canvas) return;
    updateSummary();
    chart = new Chart(canvas, chartConfig() as any);
    renderedKey = chartKey();
    resizeObserver = new ResizeObserver(() => chart?.resize());
    if (canvas.parentElement) resizeObserver.observe(canvas.parentElement);
  }

  function syncChart() {
    if (!chart) return;
    const nextKey = chartKey();
    if (nextKey !== renderedKey) {
      updateSummary();
      chart.data = chartConfig().data as any;
      chart.options = chartConfig().options as any;
      renderedKey = nextKey;
    }
    chart.update('none');
  }

  onMount(createChart);
  afterUpdate(syncChart);
  onDestroy(() => { resizeObserver?.disconnect(); chart?.destroy(); chart = undefined; });

  function keyboard(event: KeyboardEvent) {
    const points = values(); if (!points.length) return;
    const current = cursorTime != null ? closestCursorIndex(points.map((point) => point.x), cursorTime) : points.length - 1;
    const next = moveCursorIndex(current, event.key, points.length);
    if (next !== current || ['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); correlationCursor.set(points[next].x); }
  }
  function pointerDown(event: PointerEvent) { if (chart) dragStart = chart.scales.x.getValueForPixel(event.offsetX); }
  function rangeChanged(event: PointerEvent) {
    if (!chart || onRange == null || dragStart == null) return;
    const startX = dragStart; const end = chart.scales.x.getValueForPixel(event.offsetX); const points = values(); const min = points[0]?.x; const max = points.at(-1)?.x;
    dragStart = undefined;
    if (min == null || max == null || end == null || Math.abs(end - startX) < (max - min) * .01) return;
    const start = Math.max(min, Math.min(max, Math.min(startX, end))); const finish = Math.max(min, Math.min(max, Math.max(startX, end)));
    if (finish - start > (max - min) * .02 && (start > min || finish < max)) onRange(start, finish);
  }

  $: ariaValue = cursorTime == null ? 0 : closestCursorIndex(series.buckets.map((bucket) => Date.parse(bucket.timestamp)), cursorTime);
</script>

<div class="chart-wrap">
  <div class="chart" style={`height:${height}px`} role="img" aria-label={summary || `${series.name} metric chart`}><canvas bind:this={canvas} aria-hidden="true" on:pointerdown={pointerDown} on:pointerup={rangeChanged}></canvas></div>
  <dl class="insight-row" aria-label="Metric statistics"><div><dt>Min</dt><dd>{formatMetric(insight.min, series.unit)}</dd></div><div><dt>Avg</dt><dd>{formatMetric(insight.average, series.unit)}</dd></div><div><dt>Max</dt><dd>{formatMetric(insight.max, series.unit)}</dd></div><div><dt>Change</dt><dd class:positive={insight.change != null && insight.change > 0} class:negative={insight.change != null && insight.change < 0}>{insight.change == null ? '—' : `${insight.change > 0 ? '+' : ''}${insight.change.toFixed(1)}%`}</dd></div><div class="trend"><dt>Trend</dt><dd>{insight.trend}</dd></div></dl>
  <div class="sr-only" aria-live="polite">{summary}</div>
  <div class="chart-readout" class:visible={cursorVisible} aria-hidden="true"><strong>{cursorValue}</strong><span>{cursorLabel}</span></div>
  <div class="keyboard-surface" tabindex="0" role="slider" aria-label={`${series.name} metric chart. Use left and right arrow keys to move the correlation cursor.`} aria-valuemin="0" aria-valuemax={Math.max(0, series.buckets.length - 1)} aria-valuenow={ariaValue} on:keydown={keyboard}></div>
</div>

<style>
  .chart-wrap { position:relative; min-width:0; }.chart { position:relative; width:100%; min-width:0; }.chart canvas { display:block; width:100% !important; height:100% !important; }.insight-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(116px,1fr)); gap:16px 14px; margin:16px 0 0; padding-top:16px; border-top:1px solid var(--line); }.insight-row>div { min-width:0; }.insight-row dt { color:#b8b8b8; font:600 16px 'IBM Plex Mono',monospace; text-transform:uppercase; letter-spacing:.06em; }.insight-row dd { margin:7px 0 0; color:#e1e7ec; font:600 20px 'IBM Plex Mono',monospace; white-space:nowrap; }.insight-row dd.positive { color:var(--success); }.insight-row dd.negative { color:var(--coral); }.insight-row .trend dd { color:var(--cyan); }.chart-readout { position:absolute; top:10px; right:12px; display:flex; align-items:baseline; gap:10px; max-width:calc(100% - 24px); padding:10px 14px; pointer-events:none; opacity:0; border:1px solid var(--line-strong); border-radius:6px; background:rgba(17,17,17,.96); box-shadow:0 4px 12px rgba(0,0,0,.2); transition:opacity .12s ease; }.chart-readout.visible { opacity:1; }.chart-readout strong { overflow:hidden; color:var(--frost); font:600 20px 'IBM Plex Mono',monospace; text-overflow:ellipsis; white-space:nowrap; }.chart-readout span { color:var(--muted); font:600 16px 'IBM Plex Mono',monospace; white-space:nowrap; }.keyboard-surface { position:absolute; inset:0; pointer-events:none; border-radius:5px; }.keyboard-surface:focus-visible { outline:2px solid var(--brand-300); outline-offset:2px; }.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
  @media (prefers-reduced-motion:reduce) { .chart-readout { transition:none; } }
</style>
