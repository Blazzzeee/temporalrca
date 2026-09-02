import type { Config } from 'tailwindcss';
export default {
  content: ['./src/**/*.{html,js,svelte,ts}'],
  theme: { extend: { colors: { recorder: '#080808', navy: '#111820', frost: '#f7f7f8', cloudflare: '#f48120', signal: '#42d4f4', warning: '#f7b955', fault: '#ff6b5f' } } },
  plugins: []
} satisfies Config;
