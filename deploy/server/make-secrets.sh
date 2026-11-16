#!/bin/sh
# Create deploy/server/.env with fresh secrets for the server. Runs once; it never overwrites an existing .env.
#   DOMAIN=works.lotuspond.ai ACME_EMAIL=ops@lotuspond.ai deploy/server/make-secrets.sh
# Back the file up somewhere safe outside the server: it holds the database and Redis passwords and the MFA key.
set -eu
cd "$(dirname "$0")"
if [ -f .env ]; then echo "deploy/server/.env already exists; leaving it unchanged."; exit 1; fi
domain=${DOMAIN:-works.lotuspond.ai}
acme=${ACME_EMAIL:?Set ACME_EMAIL (Let\'s Encrypt certificate notices)}
pg=$(openssl rand -hex 24)
redis=$(openssl rand -hex 24)
# Authenticator (MFA) secrets are encrypted with this URL-safe base64 32-byte key. Never replace it: stored
# authenticators could no longer be read.
mfa=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
umask 077
cat > .env <<ENV
# LotusPond Works server settings, created by make-secrets.sh. Keep a copy outside the server.
DOMAIN=$domain
ACME_EMAIL=$acme

POSTGRES_DB=lotuspond
POSTGRES_USER=lotuspond
POSTGRES_PASSWORD=$pg
REDIS_PASSWORD=$redis

LOTUSPOND_DATABASE_URL=postgresql+psycopg://lotuspond:$pg@postgres:5432/lotuspond
LOTUSPOND_REDIS_URL=redis://:$redis@redis:6379/0
LOTUSPOND_ORIGIN=https://$domain
# Invitation-only. Set to true only while creating the first account (see README.md), then back to false.
LOTUSPOND_REGISTRATION_ENABLED=false
LOTUSPOND_MFA_ENCRYPTION_KEY=$mfa

# Identity mail is off until an SMTP service is configured; see deploy/example.env for the settings.
LOTUSPOND_MAIL_ENABLED=false
ENV
echo "Created deploy/server/.env with new secrets for $domain. Back it up somewhere safe."
