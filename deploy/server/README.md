# Running LotusPond Works at works.lotuspond.ai

This folder runs LotusPond Works on one server, set up the same way as the Health server (`health.lotuspond.ai`). It's an Amazon Lightsail instance in Mumbai (`ap-south-1`) with daily snapshots, a static IP and HTTPS from Caddy. The server pulls `main` every 5 minutes and deploys each new commit by itself, so it needs no GitHub Actions, secrets or inbound SSH key.

The stack is `deploy/compose.yaml` (API with the web UI, PostgreSQL, Redis) plus [`compose.server.yaml`](compose.server.yaml), which adds Caddy in front of the API. Run Compose on the server through [`dc`](dc), which loads both files and `deploy/server/.env`.

| File | What it does |
| --- | --- |
| [`aws-lightsail-cfn.sh`](aws-lightsail-cfn.sh) | Creates the server from AWS CloudShell, as the CloudFormation stack `lotuspond-works-in` ([`aws-lightsail.cfn.yaml`](aws-lightsail.cfn.yaml)) |
| [`aws-lightsail-firstboot.sh`](aws-lightsail-firstboot.sh) | Runs on the server's first boot: installs Docker, clones the repository once its deploy key is added, creates the settings, deploys and turns on automatic deploys |
| [`make-secrets.sh`](make-secrets.sh) | Creates `deploy/server/.env` with fresh database, Redis and MFA secrets |
| [`init-database.sh`](init-database.sh) | Loads `backend/schema.sql`, but only into a brand-new empty database |
| [`remote-deploy.sh`](remote-deploy.sh) | Deploys one commit of `origin/main` and waits for `/api/v1/ready` |
| [`auto-deploy.sh`](auto-deploy.sh), [`systemd/`](systemd/) | The 5-minute timer that deploys new commits on `main` |

**Cost:** the 4 GB RAM, 2 vCPU, 80 GB SSD plan is $24 a month, including the public IPv4 address. Automatic snapshots add a little more. Check the [Lightsail pricing page](https://aws.amazon.com/lightsail/pricing/) for current figures.

**Nothing checks a commit before it goes live.** No lint, typecheck or tests run first. Merge to `main` only through reviewed pull requests that passed the local CI.

## 1. Create the server

Open [AWS CloudShell](https://console.aws.amazon.com/cloudshell/home?region=ap-south-1) (the terminal in the AWS console, already signed in). Get the repository's `deploy/server` folder there. The repository is private, so the simplest way is to upload a zip of `deploy/server` with CloudShell's **Actions → Upload file**, then unzip it. Then run:

```bash
ACME_EMAIL=ops@lotuspond.ai bash deploy/server/aws-lightsail-cfn.sh
```

The script makes a new read-only deploy key and passes it to the stack inside the first-boot script, as a hidden (`NoEcho`) parameter. When the stack is ready (a few minutes), it prints the public key and the server's IPv4 and IPv6 addresses. Running it again only prints the addresses.

## 2. Add the deploy key on GitHub

On [lotuspond-ai/lotuspond-works](https://github.com/lotuspond-ai/lotuspond-works), go to **Settings → Deploy keys → Add deploy key**. Paste the public key and leave **Allow write access** off. Deploy keys are already enabled for the organization, because the Health server uses one.

## 3. DNS

Point `works.lotuspond.ai` at the static IP with an `A` record, plus an `AAAA` record for the IPv6 address. If the domain's DNS is on Cloudflare, start with **DNS only** (grey cloud) so Caddy can get its certificate. You can turn the proxy on afterwards, with SSL/TLS mode **Full (strict)**.

## 4. Wait

The server tries to clone every minute for up to 12 hours. Once it can, it does the rest by itself:

1. Creates `deploy/server/.env` with fresh secrets for `works.lotuspond.ai`, with registration closed and mail off.
2. Deploys `origin/main`. On this first deploy, `init-database.sh` loads `backend/schema.sql` into the new empty database.
3. Turns on the 5-minute deploy timer.

The first build takes about 10 minutes. Follow it in the Lightsail console's browser SSH:

```bash
tail -f /var/log/lotuspond-firstboot.log
```

The server is live when this answers `{"status":"ready",…}`:

```bash
curl https://works.lotuspond.ai/api/v1/ready
```

## 5. Back up the settings

Copy `deploy/server/.env` somewhere safe outside the server, such as a password manager:

```bash
cat /opt/lotuspond-works/deploy/server/.env
```

It holds the database and Redis passwords and `LOTUSPOND_MFA_ENCRYPTION_KEY`. Without that key, nobody can sign in with an authenticator, so never replace it. Lightsail's daily snapshot also holds a copy, but only inside the same AWS account.

## 6. Create the first account

Registration is invitation-only, and the first account has no one to invite it. So open registration just long enough to create it:

1. In `/opt/lotuspond-works/deploy/server/.env`, set `LOTUSPOND_REGISTRATION_ENABLED=true`, then run `deploy/server/dc up -d api`.
2. Sign up at https://works.lotuspond.ai with an individual account.
3. Set it back to `false` and run `deploy/server/dc up -d api` again.

Invite everyone else from inside the product.

**Organizations need identity mail.** In production, an account must verify its mailbox before it can create an organization, and verification is sent by email. Mail stays off until an SMTP service is configured. Add the SMTP settings from [`deploy/example.env`](../example.env) to `.env`, set `LOTUSPOND_MAIL_ENABLED=true`, and start the mail worker with `deploy/server/dc --profile mail up -d`. See the [identity-mail runbook](../../docs-site/docs/identity-mail.md).

## A staging server

A staging server runs the same stack from `main` on its own Lightsail instance, with its own empty database, at a hostname that says nothing about what it is (a random label under `lotuspond.ai`). Anyone with the link can open it; nothing else points to it:

- **Behind the Cloudflare proxy.** Visitors get Cloudflare's wildcard edge certificate, so no certificate for the hostname is issued by a public CA or listed in Certificate Transparency logs, where unlisted hostnames are usually found. The server's address stays hidden.
- **A Cloudflare Origin CA certificate on the server.** [`Caddyfile.origin`](Caddyfile.origin), selected with `CADDYFILE=Caddyfile.origin` in `.env`, serves it from `deploy/server/origin/` (never committed), takes the visitor's address from `CF-Connecting-IP`, and sends `X-Robots-Tag: noindex` and a `robots.txt` that disallows everything.
- **Only Cloudflare reaches ports 80 and 443.** The firewall allows Cloudflare's published ranges ([cloudflare.com/ips](https://www.cloudflare.com/ips/)); port 22 stays open for Lightsail's browser SSH.
- **SSL/TLS Full (strict) for that hostname only,** as a Cloudflare configuration rule, leaving the zone's own setting alone.

An unlisted address is not access control: anyone who has the link can open the site. Keep registration closed, use synthetic data only, and put Cloudflare Access in front of it if it ever needs to be private.

To create one, issue an ECDSA Origin CA certificate for the hostname in Cloudflare (SSL/TLS → Origin Server), put `cert.pem` and `key.pem` in a private folder's `origin/`, and `CADDYFILE=Caddyfile.origin` in its `env.extra`, then:

```bash
STACK=lotuspond-works-<label> NAME=lotuspond-works-<label> DOMAIN=<label>.lotuspond.ai ACME_EMAIL=ops@lotuspond.ai \
  EXTRA_DIR=<private folder> WEB_IPV4=<Cloudflare IPv4 ranges, comma-separated> WEB_IPV6=<Cloudflare IPv6 ranges> \
  bash deploy/server/aws-lightsail-cfn.sh
```

The files travel to the server inside the hidden first-boot parameter. Add the printed deploy key on GitHub (step 2), add the configuration rule, and only then create the DNS records, **proxied** (orange cloud). The firewall and the origin certificate need the proxy, so never switch a staging record to DNS only.

## Day to day

Run these on the server, in `/opt/lotuspond-works`:

| Task | Command |
| --- | --- |
| Follow deploys | `journalctl -u lotuspond-works-auto-deploy -f` |
| Deploy now | `deploy/server/auto-deploy.sh` |
| Deploy one commit | `deploy/server/remote-deploy.sh <sha>` |
| Status and logs | `deploy/server/dc ps`, `deploy/server/dc logs -f api` |
| Pause deploys | `sudo systemctl disable --now lotuspond-works-auto-deploy.timer` |
| Resume deploys | `sudo systemctl enable --now lotuspond-works-auto-deploy.timer` |

The timer remembers the last commit it deployed in `~/.lotuspond-works-deployed`. A commit that fails to deploy is tried only once. The timer then waits for the next commit on `main`, or you can run `remote-deploy.sh` by hand. To roll back, revert on `main`, and the server deploys the revert within a few minutes.

## Releases that change the schema

Works never migrates its database by itself in production ([`deploy/README.md`](../README.md)). Before rebuilding anything, `remote-deploy.sh` compares the `SCHEMA_VERSION` the commit expects with the version the database holds. If they differ, it stops with exit code 4 and the running version keeps serving. Pause the timer, take a snapshot, apply the reviewed migration, then run `deploy/server/remote-deploy.sh <sha>` and resume the timer.

## Not covered yet

- **Off-server backups.** Only Lightsail's daily snapshots, which stay in the same account and region. There's no database dump to S3 like Health's `backup.sh`.
- **Monitoring and alerts.** Nothing checks the site from outside the server.
- **Production execution.** The fleet and runtime workers stay off (`LOTUSPOND_FLEET_ENABLED` unset), as in `deploy/compose.yaml`.
