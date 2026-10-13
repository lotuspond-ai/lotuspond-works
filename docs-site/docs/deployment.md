# Deployment modes

## Connected single-host Compose

Copy `deploy/example.env` outside the repository, replace every placeholder, restrict file permissions, and run:

```bash
docker compose --env-file /secure/path/lotuspond.env -f deploy/compose.yaml config --quiet
docker compose --env-file /secure/path/lotuspond.env -f deploy/compose.yaml up -d --build
```

Terminate TLS at a reviewed reverse proxy or load balancer and set `LOTUSPOND_ORIGIN` to its public HTTPS origin. The Compose file is a portable single-host baseline. It is not evidence for high availability, backup recovery, or production capacity.

The image never initializes the database. Before the first startup, a database administrator must review the release-matched `backend/schema.sql` and execute it only against a newly created, independently verified empty database. Existing databases require a separately reviewed, data-preserving migration plan. `/api/v1/ready` stays unavailable until the expected schema version is present. Compose starts a separate `python -m lotuspond.worker` process so queued semantic evaluations can run; configure TypeSafe Jev variables in the protected runtime environment or leave the URL and key empty to produce explicit unknown semantic results.

## On-premises images

For connected identity verification, recovery, and invitations, configure [identity mail](identity-mail.md) and start Compose with `--profile mail`. Its delivery worker is separate from the evaluation worker. Mail remains disabled by default.

Build and scan versioned application images in a connected environment, publish them to the organization's registry, then set `LOTUSPOND_API_IMAGE` and `LOTUSPOND_COLLECTOR_IMAGE`. Use the same Compose configuration without the local build overlay:

```bash
docker compose --env-file /secure/path/lotuspond.env -f deploy/compose.onprem.yaml up -d
```

Pin image digests in the reviewed environment file for controlled releases.

## Backup and restore validation

Before admitting production data, run the local synthetic restore drill from the repository:

```bash
python3 deploy/checks/restore-smoke.py --image postgres:18.6-alpine3.24
```

It creates only randomly named, network-disabled disposable containers and refuses image pulls. It applies the reviewed empty-database schema, inserts synthetic fixtures, performs a custom-format backup and restore, and compares all public rows, complete sequence state, and a normalized schema-only dump. It fails unless labeled-container cleanup is confirmed. A passing result validates this bounded local drill; it does not validate production credentials, storage, retention, scheduling, recovery objectives, or operator procedures.

## Air-gapped bundle

In a connected build environment, run `deploy/airgap/build-bundle.sh <version> <output.tar.gz>`. Transfer both the archive and its `.sha256` file through the approved media process. Inside the isolated environment, run `deploy/airgap/verify-bundle.sh <archive> <archive.sha256>`, inspect the included manifest, load `images.tar`, and start the included Compose file.

Verification requires Python 3 and the matching shell entrypoint/Python helper from the reviewed source release. It inspects the builder's POSIX USTAR archive without extraction, checks complete manifest coverage, and bounds compressed data, file payload and decompressed archive overhead. Maximum compressed data and total file payload are each 10 GiB. Links, special files, extended metadata and unsafe paths are rejected.

The checksum detects transfer corruption only when the checksum itself arrives through a trusted independent channel. The bundle scripts do not sign artifacts, establish publisher identity, scan images, or mutate cloud resources.

The offline builder records immutable image IDs in `images.json` and the generated environment example. Use `--local-images` to package already reviewed images without building or pulling. The on-prem Compose file requires all four image variables. A separately trusted `deploy/checks/airgap-smoke.py <archive> <trusted-checksum>` installs the bundle into five disposable containers on an internal Docker network, publishes no ports, verifies synthetic API/evaluation/incident/collector flows, and checks removal of containers, anonymous volumes, and the network. It requires Python 3.11 or later and Docker. The collector uses a loopback sidecar connection for this drill; your HTTPS termination, clean-host transfer, and production organization provisioning still need separate validation.

Optional incident webhooks use the `alerts` Compose profile and [operator-owned destinations](incidents.md). Keep mail and alerts disabled in airgap mode.

The bundle also carries `OPERATIONS.md` and `monitoring/` examples. Configure the optional distinct collector metrics token and internal scraping through the [operations runbook](operations.md). Add `--failure-recovery` to the installation drill to test a stopped evaluation worker, a growing queue, the real stale-observation threshold, worker recovery, and a Redis outage/recovery using only the drill's disposable resources.

Customer-side [evaluation runners](runners.md), protocol adapters, the voice bridge and Gym runtime now have a separate execution image and offline archive. The service image and service bundle still do not include those runtimes. HTTP, MCP and voice commands in the execution image need no repository bind, Docker socket or Python runtime. Gym stays on a host-side kit with its own Docker image and documented host Python/Node/Docker prerequisites. Clean-host transfer, live providers and production operation remain separate delivery work.
