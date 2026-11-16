#!/bin/bash
# Deploy one commit of origin/main on this server: check it out, rebuild the API image, restart, and wait for
# /api/v1/ready. auto-deploy.sh runs it for each new commit; you can run it by hand: deploy/server/remote-deploy.sh <sha>
#
# Schema changes are never applied here. If the commit expects a different schema version from the one the database
# holds, the deploy stops before anything is rebuilt, and the running version keeps serving. Apply the reviewed
# migration first, then run this again (README.md, "Releases that change the schema").
#
# Everything runs inside main(), which bash reads in full before running: the checkout below rewrites this file.
main() {
  set -euo pipefail
  local repo=${LOTUSPOND_REPO:-/opt/lotuspond-works}
  local sha=${1:-}
  local dc="$repo/deploy/server/dc"

  if ! [[ $sha =~ ^[0-9a-f]{40}$ ]]; then
    echo "remote-deploy: expected a 40-character commit SHA, got '${sha}'." >&2
    exit 2
  fi

  exec 9>/tmp/lotuspond-works-deploy.lock
  flock 9

  cd "$repo"
  git fetch --quiet origin main
  if ! git merge-base --is-ancestor "$sha" origin/main; then
    echo "remote-deploy: $sha is not on origin/main; refusing." >&2
    exit 3
  fi

  # The schema version the commit expects, against the one the database holds (none on a brand-new server).
  local wanted held
  wanted=$(git show "$sha:backend/lotuspond/schema_version.py" | sed -n 's/^SCHEMA_VERSION = \([0-9][0-9]*\)$/\1/p')
  "$dc" up -d --wait postgres redis
  held=$("$dc" exec -T postgres sh -c 'psql -X -tA -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select max(version) from schema_version"' 2>/dev/null || true)
  if [ -n "$held" ] && [ "$held" != "$wanted" ]; then
    echo "remote-deploy: $sha expects schema version $wanted but the database holds $held; not deploying." >&2
    echo "remote-deploy: apply the reviewed migration, then run deploy/server/remote-deploy.sh $sha." >&2
    exit 4
  fi

  echo "remote-deploy: deploying $sha (was $(git rev-parse --short HEAD 2>/dev/null || echo none))."
  git checkout --quiet --force --detach "$sha"
  [ -n "$held" ] || "$repo/deploy/server/init-database.sh"

  # Build first, so the running API keeps serving during the build.
  "$dc" build api
  "$dc" up -d --remove-orphans
  docker image prune -f >/dev/null

  local id status=unknown
  id=$("$dc" ps -q api)
  for _ in $(seq 1 60); do
    status=$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo unknown)
    if [ "$status" = healthy ]; then
      echo "remote-deploy: $sha is live."
      exit 0
    fi
    sleep 5
  done
  echo "remote-deploy: the API did not become ready (status: $status). Recent log:" >&2
  "$dc" logs --tail 50 api >&2
  exit 1
}
main "$@"
exit
