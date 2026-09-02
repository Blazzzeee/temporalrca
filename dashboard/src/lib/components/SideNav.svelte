<script lang="ts">
  export let path = '/';
  export let open = false;
  export let onToggle: () => void = () => {};
  const primary = [
    { href: '/', label: 'Overview', hint: 'Fleet at a glance', icon: 'grid' },
    { href: '/?view=topology', label: 'Fleet', hint: 'Resource topology', icon: 'server' },
    { href: '/activity', label: 'Workloads', hint: 'Jobs, queues & schedules', icon: 'activity' },
    { href: '/timeline', label: 'Timeline', hint: 'Correlate signals', icon: 'activity' }
  ];
  const observe = [
    { href: '/experiments', label: 'Experiments', hint: 'Ground truth runs', icon: 'flask' },
    { href: '/health', label: 'Collector health', hint: 'Agent connectivity', icon: 'pulse' }
  ];
  function isActive(href: string) {
    const [target, query] = href.split('?');
    const [current, currentQuery] = path.split('?');
    if (query) return current === target && currentQuery === query;
    return target === '/' ? current === '/' && !currentQuery : current === target || current.startsWith(`${target}/`);
  }
</script>

<svelte:window on:keydown={(event) => event.key === 'Escape' && open && onToggle()} />
{#if open}<button class="scrim" aria-label="Close navigation" on:click={onToggle}></button>{/if}
<nav class:open aria-label="Primary navigation">
  <div class="workspace">
    <div class="workspace-mark" aria-hidden="true">T</div>
    <div class="workspace-copy"><strong>Temporal RCA</strong><span>Operations workspace</span></div>
  </div>
  <div class="nav-scroll">
    <p class="nav-label">Workspace</p>
    {#each primary as item}
      <a href={item.href} class:active={isActive(item.href)} aria-current={isActive(item.href) ? 'page' : undefined} on:click={() => open && onToggle()}>
        <span class="nav-icon" aria-hidden="true">
          {#if item.icon === 'grid'}<svg viewBox="0 0 20 20"><rect x="3" y="3" width="5" height="5" rx="1"/><rect x="12" y="3" width="5" height="5" rx="1"/><rect x="3" y="12" width="5" height="5" rx="1"/><rect x="12" y="12" width="5" height="5" rx="1"/></svg>
          {:else if item.icon === 'server'}<svg viewBox="0 0 20 20"><rect x="3" y="3" width="14" height="5" rx="1.5"/><rect x="3" y="12" width="14" height="5" rx="1.5"/><path d="M6 5.5h.01M6 14.5h.01M9 5.5h5M9 14.5h5"/></svg>
          {:else}<svg viewBox="0 0 20 20"><path d="M3 12.5c2.2 0 2.2-5 4.5-5s2.3 4 4.5 4 2.3-6 5-6"/><path d="M3 16h14"/></svg>{/if}
        </span>
        <span class="nav-copy"><strong>{item.label}</strong><small>{item.hint}</small></span>
        {#if isActive(item.href)}<span class="active-marker" aria-hidden="true"></span>{/if}
      </a>
    {/each}
    <p class="nav-label section-label">Observe</p>
    {#each observe as item}
      <a href={item.href} class:active={isActive(item.href)} aria-current={isActive(item.href) ? 'page' : undefined} on:click={() => open && onToggle()}>
        <span class="nav-icon" aria-hidden="true">
          {#if item.icon === 'flask'}<svg viewBox="0 0 20 20"><path d="M8 3h4M9 3v5l-4.5 7.1A1.2 1.2 0 0 0 5.5 17h9a1.2 1.2 0 0 0 1-1.9L11 8V3"/><path d="M6.3 13h7.4"/></svg>
          {:else}<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="6.5"/><path d="M10 6v4l2.5 1.5M10 2v1M10 17v1M2 10h1M17 10h1"/></svg>{/if}
        </span>
        <span class="nav-copy"><strong>{item.label}</strong><small>{item.hint}</small></span>
        {#if isActive(item.href)}<span class="active-marker" aria-hidden="true"></span>{/if}
      </a>
    {/each}
  </div>
  <div class="nav-footer">
    <div class="system-state"><span class="state-dot"></span><span><strong>Live monitoring</strong><small>Open Collector health for status</small></span></div>
    <div class="account" aria-label="Local operator session"><span class="avatar">OP</span><span class="account-copy"><strong>Operator</strong><small>local console</small></span></div>
  </div>
</nav>

<style>
  nav { width:240px; min-height:calc(100vh - 60px); flex:0 0 240px; border-right:1px solid var(--line); background:var(--surface-raised); display:flex; flex-direction:column; position:sticky; top:60px; z-index:30; }
  .workspace { display:flex; align-items:center; gap:10px; min-height:72px; padding:16px; border-bottom:1px solid var(--line); }
  .workspace-mark { width:30px; height:30px; display:grid; place-items:center; border-radius:7px; background:var(--brand-500); color:#fff; font:700 17px Inter,sans-serif; box-shadow:0 1px 2px rgba(191,72,0,.25); }
  .workspace-copy { min-width:0; flex:1; display:flex; flex-direction:column; gap:2px; }
  .workspace-copy strong { font-size:13px; color:var(--text); letter-spacing:-.01em; }
  .workspace-copy span { font-size:10px; color:var(--muted); white-space:nowrap; }
  .nav-scroll { flex:1; overflow-y:auto; padding:18px 10px 12px; }
  .nav-label { margin:0 9px 7px; color:var(--muted-strong); font:600 10px Inter,sans-serif; letter-spacing:.08em; text-transform:uppercase; }
  .section-label { margin-top:24px; }
  nav a { min-height:43px; display:flex; align-items:center; gap:10px; position:relative; border-radius:6px; padding:7px 10px; color:var(--muted-strong); text-decoration:none; }
  nav a:hover { background:var(--surface-hover); color:var(--text); }
  nav a.active { background:var(--brand-soft); color:var(--brand-dark); }
  .nav-icon { width:18px; height:18px; display:grid; place-items:center; flex:0 0 18px; color:currentColor; }
  .nav-icon svg { width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
  .nav-copy { display:flex; flex-direction:column; gap:2px; min-width:0; }
  .nav-copy strong { font-size:12px; font-weight:600; }
  .nav-copy small { color:var(--muted); font-size:10px; line-height:1.2; }
  .active .nav-copy small { color:var(--brand-dark); opacity:.72; }
  .active-marker { width:3px; height:20px; position:absolute; left:-10px; border-radius:0 2px 2px 0; background:var(--brand-500); }
  .nav-footer { border-top:1px solid var(--line); padding:12px 10px 14px; }
  .system-state { display:flex; align-items:center; gap:8px; margin:0 5px 13px; color:var(--text); }
  .system-state > span:last-child { display:flex; flex-direction:column; gap:3px; }
  .system-state strong { font-size:10px; font-weight:600; }
  .system-state small { color:var(--muted); font:10px 'IBM Plex Mono',monospace; }
  .state-dot { width:7px; height:7px; flex:0 0 7px; border-radius:50%; background:var(--success); box-shadow:0 0 0 3px var(--success-soft); }
  .account { width:100%; display:flex; align-items:center; gap:8px; min-height:43px; padding:6px; color:var(--text); text-align:left; }
  .avatar { width:28px; height:28px; display:grid; place-items:center; flex:0 0 28px; border-radius:50%; background:#e9edf2; color:#4b5563; font:600 10px 'IBM Plex Mono',monospace; }
  .account-copy { display:flex; flex-direction:column; gap:2px; min-width:0; flex:1; }
  .account-copy strong { font-size:11px; font-weight:600; }
  .account-copy small { color:var(--muted); font-size:10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .scrim { display:none; }
  @media (max-width:760px) { nav { position:fixed; inset:60px auto 0 0; transform:translateX(-100%); transition:transform .18s ease; box-shadow:12px 0 30px rgba(15,23,42,.12); } nav.open { transform:translateX(0); } .scrim { display:block; position:fixed; inset:60px 0 0; z-index:29; border:0; background:rgba(15,23,42,.18); } }
  @media (prefers-reduced-motion:reduce) { nav { transition:none; } }
</style>
