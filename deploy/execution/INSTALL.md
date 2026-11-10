# Customer-side execution archive

This archive is separate from the API/UI service bundle. It distributes the Node execution image. It carries no secrets, customer data, API credentials, or proof of production readiness.

The verification host needs Python 3.10 or later. Use the reviewed `deploy/execution/verify-bundle.sh` with its matching `deploy/checks/verify_airgap_bundle.py --kind execution` from the same source release. Verification reads the archive without extracting it and uses the same USTAR, size, and path rules as the service bundle. Deliver the verifier through a trusted channel separately from the archive it verifies.

## Contents

| Path | Role |
| --- | --- |
| `images.tar` / `images.json` | Immutable ID for the Node execution image |
| `runtime.env.example` | Required variable names. Copy it outside this directory and restrict permissions. |
| `INSTALL.md` | This procedure |

The Node image entrypoints are `runner-http`, `runner-mcp`, `runner-a2a`, and `gate`. Those commands do not mount a repository, the Docker socket, or a Python runtime, and they contain no baked credentials.

## Host prerequisites

- Docker Engine able to load the image.
- No additional runtime: run every command in the Node image.

Do not grant the runner access to the Docker socket.

## Install

1. Verify the archive with `verify-bundle.sh` before extraction. Deliver the expected archive digest through a trusted, independent channel.
2. Review `MANIFEST.sha256`, `images.json`, and your organization's image-scan results.
3. Run `docker image load -i images.tar`.
4. Confirm the loaded IDs match `images.json`.
5. Copy `runtime.env.example` to a protected path, replace placeholders, and set permissions to owner read/write only.
6. Run HTTP and MCP adapters with the execution image. `LOTUSPOND_API_URL` may be the API origin or the `/api/v1` base; remote URLs must use HTTPS.

```sh
docker run --rm --network none --env-file /protected/execution.env IMAGE_ID runner-http
```

## Build and rehearse an exact bundle

```sh
deploy/execution/build-bundle.sh reviewed-version /protected/lotuspond-execution-reviewed.tar.gz --local-images
deploy/execution/verify-bundle.sh /protected/lotuspond-execution-reviewed.tar.gz /protected/trusted.sha256
python3 deploy/checks/execution-smoke.py /protected/lotuspond-execution-reviewed.tar.gz /protected/trusted.sha256
```

`--local-images` packages an already reviewed local image without a build. Select it with `LOTUSPOND_BUNDLE_EXECUTION_IMAGE`. The reference is resolved once; the archive uses the resulting immutable ID.

The installation drill copies and verifies the archive in a private temporary directory, loads its image, and runs HTTP and MCP cases against in-image synthetic loopback endpoints. It publishes no ports, uses synthetic data only, and label-checks cleanup. Passing locally does not prove transfer to a clean machine, live provider behavior, or production capacity.
