#!/bin/bash
# First-boot script for the LotusPond Works Lightsail server (Ubuntu 24.04), used by aws-lightsail-cfn.sh.
# This file is a template: aws-lightsail-cfn.sh fills in the ACME email, the domain and any extra files (the
# placeholders below) and passes the result to CloudFormation as a hidden parameter.
# It installs Docker, adds swap and clones the public repository over HTTPS, retrying every minute (up to 12 hours)
# while GitHub cannot be reached. Then it creates deploy/server/.env with fresh secrets, deploys
# origin/main (which loads backend/schema.sql into the new empty database) and turns on automatic deploys.
# Log on the server: /var/log/lotuspond-firstboot.log
set -eu
exec >>/var/log/lotuspond-firstboot.log 2>&1
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 docker-buildx git openssl unattended-upgrades
systemctl enable --now docker
usermod -aG docker ubuntu
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
install -d -o ubuntu -g ubuntu /opt/lotuspond-works
# Optional server-specific files from aws-lightsail-cfn.sh (EXTRA_DIR), such as a staging server's Cloudflare origin
# certificate (origin/cert.pem, origin/key.pem) and env.extra, lines added to deploy/server/.env. Empty otherwise.
umask 077
echo '__EXTRA_B64__' | base64 -d > /root/lotuspond-extra.tar
umask 022

cat > /usr/local/sbin/lotuspond-bootstrap <<'BOOT'
#!/bin/bash
set -eu
REPO=/opt/lotuspond-works
for i in $(seq 1 720); do
  [ -d $REPO/.git ] && break
  if sudo -u ubuntu git clone https://github.com/lotuspond-ai/lotuspond-works.git $REPO; then break; fi
  echo "$(date -u +%FT%TZ) clone failed; retrying in 60 s"; sleep 60
done
[ -d $REPO/.git ] || { echo "gave up cloning"; exit 1; }
if [ -s /root/lotuspond-extra.tar ]; then
  tar -xf /root/lotuspond-extra.tar -C $REPO/deploy/server --no-same-owner
  chown -R ubuntu:ubuntu $REPO/deploy/server/origin 2>/dev/null || true
fi
if [ ! -f $REPO/deploy/server/.env ]; then
  sudo -u ubuntu env DOMAIN=__DOMAIN__ ACME_EMAIL=__ACME_EMAIL__ $REPO/deploy/server/make-secrets.sh
  [ -f $REPO/deploy/server/env.extra ] && cat $REPO/deploy/server/env.extra >> $REPO/deploy/server/.env && rm $REPO/deploy/server/env.extra
fi
rm -f /root/lotuspond-extra.tar
# The first deploy goes through auto-deploy.sh so it is recorded; the timer then deploys each new commit on main.
sudo -u ubuntu -H $REPO/deploy/server/auto-deploy.sh || echo "$(date -u +%FT%TZ) first deploy failed; see the log above"
$REPO/deploy/server/install-auto-deploy.sh
echo "$(date -u +%FT%TZ) LotusPond Works is running; automatic deploys are on"
BOOT
chmod 755 /usr/local/sbin/lotuspond-bootstrap
systemd-run --unit=lotuspond-bootstrap --property=StandardOutput=append:/var/log/lotuspond-firstboot.log \
  --property=StandardError=append:/var/log/lotuspond-firstboot.log /usr/local/sbin/lotuspond-bootstrap
echo "first boot done; bootstrap running as lotuspond-bootstrap.service"
