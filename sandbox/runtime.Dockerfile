FROM python:3.12.14-slim-bookworm

ARG GYM_ANYTHING_REVISION
ARG GYM_ANYTHING_CONSTRAINTS_SHA256
LABEL org.opencontainers.image.source="https://github.com/cmu-l3/gym-anything" \
      org.opencontainers.image.revision="${GYM_ANYTHING_REVISION}" \
      dev.lotuspond.gym-anything.constraints-sha256="${GYM_ANYTHING_CONSTRAINTS_SHA256}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/gym-anything
RUN pip install --no-cache-dir uv==0.11.16
COPY . .
RUN printf '%s  constraints-ci.txt\n' "$GYM_ANYTHING_CONSTRAINTS_SHA256" | sha256sum -c - && \
    uv pip install --system --no-cache -c constraints-ci.txt '.[benchmark]' && \
    gym-anything --help >/dev/null

ENTRYPOINT []
CMD ["gym-anything", "doctor", "--json"]
