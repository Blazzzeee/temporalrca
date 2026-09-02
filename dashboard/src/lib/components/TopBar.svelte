<script lang="ts">
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import TimeControls from './TimeControls.svelte';
  export let live = true;
  export let connected = true;
  export let timezone = 'local';
  export let rangeMinutes = 1440;
  export let onPreset: (m: number) => void;
  export let onLive: () => void;
  export let onTimezone: (v: string) => void;
  export let onMenu: () => void = () => {};
  let searchQuery = '';
  let helpOpen = false;
  $: section = $page.url.pathname === '/' ? 'Overview' : $page.url.pathname.split('/')[1]?.replace(/-/g, ' ') || 'Overview';
  function search() {
    const query = searchQuery.trim();
    if (query) void goto(`/timeline?filters=${encodeURIComponent(query)}`);
  }
</script>

<header>
  <div class="left">
    <button class="menu" aria-label="Open navigation" on:click={onMenu}><span></span><span></span><span></span></button>
    <a href="/" class="brand" aria-label="Temporal RCA overview"><span class="brand-mark" aria-hidden="true">T</span><span class="brand-name">Temporal RCA</span></a>
    <span class="divider" aria-hidden="true"></span>
    <nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">Operations</a><span>/</span><strong>{section}</strong></nav>
  </div>
  <div class="right">
    <form class="search" role="search" on:submit|preventDefault={search}><span class="search-icon" aria-hidden="true">⌕</span><label class="sr-only" for="global-search">Search telemetry</label><input id="global-search" bind:value={searchQuery} type="search" placeholder="Search telemetry" /><button class="sr-only" type="submit">Search</button></form>
    <div class="connection" class:down={!connected}><span class="connection-dot"></span><span>{connected ? 'Live' : 'Polling'}</span></div>
    <div class="controls-wrap"><TimeControls {live} {timezone} {rangeMinutes} {onPreset} {onLive} {onTimezone}/></div>
    <div class="help-wrap"><button class="help" aria-label="Help and keyboard shortcuts" aria-expanded={helpOpen} on:click={() => helpOpen = !helpOpen}>?</button>{#if helpOpen}<div class="help-panel" role="status"><strong>Console controls</strong><span>Search opens matching logs and events on Timeline.</span><span>Drag a chart to zoom; use arrow keys to move its cursor.</span><span>Use 1h–7d above to change the shared time range.</span></div>{/if}</div>
  </div>
</header>

<style>
  header { height:60px; display:flex; align-items:center; justify-content:space-between; gap:20px; padding:0 24px; position:sticky; top:0; z-index:40; border-bottom:1px solid var(--line); background:rgba(8,8,8,.94); backdrop-filter:blur(12px); }
  .left,.right,.brand,.breadcrumbs,.connection { display:flex; align-items:center; }
  .left { min-width:0; gap:14px; }.brand { gap:8px; color:var(--frost); text-decoration:none; white-space:nowrap; }
  .brand-mark { width:26px; height:26px; display:grid; place-items:center; border-radius:6px; background:var(--brand-500); color:#111; font-size:14px; font-weight:700; }
  .brand-name { font-size:14px; font-weight:700; letter-spacing:-.02em; }.divider { width:1px; height:20px; background:var(--line); margin-left:3px; }
  .breadcrumbs { gap:9px; min-width:0; color:var(--muted); font-size:12px; }.breadcrumbs a { color:var(--muted); text-decoration:none; }.breadcrumbs a:hover { color:var(--frost); }.breadcrumbs strong { color:var(--frost); font-weight:600; text-transform:capitalize; white-space:nowrap; }
  .right { gap:12px; min-width:0; }.search { width:190px; height:32px; display:flex; align-items:center; gap:7px; padding:0 9px; border:1px solid var(--line); border-radius:6px; background:var(--navy); color:var(--muted); }.search:focus-within { border-color:var(--brand-500); box-shadow:0 0 0 3px rgba(255,255,255,.15); }.search-icon { font-size:19px; line-height:1; transform:rotate(-20deg); }.search input { width:100%; min-width:0; border:0; outline:0; background:transparent; color:var(--frost); font-size:11px; }.search input::placeholder { color:var(--muted); }
  .connection { gap:6px; color:var(--cyan); font:10px 'IBM Plex Mono',monospace; text-transform:uppercase; white-space:nowrap; }.connection-dot { width:7px; height:7px; border-radius:50%; background:var(--cyan); }.connection.down { color:var(--amber); }.connection.down .connection-dot { background:var(--amber); }
  .controls-wrap { padding-left:12px; border-left:1px solid var(--line); } :global(.controls) { gap:3px !important; } :global(.controls .button) { min-height:30px; padding:4px 8px; border-color:var(--line); border-radius:5px; color:var(--muted); background:var(--navy); font-size:10px; } :global(.controls .button:hover) { border-color:var(--brand-500); background:var(--navy2); } :global(.controls .button.primary) { color:#111; border-color:var(--brand-500); background:var(--brand-500); } :global(.controls .live) { font-size:9px; } :global(.controls .tz select) { color:var(--muted); }
  .help-wrap{position:relative}.help { width:24px; height:24px; border:1px solid var(--line); border-radius:50%; background:var(--navy); color:var(--muted); font:600 12px Inter,sans-serif; cursor:pointer; }.help:hover { border-color:var(--line-strong); color:var(--frost); }.help-panel{position:absolute;top:34px;right:0;display:flex;width:290px;flex-direction:column;gap:8px;padding:14px;border:1px solid var(--line-strong);border-radius:7px;background:var(--surface-raised);box-shadow:0 14px 36px rgba(0,0,0,.34);color:var(--muted);font-size:11px;line-height:1.45}.help-panel strong{color:var(--frost);font-size:12px}
  .menu { display:none; width:30px; height:30px; place-content:center; gap:4px; border:1px solid var(--line); border-radius:6px; background:var(--navy); cursor:pointer; }.menu span { width:13px; height:1.5px; background:var(--muted); }
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  @media (max-width:1000px) { header { padding:0 16px; }.search { width:150px; }.help-wrap { display:none; } }
  @media (max-width:760px) { header { padding:0 12px; gap:8px; }.menu { display:grid; }.brand-name,.divider,.breadcrumbs,.search,.controls-wrap { display:none; }.right { margin-left:auto; gap:9px; }.connection span:last-child { display:none; } }
</style>
