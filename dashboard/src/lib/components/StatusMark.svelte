<script lang="ts">
  import type { Health } from '$lib/types/api';
  export let status: Health;
  export let label: string = status;
</script>
<span class="status" class:ok={status === 'healthy'} class:warn={status === 'degraded'} class:bad={status === 'offline'} aria-label={`Status: ${label}`}>
  <span class="status-glyph" aria-hidden="true">{status === 'healthy' ? '✓' : status === 'degraded' ? '!' : status === 'offline' ? '×' : '·'}</span>
  {#if label}<span>{label}</span>{/if}
</span>
<style>
  .status { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font:600 10px Inter,sans-serif; text-transform:uppercase; letter-spacing:.04em; }.status-glyph { width:16px; height:16px; display:grid; place-items:center; border-radius:50%; color:var(--muted); background:var(--navy2); font-size:11px; font-weight:700; }.ok { color:var(--cyan); }.ok .status-glyph { color:var(--cyan); background:var(--cyan-soft); }.warn { color:var(--amber); }.warn .status-glyph { color:var(--amber); background:rgba(245,158,11,.14); }.bad { color:var(--coral); }.bad .status-glyph { color:var(--coral); background:rgba(239,68,68,.14); }
</style>
