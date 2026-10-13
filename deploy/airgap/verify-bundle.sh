#!/bin/sh
set -eu

# Verification host requirement: Python 3 with only the standard library.
# The verifier never extracts the archive and supports bundles up to 10 GiB.
if [ "$#" -ne 2 ]; then
  echo "usage: $0 <bundle.tar.gz> <bundle.sha256>" >&2
  echo "requires Python 3 on the verification host; maximum bundle size is 10 GiB" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "bundle verification requires Python 3 on the verification host" >&2
  exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/../checks/verify_airgap_bundle.py" "$1" "$2"
