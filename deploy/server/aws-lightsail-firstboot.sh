#!/bin/bash
# First-boot script for the LotusPond Works Lightsail server (Ubuntu 24.04), used by aws-lightsail-cfn.sh.
# This file is a template: aws-lightsail-cfn.sh fills in the ACME email and the domain (the placeholders below) and
# passes the result to CloudFormation.
# It installs Docker, adds swap and clones the public repository over HTTPS (retrying every minute, up to 12 hours, if
# GitHub cannot be reached). Then it creates deploy/server/.env with fresh secrets, deploys origin/main (which loads
# backend/schema.sql into the new empty database) and turns on automatic deploys.
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
[ -f $REPO/deploy/server/.env ] || sudo -u ubuntu env DOMAIN=__DOMAIN__ ACME_EMAIL=__ACME_EMAIL__ $REPO/deploy/server/make-secrets.sh
# The first deploy goes through auto-deploy.sh so it is recorded; the timer then deploys each new commit on main.
sudo -u ubuntu -H $REPO/deploy/server/auto-deploy.sh || echo "$(date -u +%FT%TZ) first deploy failed; see the log above"
$REPO/deploy/server/install-auto-deploy.sh
echo "$(date -u +%FT%TZ) LotusPond Works is running; automatic deploys are on"
BOOT
chmod 755 /usr/local/sbin/lotuspond-bootstrap
systemd-run --unit=lotuspond-bootstrap --property=StandardOutput=append:/var/log/lotuspond-firstboot.log \
  --property=StandardError=append:/var/log/lotuspond-firstboot.log /usr/local/sbin/lotuspond-bootstrap
echo "first boot done; bootstrap running as lotuspond-bootstrap.service"
