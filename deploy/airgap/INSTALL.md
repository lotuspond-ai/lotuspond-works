# Air-gapped installation

The verification host needs Python 3.10 or later. Use the reviewed `deploy/airgap/verify-bundle.sh` with its matching `deploy/checks/verify_airgap_bundle.py` from the same source release. Verification reads the archive without extracting it; it supports the builder's POSIX USTAR format, at most 10 GiB of compressed data and total file payload, and 10,000 entries. It rejects links, special files, extended metadata, unsafe paths and incomplete manifests. Deliver the verifier through a trusted channel separately from the archive it verifies.

1. Verify the archive with `verify-bundle.sh` before extraction. Deliver the expected archive digest through a trusted, independent channel.
2. Review `MANIFEST.sha256`, `compose.yaml`, the `images.json` inventory, and your organization's image-scan results. The generated environment example refers to immutable local image IDs; verify each loaded ID against the inventory.
3. Run `docker image load -i images.tar`.
4. Copy `runtime.env.example` to a protected path outside this directory, replace every placeholder, and set permissions to owner read/write only.
5. Configure an internal HTTPS origin and certificate. Ensure the isolated network blocks egress; `LOTUSPOND_NETWORK_MODE=airgap` is application configuration, not a firewall.
6. Have a database administrator review the included `schema.sql` and execute it only against a newly created, independently verified empty database. This is not an upgrade script. Existing databases require a separately reviewed, data-preserving migration plan and authorization.
7. Run `docker compose --env-file /protected/path/runtime.env -f compose.yaml config --quiet`, then start the services. The API image does not initialize or migrate the database, and readiness remains unavailable until the expected schema exists.
8. Verify API liveness and readiness separately, confirm the evaluation worker remains running, run an isolated backup/restore exercise, and execute the customer-specific acceptance suite before admitting production data.

The bundle contains container images, deployment configuration, and a reviewed empty-database schema snapshot. It carries no secrets, customer data, database snapshot, certificate, signature, SBOM, vulnerability attestation, or proof of production readiness. Customer-side runners and protocol adapters are in the separate execution archive; see `deploy/execution/INSTALL.md`.

Keep `LOTUSPOND_MAIL_ENABLED=false` and do not enable the `mail` Compose profile in airgap mode. Cloud mailbox verification, password recovery, and invitations are unavailable there. The bundled `OFFLINE-IDENTITY.md` describes operator-attested enrollment for new accounts, explicit organizations and optional platform operators. Review the identity-check and trusted-delivery procedures before using the CLI. It never marks a mailbox verified. Existing-account password recovery and identity federation remain separate work.

Before platform-operator onboarding, configure a dedicated stable `LOTUSPOND_MFA_ENCRYPTION_KEY` through the installation's secret mechanism. Operators must enable an authenticator and save recovery codes before using privileged controls. Authenticator verification works offline. Read the bundled `MFA.md`, maintain accurate server/device clocks, and back up the encryption key separately from the database. The bundle never contains a configured key. Replacing the key is not a supported rotation procedure.

Keep `LOTUSPOND_ALERT_WEBHOOKS=[]` and do not enable the `alerts` profile. Incidents and their history remain available in the console; external webhook delivery is disabled.

The bundled `OPERATIONS.md` explains queue state and worker observations. It does not install monitoring or notification services.

## Build and rehearse an exact bundle

The normal builder builds application images and pulls infrastructure images in a connected review environment. To package already reviewed local images without builds or network pulls, pass `--local-images`. Select the source images through `LOTUSPOND_BUNDLE_API_IMAGE`, `LOTUSPOND_BUNDLE_COLLECTOR_IMAGE`, `LOTUSPOND_BUNDLE_POSTGRES_IMAGE`, and `LOTUSPOND_BUNDLE_REDIS_IMAGE`. Each reference is resolved once; the archive and generated configuration use the resulting immutable IDs.

```sh
deploy/airgap/build-bundle.sh reviewed-version /protected/lotuspond-reviewed.tar.gz --local-images
deploy/airgap/verify-bundle.sh /protected/lotuspond-reviewed.tar.gz /protected/trusted.sha256
python3 deploy/checks/airgap-smoke.py /protected/lotuspond-reviewed.tar.gz /protected/trusted.sha256
```

The installation drill requires Python 3.11 or later. It copies and verifies the archive in a private temporary directory, loads its image archive, and creates four disposable containers on a randomly named internal Docker network. It publishes no ports and applies the included schema only to its new synthetic database. It exercises production-mode personal signup, the organization-verification gate, operator-attested organization enrollment, one-use links, normal login, and platform administration while retaining an unverified mailbox. It also checks unavailable versus reported-zero trace costs, deterministic failure versus unavailable semantic evaluation, incident persistence, and authenticated OTLP/HTTP ingestion in JSON and protobuf straight to the API. This does not validate your internal HTTPS proxy or certificate chain.

All containers and the network are label-checked, removed, and checked for absence. Anonymous database/cache volumes are inventoried from the owned containers and separately checked for removal. Loaded image IDs remain in the local Docker cache; the drill does not remove shared images. Run this only with a trusted, reviewed bundle on a Docker test host. Passing locally does not prove transfer to a clean machine, human identity verification, complete upgrade behavior, or production capacity.

Add `--failure-recovery` to the drill command to stop its evaluation worker, observe pending work and a stale poll after the real 150-second threshold, restart and drain the queue, then verify Redis outage/readiness and recovery. Every stop/start checks the disposable container's exact name and ownership label. Allow several minutes; the drill never stops existing installation services.
