import { readable } from 'svelte/store';

// One shared cadence keeps every live chart on the same moving right edge.
export const dashboardNow = readable(new Date(), (set) => {
  const timer = setInterval(() => set(new Date()), 15_000);
  return () => clearInterval(timer);
});
