#!/bin/bash
# First-boot script for the LotusPond Works Lightsail server (Ubuntu 24.04), used by aws-lightsail-cfn.sh.
# This file is a template: aws-lightsail-cfn.sh fills in the deploy key, the ACME email and the domain (the
# placeholders below) and passes the result to CloudFormation as a hidden parameter. Never commit a filled-in copy:
# it holds the private key.
# It installs Docker, adds swap, installs the read-only GitHub deploy key and keeps retrying the clone until the key is
# added on GitHub (every minute, up to 12 hours). Then it creates deploy/server/.env with fresh secrets, deploys
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
install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/.ssh
umask 077
cat > /home/ubuntu/.ssh/lotuspond_deploy <<'KEY'
__DEPLOY_KEY__
KEY
umask 022
chown ubuntu:ubuntu /home/ubuntu/.ssh/lotuspond_deploy
install -d -o ubuntu -g ubuntu /opt/lotuspond-works

cat > /usr/local/sbin/lotuspond-bootstrap <<'BOOT'
#!/bin/bash
set -eu
REPO=/opt/lotuspond-works
SSHCMD="ssh -i /home/ubuntu/.ssh/lotuspond_deploy -o StrictHostKeyChecking=accept-new"
for i in $(seq 1 720); do
  [ -d $REPO/.git ] && break
  if sudo -u ubuntu env GIT_SSH_COMMAND="$SSHCMD" git clone git@github.com:lotuspond-ai/lotuspond-works.git $REPO; then break; fi
  echo "$(date -u +%FT%TZ) clone failed: add the deploy key on GitHub; retrying in 60 s"; sleep 60
done
[ -d $REPO/.git ] || { echo "gave up cloning"; exit 1; }
sudo -u ubuntu git -C $REPO config core.sshCommand "ssh -i /home/ubuntu/.ssh/lotuspond_deploy"
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
