#!/bin/sh
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ] || { [ "$#" -eq 3 ] && [ "$3" != "--local-images" ]; }; then
  echo "usage: $0 <version> <output.tar.gz> [--local-images]" >&2
  exit 2
fi

version=$1
output=$2
case "$version" in
  *[!A-Za-z0-9._-]*|'') echo "version may contain only letters, digits, dot, underscore, and dash" >&2; exit 2 ;;
esac
if [ -e "$output" ] || [ -e "$output.sha256" ]; then
  echo "refusing to overwrite existing output" >&2
  exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
stage=$(mktemp -d "${TMPDIR:-/tmp}/lotuspond-airgap.XXXXXX")
trap 'rm -rf "$stage"' EXIT HUP INT TERM
bundle="$stage/lotuspond-airgap-$version"
mkdir -p "$bundle"

api_image=${LOTUSPOND_BUNDLE_API_IMAGE:-lotuspond-api:$version}
collector_image=${LOTUSPOND_BUNDLE_COLLECTOR_IMAGE:-lotuspond-collector:$version}
postgres_image=${LOTUSPOND_BUNDLE_POSTGRES_IMAGE:-postgres:18.6-alpine3.24}
redis_image=${LOTUSPOND_BUNDLE_REDIS_IMAGE:-redis:7.2.16-alpine3.21}

if [ "${3:-}" != "--local-images" ]; then
  docker build -f "$repo_root/deploy/Dockerfile" -t "$api_image" "$repo_root"
  docker build -f "$repo_root/collector/Dockerfile" -t "$collector_image" "$repo_root/collector"
  docker pull "$postgres_image"
  docker pull "$redis_image"
fi
# Resolve mutable input tags exactly once. Save and configure immutable image IDs.
api_id=$(docker image inspect --format '{{.Id}}' "$api_image")
collector_id=$(docker image inspect --format '{{.Id}}' "$collector_image")
postgres_id=$(docker image inspect --format '{{.Id}}' "$postgres_image")
redis_id=$(docker image inspect --format '{{.Id}}' "$redis_image")
docker image save -o "$bundle/images.tar" "$api_id" "$collector_id" "$postgres_id" "$redis_id"

cp "$repo_root/deploy/compose.onprem.yaml" "$bundle/compose.yaml"
cp "$repo_root/deploy/example.env" "$bundle/runtime.env.example"
cp "$repo_root/deploy/airgap/INSTALL.md" "$bundle/INSTALL.md"
cp "$repo_root/docs-site/docs/offline-identity.md" "$bundle/OFFLINE-IDENTITY.md"
cp "$repo_root/docs-site/docs/mfa.md" "$bundle/MFA.md"
cp "$repo_root/docs-site/docs/operations.md" "$bundle/OPERATIONS.md"
mkdir "$bundle/monitoring"
cp "$repo_root/deploy/monitoring/"*.yml "$bundle/monitoring/"
cp "$repo_root/backend/schema.sql" "$bundle/schema.sql"
python3 - "$bundle" "$api_id" "$collector_id" "$postgres_id" "$redis_id" <<'PY'
import json
import re
import sys
from pathlib import Path

bundle = Path(sys.argv[1])
guide = bundle / 'OFFLINE-IDENTITY.md'
guide.write_text(guide.read_text().replace('(deployment.md)', '(INSTALL.md)').replace('(mfa.md)', '(MFA.md)'))
mfa_guide = bundle / 'MFA.md'
mfa_guide.write_text(mfa_guide.read_text().replace('(offline-identity.md)', '(OFFLINE-IDENTITY.md)'))
keys = ('LOTUSPOND_API_IMAGE', 'LOTUSPOND_COLLECTOR_IMAGE', 'POSTGRES_IMAGE', 'REDIS_IMAGE')
images = dict(zip(keys, sys.argv[2:], strict=True))
if not all(re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in images.values()):
    raise SystemExit('Every bundled image must have an immutable SHA-256 image ID')
(bundle / 'images.json').write_text(json.dumps({
    'schema': 'lotuspond.offline-images.v1', 'images': images,
}, indent=2) + '\n')
replacements = {**images, 'LOTUSPOND_NETWORK_MODE': 'airgap'}
path = bundle / 'runtime.env.example'
lines = path.read_text().splitlines()
path.write_text('\n'.join(
    f'{line.split("=", 1)[0]}={replacements[line.split("=", 1)[0]]}'
    if line.split('=', 1)[0] in replacements else line
    for line in lines
) + '\n')
PY

(
  cd "$bundle"
  find . -type f ! -name MANIFEST.sha256 -print | LC_ALL=C sort | while IFS= read -r file; do
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$file"; else shasum -a 256 "$file"; fi
  done > MANIFEST.sha256
)
COPYFILE_DISABLE=1 tar --format=ustar -C "$stage" -czf "$output" "lotuspond-airgap-$version"
if command -v sha256sum >/dev/null 2>&1; then sha256sum "$output" | awk '{print $1}' > "$output.sha256"; else shasum -a 256 "$output" | awk '{print $1}' > "$output.sha256"; fi
echo "created $output and $output.sha256"
