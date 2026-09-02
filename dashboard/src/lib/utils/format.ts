export function formatMetric(value: number | null | undefined, unit = ''): string {
  if (value == null || Number.isNaN(value)) return '—';
  if (['bytes','By','By/s','bytes/s'].includes(unit)) { const i = value ? Math.max(0,Math.min(4, Math.floor(Math.log(Math.abs(value))/Math.log(1024)))) : 0; return `${(value/1024**i).toFixed(i ? 1 : 0)} ${['B','KiB','MiB','GiB','TiB'][i]}${unit.endsWith('/s')?'/s':''}`; }
  if (unit === 'percent' || unit === '%') return `${value.toFixed(1)}%`;
  if (unit === 'seconds') return value < 1 ? `${Math.round(value*1000)} ms` : `${value.toFixed(2)} s`;
  if (unit === '1/s') return `${value.toFixed(value < 10 ? 2 : 1)}/s`;
  if (Math.abs(value) >= 1000) return Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
  return `${Number.isInteger(value) ? value : value.toFixed(2)}${unit && unit !== '1' ? ` ${unit}` : ''}`;
}
export const isStale = (timestamp: string, now = Date.now(), thresholdMs = 45_000) => now - Date.parse(timestamp) > thresholdMs;
export function formatTime(value: string|number|Date, timezone = 'local') { return new Intl.DateTimeFormat('en', { hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false, timeZone: timezone === 'local' ? undefined : timezone }).format(new Date(value)); }
export function formatRange(start: string|number|Date, end: string|number|Date, timezone = 'local') {
  const zone = timezone === 'local' ? undefined : timezone;
  const options: Intl.DateTimeFormatOptions = { month:'short', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false, timeZone:zone };
  const formatter = new Intl.DateTimeFormat('en', options);
  return `${formatter.format(new Date(start))} — ${formatter.format(new Date(end))}`;
}
