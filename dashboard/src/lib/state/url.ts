export type DashboardState = { start: Date; end: Date; timezone: string; filters: string; streams: string[]; live: boolean; selected?: string };
export const LIVE_WINDOW_MS = 24 * 60 * 60_000;
export function parseDashboardState(url: URL, now = new Date()): DashboardState {
  const live = url.searchParams.get('live') !== 'false';
  const requestedRange = Number(url.searchParams.get('range'));
  const liveWindowMs = Number.isFinite(requestedRange) && requestedRange > 0
    ? requestedRange * 60_000
    : LIVE_WINDOW_MS;
  const endParam = url.searchParams.get('end');
  const startParam = url.searchParams.get('start');
  const end = live ? now : endParam && !Number.isNaN(Date.parse(endParam)) ? new Date(endParam) : now;
  const start = live
    ? new Date(end.getTime() - liveWindowMs)
    : startParam && !Number.isNaN(Date.parse(startParam)) ? new Date(startParam) : new Date(end.getTime() - LIVE_WINDOW_MS);
  return { start, end, live, timezone: url.searchParams.get('tz') || 'local', filters: url.searchParams.get('filters') || '', streams: (url.searchParams.get('streams') || '').split(',').filter(Boolean), selected: url.searchParams.get('selected') || undefined };
}
export function stateToSearch(state: DashboardState) {
  const p = new URLSearchParams();
  if (state.live) {
    const rangeMinutes = Math.max(1, Math.round((state.end.getTime() - state.start.getTime()) / 60_000));
    if (rangeMinutes !== LIVE_WINDOW_MS / 60_000) p.set('range', String(rangeMinutes));
  } else { p.set('start', state.start.toISOString()); p.set('end', state.end.toISOString()); p.set('live', 'false'); }
  if (state.timezone !== 'local') p.set('tz', state.timezone);
  if (state.filters) p.set('filters', state.filters);
  if (state.streams.length) p.set('streams', state.streams.join(','));
  if (state.selected) p.set('selected', state.selected);
  return p;
}
export function rangeForPreset(minutes: number, now = new Date()) { return { start: new Date(now.getTime() - minutes * 60_000), end: now }; }
