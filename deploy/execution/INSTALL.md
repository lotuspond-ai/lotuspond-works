# Customer-side execution archive

This archive is separate from the API/UI/collector service bundle. It distributes the Node execution image, the reviewed Gym real-application image, and a host-side Gym kit. It carries no secrets, customer data, API credentials, or proof of production readiness.

The verification host needs Python 3.10 or later. Use the reviewed `deploy/execution/verify-bundle.sh` with its matching `deploy/checks/verify_airgap_bundle.py --kind execution` from the same source release. Verification reads the archive without extracting it and uses the same USTAR, size, and path rules as the service bundle. Deliver the verifier through a trusted channel separately from the archive it verifies.

## Contents

| Path | Role |
| --- | --- |
| `images.tar` / `images.json` | Immutable IDs for the Node execution image and the Gym real-application image |
| `runtime.env.example` | Required variable names. Copy it outside this directory and restrict permissions. |
| `gym-kit/` | Host-side Node adapter, Python standard-library controller, task, verifier, license, and provenance |
| `INSTALL.md` | This procedure |

The Node image entrypoints are `runner-http`, `runner-mcp`, `runner-a2a`, `voice`, and `gate`. Those commands do not mount a repository, the Docker socket, or a Python runtime, and they contain no baked credentials. Gym execution is deliberately not an entrypoint of that image.

## Host prerequisites

- Docker Engine able to load the two images.
- For HTTP, MCP, voice, and release gating: no additional runtime. Run those commands in the Node image.
- For the reviewed Gym xterm task: host Python 3.13 or later and a non-root account that can use Docker. Host Node 24 is required only if you run `gym-kit/leased-run-adapter.mjs` against the Lotuspond API.

Do not grant the general HTTP/MCP/voice runner access to the Docker socket. The Gym kit is the only distributed tool that talks to Docker, and only to launch the pinned application image with the reviewed isolation policy.

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

Voice transcription requires provider credentials and network access to the configured speech endpoint. In airgap mode the command refuses before opening a socket:

```sh
docker run --rm --network none -e LOTUSPOND_NETWORK_MODE=airgap IMAGE_ID \
  voice --session 12345678-1234-4234-8234-123456789abc --channel-map 0:user
```

7. For the genuine xterm task, extract `gym-kit/` to a protected directory. The bundled `real-app/manifest.json` already names the packaged Gym image ID. Then:

```sh
mkdir -p /protected/gym-runs
PYTHONPATH=/protected/gym-kit/src PYTHONDONTWRITEBYTECODE=1 python3 \
  -m lotuspond_sandbox.real_app \
  --bundle /protected/gym-kit/real-app \
  --output-dir /protected/gym-runs
```

The leased API adapter remains `gym-kit/leased-run-adapter.mjs` and still requires a scoped runner key, an explicit loopback agent URL, and a host Node process that does not receive the Docker socket through the general runner image.

## Build and rehearse an exact bundle

```sh
deploy/execution/build-bundle.sh reviewed-version /protected/lotuspond-execution-reviewed.tar.gz --local-images
deploy/execution/verify-bundle.sh /protected/lotuspond-execution-reviewed.tar.gz /protected/trusted.sha256
python3 deploy/checks/execution-smoke.py /protected/lotuspond-execution-reviewed.tar.gz /protected/trusted.sha256
```

`--local-images` packages already reviewed local images without builds. Select them with `LOTUSPOND_BUNDLE_EXECUTION_IMAGE` and `LOTUSPOND_BUNDLE_GYM_REAL_APP_IMAGE`. Each reference is resolved once; the archive and Gym manifest use the resulting immutable IDs.

The installation drill copies and verifies the archive in a private temporary directory, loads its images, runs HTTP and MCP cases against in-image synthetic loopback endpoints, checks voice runtime availability and airgap refusal without provider credentials, and runs the genuine xterm task from the extracted host kit. It publishes no ports, uses synthetic data only, and label-checks cleanup. Passing locally does not prove transfer to a clean machine, live provider behavior, or production capacity.
