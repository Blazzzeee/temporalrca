<script lang="ts">
  import type { Entity } from '$lib/types/api';
  import StatusMark from './StatusMark.svelte';
  export let entities: Entity[] = [];
  export let selected: string | undefined;
  export let onSelect: (e: Entity) => void;

  $: hosts = entities.filter((entity) => entity.kind === 'host');
  $: orphaned = entities.filter((entity) => !entity.host_id && entity.kind !== 'host');
  const children = (hostId: string, kind: Entity['kind']) => entities.filter((entity) => entity.kind === kind && entity.host_id === hostId);
  const serviceProcesses = (serviceId: string) => entities.filter((entity) => entity.kind === 'process' && entity.parent_id === serviceId);
  const icon = (kind: Entity['kind']) => ({ host: '▦', service: '◈', process: '·', dependency: '◇' } as Record<string, string>)[kind] || '·';
  const label = (kind: Entity['kind']) => ({ host: 'Host', service: 'Service', process: 'Process', dependency: 'Dependency' } as Record<string, string>)[kind] || 'Resource';
</script>

<div class="tree" role="tree" aria-label="Resource topology">
  <div class="tree-summary"><span class="eyebrow">RESOURCE MAP</span><span class="mono">{entities.length} tracked</span></div>
  {#each hosts as host (host.id)}
    <button class="node host" class:selected={selected === host.id} role="treeitem" aria-selected={selected === host.id} on:click={() => onSelect(host)}>
      <span class="node-icon" aria-hidden="true">{icon(host.kind)}</span><span class="node-copy"><strong>{host.name}</strong><small>{children(host.id, 'service').length} services · {children(host.id, 'process').length} processes</small></span><StatusMark status={host.health} label="" />
    </button>
    <div class="children" role="group">
      {#each children(host.id, 'service') as service (service.id)}
        <button class="node" class:selected={selected === service.id} role="treeitem" aria-selected={selected === service.id} on:click={() => onSelect(service)}>
          <span class="node-icon" aria-hidden="true">{icon(service.kind)}</span><span class="node-copy"><strong>{service.name}</strong><small>{serviceProcesses(service.id).length || 'No'} processes</small></span><StatusMark status={service.health} label="" />
        </button>
        {#each serviceProcesses(service.id) as process (process.id)}
          <button class="node leaf" class:selected={selected === process.id} role="treeitem" aria-selected={selected === process.id} on:click={() => onSelect(process)}>
            <span class="node-icon" aria-hidden="true">{icon(process.kind)}</span><span class="node-copy"><strong>{process.name}</strong><small>{label(process.kind)}</small></span><StatusMark status={process.health} label="" />
          </button>
        {/each}
      {/each}
      {#each children(host.id, 'process').filter((process) => !process.parent_id) as process (process.id)}
        <button class="node leaf" class:selected={selected === process.id} role="treeitem" aria-selected={selected === process.id} on:click={() => onSelect(process)}>
          <span class="node-icon" aria-hidden="true">{icon(process.kind)}</span><span class="node-copy"><strong>{process.name}</strong><small>{label(process.kind)}</small></span><StatusMark status={process.health} label="" />
        </button>
      {/each}
      {#each children(host.id, 'dependency') as dependency (dependency.id)}
        <button class="node leaf" class:selected={selected === dependency.id} role="treeitem" aria-selected={selected === dependency.id} on:click={() => onSelect(dependency)}>
          <span class="node-icon" aria-hidden="true">{icon(dependency.kind)}</span><span class="node-copy"><strong>{dependency.name}</strong><small>{label(dependency.kind)}</small></span><StatusMark status={dependency.health} label="" />
        </button>
      {/each}
    </div>
  {/each}
  {#each orphaned as entity (entity.id)}
    <button class="node" class:selected={selected === entity.id} role="treeitem" aria-selected={selected === entity.id} on:click={() => onSelect(entity)}>
      <span class="node-icon" aria-hidden="true">{icon(entity.kind)}</span><span class="node-copy"><strong>{entity.name}</strong><small>{label(entity.kind)}</small></span><StatusMark status={entity.health} label="" />
    </button>
  {/each}
</div>

<style>
  .tree { padding: 0 8px 10px; }.tree-summary { display: flex; justify-content: space-between; align-items: center; padding: 13px 8px 9px; border-bottom: 1px solid var(--line); }.tree-summary .mono { color: #8f8f8f; font-size: 9px; }
  .node { width: 100%; min-height: 43px; display: grid; grid-template-columns: 20px minmax(0,1fr) auto; gap: 8px; align-items: center; border: 1px solid transparent; border-radius: 6px; padding: 6px 8px; color: var(--muted-strong); background: transparent; text-align: left; cursor: pointer; }.node:hover { background: var(--surface-hover); color: var(--text); }.node.selected { border-color: rgba(255,255,255,.42); background: var(--brand-soft); color: var(--text); box-shadow: inset 3px 0 var(--brand-500); }.node.host { margin-top: 8px; min-height: 50px; background: rgba(32,32,32,.58); }.node.host.selected { background: var(--brand-soft); }.node.leaf { min-height: 37px; padding-left: 28px; }
  .children { margin-left: 10px; padding-left: 10px; border-left: 1px solid #27313a; }.node-icon { color: var(--cyan); font: 15px 'IBM Plex Mono',monospace; text-align: center; }.node.host .node-icon { color: var(--brand-300); }.node-copy { min-width: 0; display: flex; flex-direction: column; gap: 3px; }.node-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; font-weight: 600; }.node-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #748394; font: 9px 'IBM Plex Mono',monospace; }.node :global(.status) { justify-self: end; }.node :global(.status-glyph) { width: 14px; height: 14px; font-size: 9px; }
</style>
