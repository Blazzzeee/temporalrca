<script lang="ts">
  import '../app.css';
  import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
  import { page } from '$app/stores';
  import { goto, invalidateAll } from '$app/navigation';
  import { onMount } from 'svelte';
  import TopBar from '$lib/components/TopBar.svelte';
  import SideNav from '$lib/components/SideNav.svelte';
  import { parseDashboardState, rangeForPreset, stateToSearch } from '$lib/state/url';
  import { dashboardNow } from '$lib/state/clock';
  import { subscribeLive } from '$lib/api/client';
  const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 3000, retry: 1 } } });
  let connected = true; let navOpen = false;
  $: state = parseDashboardState($page.url, $dashboardNow);
  $: rangeMinutes = Math.max(1, Math.round((state.end.getTime() - state.start.getTime()) / 60_000));
  function replace(next: typeof state) {
    const params = stateToSearch(next);
    // Time controls are shared by every page, but must not erase the current
    // root tab or an explicitly selected node.
    for (const key of ['view', 'host']) {
      const value = $page.url.searchParams.get(key);
      if (value) params.set(key, value);
    }
    goto(`${$page.url.pathname}?${params}`, { replaceState: true, keepFocus: true, noScroll: true });
  }
  function preset(minutes: number) { const range = rangeForPreset(minutes); replace({ ...state, ...range, live: true }); }
  function returnLive() { const range = rangeForPreset(24 * 60); replace({ ...state, ...range, live: true }); }
  function timezone(value: string) { replace({ ...state, timezone: value }); }
  onMount(() => { const close = subscribeLive(() => invalidateAll(), (value) => connected = value, () => invalidateAll()); return close; });
</script>
<svelte:head><title>Temporal RCA · Operations console</title><meta name="theme-color" content="#080808" /></svelte:head>
<QueryClientProvider client={queryClient}>
  <TopBar live={state.live} timezone={state.timezone} {rangeMinutes} {connected} onPreset={preset} onLive={returnLive} onTimezone={timezone} onMenu={() => navOpen = !navOpen} />
  <div class="shell"><SideNav path={$page.url.pathname + $page.url.search} open={navOpen} onToggle={() => navOpen = false} /><main><slot /></main></div>
</QueryClientProvider>
<style>
  .shell { display:flex; align-items:stretch; } main { min-width:0; flex:1; min-height:calc(100vh - 60px); padding:28px 32px 48px; } @media (max-width:1000px) { main { padding:24px; } } @media (max-width:760px) { main { padding:20px 14px 32px; min-height:calc(100vh - 60px); } }
</style>
