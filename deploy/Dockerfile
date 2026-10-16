# syntax=docker/dockerfile:1
FROM node:24.21.0-alpine AS web
WORKDIR /src
COPY package.json package-lock.json vite.config.ts tsconfig.json ./
COPY web ./web
# The Studio imports this example with a path outside web/. The production bundle
# inlines it; contract tests keep reading the same file in the repository.
COPY contracts/examples/reviewed-action.v1.json ./contracts/examples/reviewed-action.v1.json
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci && npx vite build

FROM python:3.14.7-slim-trixie AS python-build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir uv==0.11.16
COPY sdk/python /app/sdk/python
COPY runtime/python /app/runtime/python
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --frozen --no-dev --no-editable --no-install-project
COPY backend/lotuspond ./lotuspond
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --frozen --no-dev --no-editable && uv pip check

FROM python:3.14.7-slim-trixie
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend
RUN groupadd --system --gid 10001 lotuspond && \
    useradd --system --uid 10001 --gid lotuspond --home-dir /nonexistent --shell /usr/sbin/nologin lotuspond
COPY --from=python-build --chown=lotuspond:lotuspond /app/backend /app/backend
COPY --from=web --chown=lotuspond:lotuspond /src/dist /app/dist
USER lotuspond
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3).read()"]
CMD ["uvicorn", "lotuspond.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
