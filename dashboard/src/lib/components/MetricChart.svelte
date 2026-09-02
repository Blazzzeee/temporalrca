<script lang="ts">
  import { afterUpdate, onDestroy, onMount } from 'svelte';
  import { Chart, Filler, LinearScale, LineController, LineElement, PointElement, Tooltip } from 'chart.js';
  import type { MetricSeries } from '$lib/types/api';
  import { closestCursorIndex, correlationCursor, moveCursorIndex } from '$lib/state/cursor';
  import { formatMetric, formatTime } from '$lib/utils/format';

  Chart.register(Filler, LinearScale, LineController, LineElement, PointElement, Tooltip);

  export let series: MetricSeries;
  export let height = 150;
  export let cursorTime: number | null = null;
  export let onRange: ((start: number, end: number) => void) | undefined = undefined;

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

  const dateLabel = (value: number) => formatTime(value, 'local');
  const values = () => series.buckets.map((bucket) => ({ x: Date.parse(bucket.timestamp), y: bucket.average ?? bucket.last }));
  const colorFor = () => series.name.startsWith('system.') ? '#42d4f4' : '#f48120';
  const chartKey = () => `${series.id}:${series.buckets.length}:${series.buckets.at(-1)?.timestamp || ''}`;

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
      context.save(); context.strokeStyle = '#f48120'; context.lineWidth = 1; context.setLineDash([4, 4]);
      context.beginPath(); context.moveTo(x, instance.chartArea.top); context.lineTo(x, instance.chartArea.bottom); context.stroke(); context.restore();
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
    return {
      type: 'line' as const,
      data: { datasets: [{ label: series.name, data: points, borderColor: line, backgroundColor: `${line}16`, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: line, pointHoverBorderColor: '#f7f7f8', fill: true, tension: 0.22, spanGaps: false }] },
      plugins: [cursorPlugin],
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { intersect: false, mode: 'index' as const },
        layout: { padding: { top: 9, right: 10, bottom: 3, left: 2 } },
        plugins: {
          legend: { display: false },
          tooltip: { displayColors: false, padding: 10, backgroundColor: '#111820', borderColor: '#464d55', borderWidth: 1, titleColor: '#bcc5ce', bodyColor: '#f7f7f8', callbacks: { title: (items: any[]) => items.length ? dateLabel(Number(items[0].parsed.x)) : '', label: (context: any) => `${formatMetric(context.parsed.y, series.unit)}` } }
        },
        scales: {
          x: { type: 'linear' as const, min: points[0]?.x, max: points.at(-1)?.x, grid: { color: `${grid}99`, drawTicks: false }, border: { display: false }, ticks: { color: text, maxTicksLimit: 5, padding: 6, font: { family: 'IBM Plex Mono', size: 9 }, callback: (value: string | number) => dateLabel(Number(value)) } },
          y: { grid: { color: `${grid}99`, drawTicks: false }, border: { display: false }, ticks: { color: text, maxTicksLimit: 4, padding: 7, font: { family: 'IBM Plex Mono', size: 9 }, callback: (value: string | number) => formatMetric(Number(value), series.unit) } }
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
  <div class="chart" style={`height:${height}px`}><canvas bind:this={canvas} role="img" aria-label={summary || `${series.name} metric chart`} on:pointerdown={pointerDown} on:pointerup={rangeChanged}></canvas></div>
  <dl class="insight-row" aria-label="Metric statistics"><div><dt>Min</dt><dd>{formatMetric(insight.min, series.unit)}</dd></div><div><dt>Avg</dt><dd>{formatMetric(insight.average, series.unit)}</dd></div><div><dt>Max</dt><dd>{formatMetric(insight.max, series.unit)}</dd></div><div><dt>Change</dt><dd class:positive={insight.change != null && insight.change > 0} class:negative={insight.change != null && insight.change < 0}>{insight.change == null ? '—' : `${insight.change > 0 ? '+' : ''}${insight.change.toFixed(1)}%`}</dd></div><div class="trend"><dt>Trend</dt><dd>{insight.trend}</dd></div></dl>
  <div class="sr-only" aria-live="polite">{summary}</div>
  <div class="chart-readout" class:visible={cursorVisible} aria-hidden="true"><strong>{cursorValue}</strong><span>{cursorLabel}</span></div>
  <div class="keyboard-surface" tabindex="0" role="slider" aria-label={`${series.name} metric chart. Use left and right arrow keys to move the correlation cursor.`} aria-valuemin="0" aria-valuemax={Math.max(0, series.buckets.length - 1)} aria-valuenow={ariaValue} on:keydown={keyboard}></div>
</div>

<style>
  .chart-wrap { position:relative; min-width:0; }.chart { position:relative; width:100%; min-width:0; }.chart canvas { display:block; width:100% !important; height:100% !important; }.insight-row { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:6px; margin:7px 0 0; padding-top:7px; border-top:1px solid rgba(48,54,61,.7); }.insight-row>div { min-width:0; }.insight-row dt { color:#6f7d8b; font:8px 'IBM Plex Mono',monospace; text-transform:uppercase; letter-spacing:.06em; }.insight-row dd { overflow:hidden; margin:3px 0 0; color:#c2ccd4; font:10px 'IBM Plex Mono',monospace; text-overflow:ellipsis; white-space:nowrap; }.insight-row dd.positive { color:var(--success); }.insight-row dd.negative { color:var(--coral); }.insight-row .trend dd { color:var(--cyan); }.chart-readout { position:absolute; top:8px; right:10px; display:flex; align-items:baseline; gap:8px; max-width:calc(100% - 20px); padding:5px 8px; pointer-events:none; opacity:0; border:1px solid var(--line-strong); border-radius:5px; background:rgba(17,24,32,.94); box-shadow:0 4px 12px rgba(0,0,0,.2); transition:opacity .12s ease; }.chart-readout.visible { opacity:1; }.chart-readout strong { overflow:hidden; color:var(--frost); font:600 12px 'IBM Plex Mono',monospace; text-overflow:ellipsis; white-space:nowrap; }.chart-readout span { color:var(--muted); font:9px 'IBM Plex Mono',monospace; white-space:nowrap; }.keyboard-surface { position:absolute; inset:0; pointer-events:none; border-radius:5px; }.keyboard-surface:focus-visible { outline:2px solid var(--brand-300); outline-offset:2px; }.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
  @media (prefers-reduced-motion:reduce) { .chart-readout { transition:none; } }
</style>
