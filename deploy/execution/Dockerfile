FROM node:24.21.0-alpine
WORKDIR /app
RUN addgroup -S -g 10001 lotuspond && \
    adduser -S -D -H -u 10001 -G lotuspond -s /sbin/nologin lotuspond

COPY protocol-adapters/package.json protocol-adapters/package-lock.json /app/protocol-adapters/
COPY voice-bridge/package.json voice-bridge/package-lock.json /app/voice-bridge/
RUN cd /app/protocol-adapters && npm ci --omit=dev --ignore-scripts && \
    cd /app/voice-bridge && npm ci --omit=dev --ignore-scripts && \
    npm cache clean --force

COPY scripts/api-url.mjs scripts/direct-transport.mjs scripts/client.mjs \
     scripts/cli.mjs scripts/runner.mjs scripts/runner-execution.mjs \
     scripts/runner-execution-child.mjs scripts/runner-evidence.mjs \
     scripts/execution-cli.mjs /app/scripts/
COPY protocol-adapters/src /app/protocol-adapters/src
COPY voice-bridge/src /app/voice-bridge/src

RUN chown -R lotuspond:lotuspond /app
USER lotuspond
ENTRYPOINT ["node", "/app/scripts/execution-cli.mjs"]
CMD ["help"]
