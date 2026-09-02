<script lang="ts">
  export let live = true;
  export let timezone = 'local';
  export let rangeMinutes = 1440;
  export let onPreset: (minutes: number) => void;
  export let onLive: () => void;
  export let onTimezone: (value: string) => void = () => {};
</script>

<div class="controls" aria-label="Time range controls">
  <span class="live mono" class:on={live} aria-label={live ? `Live, following the latest ${rangeMinutes} minutes` : 'Paused at a selected historical range'}><i aria-hidden="true"></i>{live ? 'LIVE' : 'PAUSED'}</span>
  <span class="window mono" aria-hidden="true">{rangeMinutes < 60 ? `${rangeMinutes}M` : rangeMinutes < 1440 ? `${Math.round(rangeMinutes / 60)}H` : `${Math.round(rangeMinutes / 1440)}D`} WINDOW</span>
  <div class="presets" role="group" aria-label="Time range presets">
    <button class="button" class:active={rangeMinutes === 15} aria-pressed={rangeMinutes === 15} aria-label="Show the last 15 minutes" on:click={() => onPreset(15)}>15m</button>
    <button class="button" class:active={rangeMinutes === 60} aria-pressed={rangeMinutes === 60} aria-label="Show the last hour" on:click={() => onPreset(60)}>1h</button>
    <button class="button" class:active={rangeMinutes === 360} aria-pressed={rangeMinutes === 360} aria-label="Show the last 6 hours" on:click={() => onPreset(360)}>6h</button>
    <button class="button" class:active={rangeMinutes === 1440} aria-pressed={rangeMinutes === 1440} aria-label="Show the last 24 hours" on:click={() => onPreset(1440)}>24h</button>
    <button class="button" class:active={rangeMinutes === 10080} aria-pressed={rangeMinutes === 10080} aria-label="Show the last 7 days" on:click={() => onPreset(10080)}>7d</button>
  </div>
  {#if !live}<button class="button primary resume" on:click={onLive}>↗ Return to live</button>{/if}
  <label class="tz mono"><span class="sr-only">Timezone</span><select value={timezone} on:change={(e) => onTimezone(e.currentTarget.value)} aria-label="Timezone"><option value="local">LOCAL</option><option value="UTC">UTC</option></select></label>
</div>

<style>
  .controls { display: flex; align-items: center; gap: 5px; overflow: auto; white-space: nowrap; }.live { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 10px; letter-spacing: .08em; margin-right: 2px; }.live i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }.live.on { color: var(--cyan); }.live.on i { background: var(--cyan); box-shadow: 0 0 0 3px var(--cyan-soft), 0 0 10px var(--cyan); }.window { color: #8f8f8f; font-size: 9px; padding-right: 5px; border-right: 1px solid var(--line); }.presets { display: flex; gap: 3px; }.controls .button { min-height: 30px; padding: 4px 8px; border-color: var(--line); border-radius: 5px; color: var(--muted); background: var(--navy); font-size: 10px; }.controls .button:hover { border-color: var(--brand-500); background: var(--navy2); color: var(--text); }.controls .button.active { border-color: var(--brand-500); color: var(--frost); background: var(--surface-hover); box-shadow:inset 0 -2px 0 var(--brand-500); }.controls .button.primary { border-color: var(--brand-500); color: var(--text-on-brand); background: var(--brand-500); }.resume { margin-left: 2px; }.tz { margin-left: 3px; }.tz select { border: 0; background: transparent; color: var(--muted); font: 10px 'IBM Plex Mono',monospace; padding: 5px 2px; cursor: pointer; }.tz option { background: var(--card); }.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
  @media (max-width: 760px) { .window { display: none; } }
</style>
