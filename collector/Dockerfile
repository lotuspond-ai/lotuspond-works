FROM golang:1.27.1-alpine3.24 AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go test ./... && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/lotuspond-collector ./cmd/lotuspond-collector && \
    mkdir -p /out/rootfs && mkdir -m 700 /out/rootfs/spool

FROM scratch
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/lotuspond-collector /lotuspond-collector
# An empty owner-only spool, so a volume mounted here starts with the right owner and mode.
# Copied as an entry of a tree, because COPY of a directory would recreate it as 0755.
COPY --from=build --chown=65532:65532 /out/rootfs/ /
USER 65532:65532
EXPOSE 4318
ENTRYPOINT ["/lotuspond-collector"]
