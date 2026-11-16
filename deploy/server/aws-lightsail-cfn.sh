#!/bin/bash
# Create the LotusPond Works server on Amazon Lightsail in Mumbai as a CloudFormation stack, from AWS CloudShell (or
# any shell with the AWS CLI, ssh-keygen and python3). The stack (aws-lightsail.cfn.yaml) holds the instance
# (Ubuntu 24.04, 4 GB / 2 vCPU, $24/month), daily automatic snapshots, a static IP and the firewall rules.
#
# The script makes a new read-only deploy key, fills it into aws-lightsail-firstboot.sh and passes that to the stack as
# a hidden parameter, then prints the public key to add on GitHub and the addresses for DNS. Once the key is added, the
# server clones the repository, creates deploy/server/.env with fresh secrets, loads the schema into its new empty
# database, starts the app and turns on automatic deploys by itself.
#   ACME_EMAIL=ops@lotuspond.ai bash deploy/server/aws-lightsail-cfn.sh
# See deploy/server/README.md. The private key exists only on the server and in the stack's hidden parameter.
set -euo pipefail

REGION=${REGION:-ap-south-1}
STACK=${STACK:-lotuspond-works-in}
DOMAIN=${DOMAIN:-works.lotuspond.ai}
ACME_EMAIL=${ACME_EMAIL:?Set ACME_EMAIL (Let\'s Encrypt certificate notices)}
HERE=$(cd "$(dirname "$0")" && pwd)
export AWS_DEFAULT_REGION=$REGION

if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  echo "Stack $STACK already exists; showing its outputs."
else
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
  chmod 700 "$WORK"
  ssh-keygen -q -t ed25519 -N "" -C lotuspond-works-server -f "$WORK/deploy_key"
  python3 - "$HERE/aws-lightsail-firstboot.sh" "$WORK/deploy_key" "$ACME_EMAIL" "$DOMAIN" "$WORK/params.json" <<'PY'
import json, os, sys
script, key, email, domain, out = sys.argv[1:]
body = (open(script).read().replace("__DEPLOY_KEY__", open(key).read().strip())
        .replace("__ACME_EMAIL__", email).replace("__DOMAIN__", domain))
os.umask(0o077)
json.dump([{"ParameterKey": "UserData", "ParameterValue": body}], open(out, "w"))
PY
  aws cloudformation create-stack --stack-name "$STACK" --template-body "file://$HERE/aws-lightsail.cfn.yaml" \
    --parameters "file://$WORK/params.json" --tags Key=project,Value=lotuspond-works >/dev/null
  echo "Creating stack $STACK…"
  aws cloudformation wait stack-create-complete --stack-name "$STACK"
  echo
  echo "Add this as a read-only deploy key on lotuspond-ai/lotuspond-works (Settings → Deploy keys, write access off):"
  cat "$WORK/deploy_key.pub"
fi

echo
aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text
echo "Point $DOMAIN at these (A and AAAA, DNS only), then see deploy/server/README.md."
