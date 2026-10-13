# Runtime and dependency verification

Checked 2026-09-21. Exact project dependency resolutions are in the npm, uv and Cargo lockfiles. A newer major version and a known vulnerability are different findings.

## Python service

Official PyPI metadata matched all direct production resolutions at verification: FastAPI 0.141.1, Uvicorn 0.53.0, SQLAlchemy 2.0.54, psycopg 3.3.6, Pydantic 2.13.5, pydantic-settings 2.15.0, Argon2 25.1.0, HTTPX 0.28.1, HTTPX2 2.13.0, Redis client 8.1.0, email-validator 2.3.0, and TypeSafe SDK 0.7.0. Pydantic's exact pydantic-core dependency must not be upgraded independently.

The local interpreter is Python 3.14.3; the application image pins a newer 3.14 patch. Python 3.14 is on its supported bugfix branch according to the [Python version status](https://devguide.python.org/versions/). The package minimum is Python 3.13.

`uv export --frozen --no-dev --no-emit-project` supplied the exact marker-aware production resolution to [PyPA pip-audit](https://github.com/pypa/pip-audit). It reported zero known vulnerabilities across 42 packages. The local project itself is not a published PyPI package and is not independently audited by a registry advisory lookup.

## Node and frontend

React and React DOM resolve to 19.3.0; the isolated voice bridge pins ws 8.21.3. Root npm and the voice package returned zero advisories. TypeScript 7.0.2, Vite 8.3.0, tsx 4.23.15 and Node 24 type definitions 24.13.6 passed lint and the production web build. Vite client types were explicitly included for TypeScript 7 CSS side-effect imports; no strict check was disabled. The latest root dependency installation reported zero npm advisories. See the [official Vite 8 migration guide](https://vite.dev/guide/migration).

The build image, `.node-version` and CI use Node 24.21.0, the current LTS patch. The local host's Node 22.22.0 remains supported and is the compatibility-test runtime; it has not been changed globally. The web image was built under Node 24.21.0 and smoke-tested locally. See the [official Node lifecycle](https://nodejs.org/en/about/previous-releases) and [Vite support policy](https://vite.dev/releases/).

The isolated MCP adapter pins official `@modelcontextprotocol/client` 2.0.0 with its own lockfile and zero reported npm production advisories. Its Streamable HTTP tests use only local mock endpoints. Rust is pinned to 1.98.1 and passed its three tests using that exact toolchain; Go is pinned to 1.27.1 in the collector build. Runtime research and image provenance are recorded in `deployment-and-pilots.md`.

## Delivery tooling

GitHub Actions versions were checked against their official repositories: [checkout](https://github.com/actions/checkout), [setup-node](https://github.com/actions/setup-node), [setup-python](https://github.com/actions/setup-python), [setup-go](https://github.com/actions/setup-go), and [setup-uv](https://github.com/astral-sh/setup-uv). Pages artifact and deployment steps follow [upload-pages-artifact](https://github.com/actions/upload-pages-artifact) and [deploy-pages](https://github.com/actions/deploy-pages).

Go/Rust/container/Gym provenance and the pinned sandbox constraints are recorded in `deployment-and-pilots.md` and the corresponding runtime directories. Container tags and package locks do not by themselves establish vulnerability freedom, image reproducibility, or a validated airgap deployment.
