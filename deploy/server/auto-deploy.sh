#!/bin/bash
# Pull-based deploys, as on the Health server: a systemd timer runs this every 5 minutes as ubuntu. It fetches
# origin/main (the public repository, over HTTPS) and, when main has a commit not deployed yet, hands it to
# remote-deploy.sh. No GitHub Actions, secrets or inbound SSH key are involved.
# It remembers the last commit deployed (~/.lotuspond-works-deployed). A commit that fails is tried once; the timer
# then waits for the next commit on main. Deploy now: deploy/server/auto-deploy.sh. Log: journalctl -u lotuspond-works-auto-deploy
#
# Everything runs inside main(), which bash reads in full before running: the deploy rewrites this file.
main() {
  set -euo pipefail
  local repo=${LOTUSPOND_REPO:-/opt/lotuspond-works}
  local failed_file="$HOME/.lotuspond-works-deploy-failed"
  local deployed_file="$HOME/.lotuspond-works-deployed"
  local target current

  cd "$repo"
  git fetch --quiet origin main
  target=$(git rev-parse origin/main)
  current=$(cat "$deployed_file" 2>/dev/null || echo none)
  [ "$target" = "$current" ] && exit 0
  if [ -f "$failed_file" ] && [ "$(cat "$failed_file")" = "$target" ]; then
    echo "auto-deploy: $target failed before; waiting for a newer commit on main (or run remote-deploy.sh $target)."
    exit 0
  fi

  echo "auto-deploy: origin/main is $target, last deployed $current."
  if "$repo/deploy/server/remote-deploy.sh" "$target"; then
    echo "$target" > "$deployed_file"
    rm -f "$failed_file"
  else
    echo "$target" > "$failed_file"
    echo "auto-deploy: deploying $target failed; the stack keeps what was running." >&2
    exit 1
  fi
}
main "$@"
exit
