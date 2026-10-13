# Identity mail

Lotuspond uses email to verify mailbox ownership, recover passwords, and invite organization members. The API queues encrypted messages in PostgreSQL. A separate mail worker submits them to the operator's SMTP server. An accepted API request is not proof of delivery, and SMTP acceptance does not prove inbox placement.

## Configure a connected installation

Mail is disabled by default. Set the following values through the installation's protected runtime configuration:

| Variable                                              | Purpose                                                                         |
| ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| `LOTUSPOND_MAIL_ENABLED`                              | Set to `true` after reviewing the SMTP destination and sender.                  |
| `LOTUSPOND_MAIL_ENCRYPTION_KEY`                       | URL-safe base64 encoding of 32 random bytes; shared by the API and mail worker. |
| `LOTUSPOND_SMTP_HOST`                                 | Operator-controlled SMTP hostname.                                              |
| `LOTUSPOND_SMTP_PORT`                                 | Defaults to `587`.                                                              |
| `LOTUSPOND_SMTP_FROM`                                 | Sender address authorized by the SMTP operator.                                 |
| `LOTUSPOND_SMTP_USERNAME` / `LOTUSPOND_SMTP_PASSWORD` | Configure both when authentication is required.                                 |
| `LOTUSPOND_SMTP_STARTTLS`                             | Keep `true` in production. Certificate validation is required.                  |
| `LOTUSPOND_SMTP_TIMEOUT_SECONDS`                      | Bounds one SMTP socket operation; defaults to `15`.                             |
| `LOTUSPOND_SMTP_CONVERSATION_SECONDS`                 | Bounds the whole SMTP conversation; defaults to `45`, never below the socket timeout. |
| `LOTUSPOND_ORIGIN`                                    | Public HTTPS origin used to generate account links.                             |

Use a secret-management system to generate and retain the encryption key. Do not put it in Git, logs, screenshots, or support tickets. Existing encrypted messages require their original key; coordinate key changes with pending-message expiry and reissuance. Losing the key prevents those pending messages from being sent.

Start the optional worker alongside the API and evaluation worker:

```bash
docker compose --env-file /secure/path/lotuspond.env -f deploy/compose.yaml --profile mail up -d
```

Use `deploy/compose.onprem.yaml` for prebuilt images. Local development uses `npm run worker:mail`. The only plaintext SMTP exception is an independently verified loopback receiver in development or test. Mail configuration is rejected in airgap mode; do not enable the mail profile there.

## Validate delivery

Before admitting users, send verification, recovery, and invitation messages to designated test accounts with their owners' authorization. Check sender authentication and inbox placement with the mail operator. Exercise expired links, repeated links, cancellation, and worker restarts. Use synthetic accounts and a loopback receiver for development tests.

The worker uses expiring leases and bounded retries. A lost SMTP acknowledgement can cause a duplicate email; account links remain single-use. Monitor queued-message age, failed attempts, and worker liveness. The worker has no HTTP listener, so the API image's HTTP healthcheck is disabled for this service.

Only message metadata belongs in operational logs. Never log a link, token, password, message body, SMTP response containing recipient data, or encryption key. A database backup containing pending outbox records must receive the same access controls as identity data; retain the encryption key separately from that backup.

## Account behavior

Verification proves control of the account's current email address. Organization invitations require a signed-in account with the matching verified email address. Creating an invitation does not change membership; accepting it is a separate action.

Password recovery gives the same acknowledgement for existing and unknown accounts. A successful reset invalidates existing sessions and requires a normal sign-in. Recovery requests must never be treated as account-existence lookups.

Design references: [OWASP password recovery guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html) and [Python SMTP TLS behavior](https://docs.python.org/3/library/smtplib.html). Local tests validate the implementation's bounded cases; external delivery remains an installation-specific check.

## Account change notices

A password change, a completed password reset, an authenticator enrolment, an authenticator removal and a recovery-code reissue each queue one `security_notice` message. Unlike verification and invitation mail it carries no link and no code, only the change and the time it was recorded, so it can be delivered late without harm and needs no expiry or single-use check.

These notices go through the same encrypted outbox, worker leases and bounded retries as the rest of the mail. They reference no challenge or invitation, so the worker never cancels one as stale; a repeated delivery is only a repeated warning. If mail is disabled or unconfigured, nothing is queued and the security change still completes — tell your users which of the two situations your installation is in, because an absent notice otherwise looks like an absent change.
