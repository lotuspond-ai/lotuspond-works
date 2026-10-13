# Install from source

## Prerequisites

- Python 3.13 or newer and uv; the container build pins Python 3.14.
- Node.js 24 LTS for the React/Vite build and execution clients; `.node-version` pins the reviewed version. Node 22.12+ remains the compatibility floor.
- PostgreSQL tools for local development; Redis is required by production configuration.
- Docker Engine with Compose for the portable deployment and application sandboxes.

The primary API package is `backend/`, running `lotuspond.app:app` on port 8000. It serves `dist/` and the same-origin API.

## Local application

From the repository root, use only the independently configured local development database:

```sh
npm ci
uv sync --frozen --project backend
npm run db:start
npm run db:init
npm run build
LOTUSPOND_REGISTRATION_ENABLED=true npm start
```

Open http://127.0.0.1:8000. The database starts empty and public signup creates no platform administrator. `db:init` refuses nonempty schemas. It never imports data or resets another project. The exact development database is `lotuspond_platform_dev` on loopback port 55439.

Start `npm run worker` in another terminal for queued semantic assessments. Missing provider configuration produces unknown outcomes. Environment examples are in `.env.example`; keep real environment files out of Git.

For separate development servers, use `LOTUSPOND_REGISTRATION_ENABLED=true LOTUSPOND_ORIGIN=http://127.0.0.1:3001 npm run dev` and `npm run dev:web`. The browser origin and configured origin must match.

## Existing local schema

Read `docs/DATABASE.md` before `npm run db:migrate`. Reviewed additive upgrades from local versions 2, 3, or 4 add the usage ledger, widen its numeric fields where needed, and add identity challenges, invitations, encrypted mail outbox records, and API-key creator attribution. No user record is dropped, renamed, or imported. Hosted upgrades require a separately reviewed operator plan.

## Additional runtimes

Install the live transcription bridge with `npm --prefix voice-bridge ci --ignore-scripts`; see [voice evidence](voice.md). See [application sandboxes](sandboxes.md) for pinned Gym Anything images and isolated task grants. Packaged HTTP, MCP, voice and release-gate commands are in the separate execution image; Gym remains a host-side kit. Node runtime clients, Go ingestion, and Rust artifact verification are separate tools with their own lockfiles and checks.

A source installation or a container build does not establish hosted readiness. See [deployment](deployment.md) for the current packaging and remaining operator validation.
