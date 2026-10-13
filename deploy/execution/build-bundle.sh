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
stage=$(mktemp -d "${TMPDIR:-/tmp}/lotuspond-execution.XXXXXX")
trap 'rm -rf "$stage"' EXIT HUP INT TERM
bundle="$stage/lotuspond-execution-$version"
mkdir -p "$bundle/gym-kit/src/lotuspond_sandbox" "$bundle/gym-kit/real-app/upstream" "$bundle/gym-kit/examples"

execution_image=${LOTUSPOND_BUNDLE_EXECUTION_IMAGE:-lotuspond-execution:$version}
gym_image=${LOTUSPOND_BUNDLE_GYM_REAL_APP_IMAGE:-lotuspond-gym-real-app:reviewed}

if [ "${3:-}" != "--local-images" ]; then
  docker build -f "$repo_root/deploy/execution/Dockerfile" -t "$execution_image" "$repo_root"
  docker build --pull=false --tag "$gym_image" "$repo_root/sandbox/real-app"
fi

execution_id=$(docker image inspect --format '{{.Id}}' "$execution_image")
gym_id=$(docker image inspect --format '{{.Id}}' "$gym_image")
docker image save -o "$bundle/images.tar" "$execution_id" "$gym_id"

cp "$repo_root/deploy/execution/INSTALL.md" "$bundle/INSTALL.md"
cp "$repo_root/deploy/execution/runtime.env.example" "$bundle/runtime.env.example"
cp "$repo_root/sandbox/leased-run-adapter.mjs" "$bundle/gym-kit/leased-run-adapter.mjs"
cp "$repo_root/sandbox/examples/deterministic-agent.py" "$bundle/gym-kit/examples/deterministic-agent.py"
cp "$repo_root/sandbox/src/lotuspond_sandbox/"*.py "$bundle/gym-kit/src/lotuspond_sandbox/"
cp "$repo_root/sandbox/real-app/upstream/LICENSE" "$bundle/gym-kit/real-app/upstream/LICENSE"
cp "$repo_root/sandbox/real-app/upstream/PROVENANCE.md" "$bundle/gym-kit/real-app/upstream/PROVENANCE.md"
cp "$repo_root/sandbox/real-app/upstream/task.json" "$bundle/gym-kit/real-app/upstream/task.json"
cp "$repo_root/sandbox/real-app/upstream/verifier.py" "$bundle/gym-kit/real-app/upstream/verifier.py"

python3 - "$bundle" "$execution_id" "$gym_id" "$repo_root/sandbox/real-app/manifest.json" <<'PY'
import json
import re
import sys
from pathlib import Path

bundle = Path(sys.argv[1])
execution_id, gym_id = sys.argv[2], sys.argv[3]
if not all(re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in (execution_id, gym_id)):
    raise SystemExit('Every bundled image must have an immutable SHA-256 image ID')
manifest = json.loads(Path(sys.argv[4]).read_text())
manifest['runtime_image'] = gym_id
(bundle / 'gym-kit/real-app/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
(bundle / 'images.json').write_text(json.dumps({
    'schema': 'lotuspond.offline-execution-images.v1',
    'images': {
        'LOTUSPOND_EXECUTION_IMAGE': execution_id,
        'LOTUSPOND_GYM_REAL_APP_IMAGE': gym_id,
    },
}, indent=2) + '\n')
example = bundle / 'runtime.env.example'
text = example.read_text()
example.write_text(
    f'LOTUSPOND_EXECUTION_IMAGE={execution_id}\nLOTUSPOND_GYM_REAL_APP_IMAGE={gym_id}\n' + text
)
PY

(
  cd "$bundle"
  find . -type f ! -name MANIFEST.sha256 -print | LC_ALL=C sort | while IFS= read -r file; do
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$file"; else shasum -a 256 "$file"; fi
  done > MANIFEST.sha256
)
COPYFILE_DISABLE=1 tar --format=ustar -C "$stage" -czf "$output" "lotuspond-execution-$version"
if command -v sha256sum >/dev/null 2>&1; then sha256sum "$output" | awk '{print $1}' > "$output.sha256"; else shasum -a 256 "$output" | awk '{print $1}' > "$output.sha256"; fi
echo "created $output and $output.sha256"
