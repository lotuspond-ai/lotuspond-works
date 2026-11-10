# Deployment

## Single-host Compose

Copy `deploy/example.env` outside the repository, replace every placeholder, restrict file permissions, and run:

```bash
docker compose --env-file /secure/path/lotuspond.env -f deploy/compose.yaml config --quiet
docker compose --env-file /secure/path/lotuspond.env -f deploy/compose.yaml up -d --build
```

Terminate TLS at a reviewed reverse proxy or load balancer and set `LOTUSPOND_ORIGIN` to its public HTTPS origin. The Compose file is a portable single-host baseline. It is not evidence for high availability, backup recovery, or production capacity. For controlled releases, build and scan versioned images, publish them to your registry, and set `LOTUSPOND_API_IMAGE` to a digest-pinned reference.

The image never initializes the database. Before the first startup, a database administrator must review the release-matched `backend/schema.sql` and execute it only against a newly created, independently verified empty database. Existing databases require a separately reviewed, data-preserving migration plan. `/api/v1/ready` stays unavailable until the expected schema version is present.

For identity verification, recovery, and invitations, configure [identity mail](identity-mail.md) and start Compose with `--profile mail`. Its delivery worker is the only background worker in the service Compose file. Mail remains disabled by default; a hosted installation needs an authorized SMTP service, because a mailbox is the only way to verify an identity.

## Backup and restore validation

Before admitting production data, run the local synthetic restore drill from the repository:

```bash
python3 deploy/checks/restore-smoke.py --image postgres:18.6-alpine3.24
```

It creates only randomly named, network-disabled disposable containers and refuses image pulls. It applies the reviewed empty-database schema, inserts synthetic fixtures, performs a custom-format backup and restore, and compares all public rows, complete sequence state, and a normalized schema-only dump. It fails unless labeled-container cleanup is confirmed. A passing result validates this bounded local drill; it does not validate production credentials, storage, retention, scheduling, recovery objectives, or operator procedures.

## Customer-side execution image

[Evaluation runners](runners.md) run in a separate image, `deploy/execution/Dockerfile`, beside your agents. Its `runner-http` and `gate` commands need no repository bind, Docker socket or Python runtime; see `deploy/execution/INSTALL.md`. Live providers and production operation remain separate delivery work.
