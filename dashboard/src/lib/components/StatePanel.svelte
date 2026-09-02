<script lang="ts">
  export let state: 'loading' | 'empty' | 'error';
  export let title = '';
  export let message = '';
  export let retry: (() => void) | undefined = undefined;
  const copy = {
    loading: { eyebrow: 'Reading telemetry', title: 'Building this view', message: 'Fetching the latest inventory and signal data.' },
    empty: { eyebrow: 'No matching data', title: 'Nothing to show yet', message: 'Try a wider time range or remove a filter.' },
    error: { eyebrow: 'Connection issue', title: 'This view is unavailable', message: 'The telemetry API did not return a usable response.' }
  };
</script>

<section class="state panel" class:state-error={state === 'error'} class:state-empty={state === 'empty'} role={state === 'error' ? 'alert' : 'status'} aria-live="polite">
  <div class="state-icon" aria-hidden="true">
    {#if state === 'loading'}<span class="spinner"></span>{:else if state === 'empty'}<span class="empty-glyph">∅</span>{:else}<span class="error-glyph">!</span>{/if}
  </div>
  <div class="state-copy">
    <span class="eyebrow">{copy[state].eyebrow}</span>
    <h2 class="display">{title || copy[state].title}</h2>
    <p>{message || copy[state].message}</p>
    {#if retry}<button class="button" on:click={retry}>Retry</button>{/if}
  </div>
</section>

<style>
  .state { min-height: 250px; display: grid; grid-template-columns: 42px minmax(0, 420px); gap: 16px; align-items: center; justify-content: center; padding: 32px; }
  .state-icon { width: 42px; height: 42px; display: grid; place-items: center; border: 1px solid var(--line-strong); border-radius: 10px; color: var(--cyan); background: var(--cyan-soft); }
  .state-empty .state-icon { color: var(--amber); border-color: rgba(245,158,11,.32); background: rgba(245,158,11,.08); }
  .state-error .state-icon { color: var(--coral); border-color: rgba(239,68,68,.34); background: rgba(239,68,68,.08); }
  .spinner { width: 17px; height: 17px; border: 2px solid var(--line-strong); border-top-color: var(--cyan); border-radius: 50%; animation: spin .8s linear infinite; }
  .empty-glyph,.error-glyph { font: 600 22px 'IBM Plex Mono',monospace; }.error-glyph { font-size: 20px; }.state-copy { min-width: 0; }
  .state h2 { margin: 7px 0 5px; font-size: 25px; letter-spacing: .02em; }.state p { margin: 0 0 15px; color: var(--muted); font-size: 13px; line-height: 1.55; }
  @keyframes spin { to { transform: rotate(360deg); } } @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
  @media (max-width: 560px) { .state { grid-template-columns: 1fr; text-align: center; justify-items: center; padding: 26px 18px; }.state-copy { max-width: 34ch; } }
</style>
