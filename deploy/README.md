# Deployment artifacts

These files provide a portable single-host baseline for the Python API, its static React UI, the optional identity-mail worker, PostgreSQL, and Redis.

- `compose.yaml` builds application images from this checkout.
- `compose.onprem.yaml` consumes prebuilt, preferably digest-pinned images.
- `airgap/` creates and verifies an offline image bundle for the API, UI, PostgreSQL and Redis.
- `execution/` creates a separate offline archive for the Node runner image.

The API port binds to loopback by default. Put a reviewed HTTPS reverse proxy in front of the API, keep PostgreSQL and Redis unexposed, store configuration outside the repository, and restrict its permissions. Run `docker compose ... config --quiet` before startup; it detects missing required variables but does not prove credentials, TLS, migrations, backups, capacity, or external integration compatibility.

Both Compose variants forward `LOTUSPOND_REQUIRE_MFA` to the API and `LOTUSPOND_SMTP_CONVERSATION_SECONDS` to the API and mail worker. Set them in the private installation env file; the defaults are `false` and `45`. Installation-wide MFA requires the separately supplied MFA encryption key and fails startup without it. Production operator MFA remains mandatory regardless of this setting. The whole SMTP bound must be at least the socket timeout and at most 120 seconds. Background workers do not receive the MFA key.

The API image does not initialize or migrate a database. Before first startup, a database administrator must review and execute the version-matched `backend/schema.sql` only against a newly created, independently verified empty database. Applying schema or migration SQL to an existing database requires a separate data-preserving migration review and authorization. Readiness remains unavailable until the expected schema version exists. The mail worker uses the API image with `python -m lotuspond.mail_worker`; its inherited HTTP healthcheck is disabled because it does not listen on a port.

These artifacts have no cloud side effects. They do not provision DNS, certificates, hosts, databases, registries, backups, or monitoring.

## Operational visibility

Administration → Operations shows the identity-mail queue and its Redis-backed worker poll observation. Read the [operations runbook](../docs-site/docs/operations.md) before configuring credentials and notification delivery.

## Identity mail

The API and optional `mail-worker` share the mail settings in `example.env`. Keep mail disabled until an authorized SMTP service and an encryption key are configured. Start with `--profile mail` to run the delivery worker. The API can enqueue messages without a worker, so monitor queue age and worker liveness separately. See [the identity-mail runbook](../docs-site/docs/identity-mail.md) for configuration, delivery checks, and key handling. Mail is unavailable in airgap mode.

Production platform operators also require authenticator MFA. Supply a distinct stable `LOTUSPOND_MFA_ENCRYPTION_KEY` to the API through the secret mechanism; workers do not receive it. Back it up separately, coordinate it across API instances, and test restoring the database and key together. Changing the key without re-encrypting stored factors will lock out authenticator sign-in. See the [MFA runbook](../docs-site/docs/mfa.md) for enrollment, recovery codes and limitations.

## Synthetic restore drill

`deploy/checks/restore-smoke.py` exercises `pg_dump` and `pg_restore` only against two newly created, randomly named, network-disabled PostgreSQL containers. It refuses image pulls, resolves the already-local image to an immutable id, loads `backend/schema.sql`, and inserts fixed synthetic records containing no customer data or usable credential, including verification/recovery metadata, invitations, an opaque outbox fixture, creator-attributed API keys, and the incident history and alert outbox record that ADR-0013 retired but kept. It compares every public-table row, complete sequence metadata and state, and a normalized schema-only dump that covers types, varchar lengths, defaults, indexes, constraints, ownership relationships, and sequence definitions. PostgreSQL's random dump safety token is the only normalized field.

Run it on a workstation with the reviewed image already present:

```bash
python3 deploy/checks/restore-smoke.py --image postgres:18.6-alpine3.24
```

The script verifies its labels before removal and then lists containers again to confirm cleanup. If cleanup cannot be confirmed, the command fails; when the drill already failed, the cleanup failure is attached to the original exception rather than replacing it. A local run on 2026-09-21 passed with 38 tables, a 129,613-byte custom backup, network `none`, and confirmed cleanup using image id `sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`. This is local restore evidence for the synthetic schema and fixture only, not a production backup or disaster-recovery test.

## Offline installation drill

`airgap/build-bundle.sh` records exact image IDs in `images.json` and the generated environment example. Its `--local-images` mode performs no build or pull. The separately trusted `checks/airgap-smoke.py` verifies a supplied bundle and exercises its API, PostgreSQL and Redis on an internal-only disposable Docker network. Read [the installation procedure](airgap/INSTALL.md) for requirements, cleanup behavior, and the limits of this local proof.

`execution/build-bundle.sh` records the exact execution image ID. `checks/execution-smoke.py` verifies that archive and runs HTTP, MCP and A2A cases from the extracted Node image. Read [the execution installation procedure](execution/INSTALL.md). These drills do not prove clean-host transfer or live provider behavior.
