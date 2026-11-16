#!/bin/sh
# Install (or update) the pull-based deploy timer. The first-boot script runs it; run it again, as root, after changing
# deploy/server/systemd/:   sudo /opt/lotuspond-works/deploy/server/install-auto-deploy.sh
# Pause automatic deploys:  sudo systemctl disable --now lotuspond-works-auto-deploy.timer
set -eu
here=$(cd "$(dirname "$0")" && pwd)
install -m 644 "$here/systemd/lotuspond-works-auto-deploy.service" /etc/systemd/system/
install -m 644 "$here/systemd/lotuspond-works-auto-deploy.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lotuspond-works-auto-deploy.timer
systemctl list-timers lotuspond-works-auto-deploy.timer --no-pager
echo "Automatic deploys are on. Log: journalctl -u lotuspond-works-auto-deploy -f"
