FROM node:24.21.0-alpine AS web
WORKDIR /src
COPY package.json package-lock.json vite.config.ts tsconfig.json ./
COPY web ./web
RUN npm ci && npx vite build

FROM python:3.14.7-slim-trixie AS python-build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
RUN pip install --no-cache-dir uv==0.11.16
COPY sdk/python /app/sdk/python
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project
COPY backend/lotuspond ./lotuspond
RUN uv sync --frozen --no-dev --no-editable

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
