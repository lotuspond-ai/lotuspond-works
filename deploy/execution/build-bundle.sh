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
mkdir -p "$bundle"

execution_image=${LOTUSPOND_BUNDLE_EXECUTION_IMAGE:-lotuspond-execution:$version}

if [ "${3:-}" != "--local-images" ]; then
  docker build -f "$repo_root/deploy/execution/Dockerfile" -t "$execution_image" "$repo_root"
fi

execution_id=$(docker image inspect --format '{{.Id}}' "$execution_image")
docker image save -o "$bundle/images.tar" "$execution_id"

cp "$repo_root/deploy/execution/INSTALL.md" "$bundle/INSTALL.md"
cp "$repo_root/deploy/execution/runtime.env.example" "$bundle/runtime.env.example"

python3 - "$bundle" "$execution_id" <<'PY'
import json
import re
import sys
from pathlib import Path

bundle = Path(sys.argv[1])
execution_id = sys.argv[2]
if not re.fullmatch(r'sha256:[0-9a-f]{64}', execution_id):
    raise SystemExit('The bundled image must have an immutable SHA-256 image ID')
(bundle / 'images.json').write_text(json.dumps({
    'schema': 'lotuspond.offline-execution-images.v1',
    'images': {'LOTUSPOND_EXECUTION_IMAGE': execution_id},
}, indent=2) + '\n')
example = bundle / 'runtime.env.example'
text = example.read_text()
example.write_text(f'LOTUSPOND_EXECUTION_IMAGE={execution_id}\n' + text)
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
