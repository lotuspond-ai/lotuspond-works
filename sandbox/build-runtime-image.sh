#!/bin/sh
set -eu

revision=f3dc04649641dba69d5032f7bb6adaa9785c150d
constraints_sha256=0818490024cacfbaa221f21c428bd06a14b1045e2f70f647a3a01f0cbdf54f79

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <clean-local-gym-anything-checkout> <image-tag>" >&2
  exit 2
fi
checkout=$1
image_tag=$2
if [ ! -d "$checkout/.git" ]; then
  echo "checkout must be a local Git worktree" >&2
  exit 1
fi
actual_revision=$(git -C "$checkout" rev-parse HEAD)
if [ "$actual_revision" != "$revision" ]; then
  echo "checkout does not match the reviewed Gym Anything revision" >&2
  exit 1
fi
if [ -n "$(git -C "$checkout" status --porcelain --untracked-files=all)" ]; then
  echo "checkout must be clean, including untracked files" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual_constraints=$(sha256sum "$checkout/constraints-ci.txt" | awk '{print $1}')
else
  actual_constraints=$(shasum -a 256 "$checkout/constraints-ci.txt" | awk '{print $1}')
fi
if [ "$actual_constraints" != "$constraints_sha256" ]; then
  echo "constraints-ci.txt does not match the reviewed dependency lock" >&2
  exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
stage=$(mktemp -d "${TMPDIR:-/tmp}/lotuspond-gym-build.XXXXXX")
trap 'rm -rf "$stage"' EXIT HUP INT TERM
mkdir -p "$stage/context"
git -C "$checkout" archive --format=tar --output "$stage/source.tar" "$revision"
tar -C "$stage/context" -xf "$stage/source.tar"
if command -v sha256sum >/dev/null 2>&1; then
  archived_constraints=$(sha256sum "$stage/context/constraints-ci.txt" | awk '{print $1}')
else
  archived_constraints=$(shasum -a 256 "$stage/context/constraints-ci.txt" | awk '{print $1}')
fi
if [ "$archived_constraints" != "$constraints_sha256" ]; then
  echo "archived constraints-ci.txt does not match the reviewed dependency lock" >&2
  exit 1
fi
docker build \
  --file "$script_dir/runtime.Dockerfile" \
  --build-arg "GYM_ANYTHING_REVISION=$revision" \
  --build-arg "GYM_ANYTHING_CONSTRAINTS_SHA256=$constraints_sha256" \
  --tag "$image_tag" \
  "$stage/context"
image_id=$(docker image inspect --format '{{.Id}}' "$image_tag")
if ! printf '%s\n' "$image_id" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
  echo "Docker returned an invalid image id" >&2
  exit 1
fi
echo "runtime_image=$image_id"
