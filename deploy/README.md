# Deployment artifacts

These files provide a portable single-host baseline for the Python API, its static React UI, the optional identity-mail worker, PostgreSQL, and Redis, plus a separate image for the customer-side evaluation runner.

- `compose.yaml` runs the service. Build images from this checkout, or set `LOTUSPOND_API_IMAGE` to a reviewed, digest-pinned image.
- `execution/` builds the customer-side image for the HTTP runner and the release gate.

The API port binds to loopback by default. Put a reviewed HTTPS reverse proxy in front of the API, keep PostgreSQL and Redis unexposed, store configuration outside the repository, and restrict its permissions. Run `docker compose ... config --quiet` before startup; it detects missing required variables but does not prove credentials, TLS, migrations, backups, capacity, or external integration compatibility.

Compose forwards `LOTUSPOND_SMTP_CONVERSATION_SECONDS` to the API and mail worker. Set it in the private installation env file; the default is `45`, and the whole SMTP bound must be at least the socket timeout and at most 120 seconds.

The API image does not initialize or migrate a database. Before first startup, a database administrator must review and execute the version-matched `backend/schema.sql` only against a newly created, independently verified empty database. Applying schema or migration SQL to an existing database requires a separate data-preserving migration review and authorization. Readiness remains unavailable until the expected schema version exists. The mail worker uses the API image with `python -m lotuspond.mail_worker`; its inherited HTTP healthcheck is disabled because it does not listen on a port.

These artifacts have no cloud side effects. They do not provision DNS, certificates, hosts, databases, registries, backups, or monitoring.

## The hosted server

[`server/`](server/README.md) runs this stack at `works.lotuspond.ai` on one Amazon Lightsail server in Mumbai, set up like the Health server: Caddy for HTTPS, a first-boot script, and a timer that deploys each new commit on `main`. Its scripts create AWS resources only when you run `server/aws-lightsail-cfn.sh` yourself. On that server, `server/init-database.sh` loads `backend/schema.sql` only into the newly created empty database, and `server/remote-deploy.sh` refuses any release whose schema version differs from the database's.

## Identity mail

The API and optional `mail-worker` share the mail settings in `example.env`. Keep mail disabled until an authorized SMTP service and an encryption key are configured; a hosted installation needs it, because a mailbox is the only way to verify an identity. Start with `--profile mail` to run the delivery worker. The API can enqueue messages without a worker, so watch queue age and worker liveness yourself. See [the identity-mail runbook](../docs-site/docs/identity-mail.md) for configuration, delivery checks, and key handling.

Production platform operators also require authenticator MFA. Supply a distinct stable `LOTUSPOND_MFA_ENCRYPTION_KEY` to the API through the secret mechanism; the mail worker does not receive it. Back it up separately, coordinate it across API instances, and test restoring the database and key together. Changing the key without re-encrypting stored factors will lock out authenticator sign-in. See the [MFA runbook](../docs-site/docs/mfa.md) for enrollment, recovery codes and limitations.

## Synthetic restore drill

`deploy/checks/restore-smoke.py` exercises `pg_dump` and `pg_restore` only against two newly created, randomly named, network-disabled PostgreSQL containers. It refuses image pulls, resolves the already-local image to an immutable id, loads `backend/schema.sql`, and inserts fixed synthetic records containing no customer data or usable credential, including verification/recovery metadata, invitations, an opaque outbox fixture, creator-attributed API keys, and the incident history and alert outbox record that ADR-0013 retired but kept. It compares every public-table row, complete sequence metadata and state, and a normalized schema-only dump that covers types, varchar lengths, defaults, indexes, constraints, ownership relationships, and sequence definitions. PostgreSQL's random dump safety token is the only normalized field.

Run it on a workstation with the reviewed image already present:

```bash
python3 deploy/checks/restore-smoke.py --image postgres:18.6-alpine3.24
```

The script verifies its labels before removal and then lists containers again to confirm cleanup. If cleanup cannot be confirmed, the command fails; when the drill already failed, the cleanup failure is attached to the original exception rather than replacing it. A local run on 2026-09-21 passed with 38 tables, a 129,613-byte custom backup, network `none`, and confirmed cleanup using image id `sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`. This is local restore evidence for the synthetic schema and fixture only, not a production backup or disaster-recovery test.

## Execution image check

`checks/execution-smoke.py --image IMAGE` checks a built execution image: its nonroot user, entrypoint and help, the absence of Python, Docker and a socket, and a synthetic HTTP runner case against an in-image loopback endpoint. Read [the execution image guide](execution/INSTALL.md). This check does not prove live agent behavior.

The offline image bundle, the execution archive and the on-premises Compose variant were retired on 2026-11-11 ([ADR-0014](../docs/architecture/0014-focus-on-the-core-loop.md)).
