# Temporal RCA dashboard

The SvelteKit flight-recorder UI reads the server through `/api/v1`. For local development, run `npm install && npm run dev`. Production uses the included multi-stage Dockerfile and listens on port 3000; the repository Compose stack places Caddy in front of it.

The active range, timezone, filters, selected resource/streams, and live state are URL parameters so every investigation is shareable. Metric charts expose a text summary and support left/right-arrow movement of the shared correlation cursor.

FastAPI contract types are generated with the Docker `codegen` target (`OPENAPI_SCHEMA=http://server:8000/openapi.json npm run generate:api`). The root Compose stack exposes this as `dashboard-codegen`; generated OpenAPI types remain separate from the UI's normalized view models.

The Docker `test` target runs Svelte/TypeScript checks followed by Vitest. The `e2e` target uses the pinned Playwright image so browser tooling is also containerized.
