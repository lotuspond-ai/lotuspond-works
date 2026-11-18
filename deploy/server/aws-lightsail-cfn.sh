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
# A second server, such as staging (README.md, "A staging server"), sets STACK, NAME and DOMAIN, and may set
# DEPLOY_KEY (an existing private key file), WEB_IPV4/WEB_IPV6 (comma-separated ranges for ports 80/443) and
# EXTRA_DIR (files unpacked into deploy/server on the server, such as origin/ and env.extra; they travel in the hidden
# parameter, so keep them small: an ECDSA origin certificate fits, an RSA one may not).
# See deploy/server/README.md. The private key exists only on the server and in the stack's hidden parameter.
set -euo pipefail

REGION=${REGION:-ap-south-1}
STACK=${STACK:-lotuspond-works-in}
DOMAIN=${DOMAIN:-works.lotuspond.ai}
NAME=${NAME:-lotuspond-works-in}
WEB_IPV4=${WEB_IPV4:-0.0.0.0/0}
WEB_IPV6=${WEB_IPV6:-::/0}
ACME_EMAIL=${ACME_EMAIL:?Set ACME_EMAIL (Let\'s Encrypt certificate notices)}
HERE=$(cd "$(dirname "$0")" && pwd)
export AWS_DEFAULT_REGION=$REGION

if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  echo "Stack $STACK already exists; showing its outputs."
else
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
  chmod 700 "$WORK"
  if [ -n "${DEPLOY_KEY:-}" ]; then
    cp "$DEPLOY_KEY" "$WORK/deploy_key" && ssh-keygen -y -f "$WORK/deploy_key" > "$WORK/deploy_key.pub"
  else
    ssh-keygen -q -t ed25519 -N "" -C "$NAME" -f "$WORK/deploy_key"
  fi
  : > "$WORK/extra.tar"
  [ -z "${EXTRA_DIR:-}" ] || tar -cf "$WORK/extra.tar" -C "$EXTRA_DIR" .
  python3 - "$HERE/aws-lightsail-firstboot.sh" "$WORK/deploy_key" "$ACME_EMAIL" "$DOMAIN" "$WORK/extra.tar" \
    "$NAME" "$WEB_IPV4" "$WEB_IPV6" "$WORK/params.json" <<'PY'
import base64, gzip, json, os, sys
script, key, email, domain, extra, name, ipv4, ipv6, out = sys.argv[1:]
# The script goes as written: it holds other scripts (heredocs) whose own #! lines must survive.
body = (open(script).read().replace("__DEPLOY_KEY__", open(key).read().strip()).replace("__ACME_EMAIL__", email)
        .replace("__DOMAIN__", domain).replace("__EXTRA_B64__", base64.b64encode(open(extra, "rb").read()).decode()))
# A CloudFormation parameter holds at most 4096 bytes: the script travels gzipped, unpacked on the server, and split
# across UserData and UserDataMore, which the template joins.
packed = base64.b64encode(gzip.compress(body.encode(), 9, mtime=0)).decode()
body = f"#!/bin/sh\necho {packed} | base64 -d | gunzip > /root/lotuspond-firstboot.sh\nexec bash /root/lotuspond-firstboot.sh\n"
if len(body) > 8192:
    sys.exit(f"The packed first-boot script is {len(body)} bytes; the two parameters hold 8192.")
os.umask(0o077)
json.dump([{"ParameterKey": "UserData", "ParameterValue": body[:4096]},
           {"ParameterKey": "UserDataMore", "ParameterValue": body[4096:]}, {"ParameterKey": "InstanceName", "ParameterValue": name},
           {"ParameterKey": "WebIpv4Cidrs", "ParameterValue": ipv4}, {"ParameterKey": "WebIpv6Cidrs", "ParameterValue": ipv6}],
          open(out, "w"))
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
