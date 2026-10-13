"""Prove that a built API image embeds exactly the source trees in this working copy.

The repository has no commit, so provenance here is content-based: the same tree hash
is computed over the source on disk and over the tree actually present inside the
image. It answers "is this image built from this source", not "is this source
reviewed", "is this image published", or anything about a hosted deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Dependency, build, and cache directories are not source. Excluding them keeps the
# source hash comparable with the image, which carries neither.
EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "site",
    "target",
    ".mypy_cache",
}
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
TREE_ALGORITHM = (
    "SHA-256 over sorted relative POSIX path, NUL, file bytes, NUL; dependency, build, "
    "and cache directories plus .pyc excluded from source trees."
)
# Runs inside the image with no network. It must not read anything outside the tree.
EMBEDDED_TREE = r"""
import hashlib, json, os, sys
root = sys.argv[1]
excluded = set(json.loads(sys.argv[2]))
suffixes = tuple(json.loads(sys.argv[3]))
paths = []
for base, directories, files in os.walk(root):
    directories[:] = sorted(name for name in directories if name not in excluded)
    for name in sorted(files):
        if name.endswith(suffixes):
            continue
        full = os.path.join(base, name)
        paths.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
digest = hashlib.sha256()
for relative, full in sorted(paths):
    digest.update(relative.encode())
    digest.update(b"\0")
    with open(full, "rb") as handle:
        digest.update(handle.read())
    digest.update(b"\0")
print(json.dumps({"files": len(paths), "sha256": digest.hexdigest()}))
"""


def tree_hash(root: Path, *, paths: list[Path] | None = None) -> dict[str, object]:
    selected: list[tuple[str, Path]] = []
    for path in sorted(paths if paths is not None else root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if set(relative.parts) & EXCLUDED_DIRECTORIES or path.name.endswith(EXCLUDED_SUFFIXES):
            continue
        selected.append((relative.as_posix(), path))
    digest = hashlib.sha256()
    for relative, path in sorted(selected):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {"files": len(selected), "sha256": digest.hexdigest()}


def file_record(path: Path) -> dict[str, object]:
    return {
        "path": path.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def embedded_tree(image: str, root: str) -> dict[str, object]:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            image,
            "-c",
            EMBEDDED_TREE,
            root,
            json.dumps(sorted(EXCLUDED_DIRECTORIES)),
            json.dumps(list(EXCLUDED_SUFFIXES)),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"reading {root} from the image failed (exit {result.returncode})")
    return json.loads(result.stdout)


def image_id(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return result.stdout.decode().strip()


def build(repo: Path, args) -> dict[str, object]:
    backend = repo / "backend"
    runtime_module = backend / "lotuspond"
    excluded_runner_delivery = [
        "scripts/runner.mjs",
        "scripts/runner-execution.mjs",
        "scripts/runner-execution-child.mjs",
        "scripts/client.mjs",
        "protocol-adapters",
        "sandbox",
    ]
    proof = {
        "schema": "lotuspond.local-content-proof.v1",
        "tree_hash_algorithm": TREE_ALGORITHM,
        "git_provenance": None,
        "git_provenance_note": "Repository has no HEAD; hashes are content-based.",
        "bundle": file_record(args.bundle),
        "smoke_report": file_record(args.smoke_report),
        "delivery_scope": ["api", "static_ui", "collector", "postgres", "redis"],
        "excluded_runner_delivery": excluded_runner_delivery,
        "source_trees": {
            "backend": tree_hash(backend),
            "backend_runtime_module": tree_hash(runtime_module),
            "web": tree_hash(repo / "web"),
            "scripts_excluded_from_bundle": tree_hash(
                repo / "scripts",
                paths=[path for path in (repo / "scripts").rglob("*") if path.is_file()],
            ),
        },
        "local_built_dist": tree_hash(repo / "dist"),
        "images": {
            "LOTUSPOND_API_IMAGE": image_id(args.api_image),
            "LOTUSPOND_COLLECTOR_IMAGE": image_id(args.collector_image),
        },
        "embedded_runtime_trees": {
            "/app/backend/lotuspond": embedded_tree(args.api_image, "/app/backend/lotuspond"),
            "/app/dist": embedded_tree(args.api_image, "/app/dist"),
        },
    }
    module_source = proof["source_trees"]["backend_runtime_module"]["sha256"]
    module_image = proof["embedded_runtime_trees"]["/app/backend/lotuspond"]["sha256"]
    dist_source = proof["local_built_dist"]["sha256"]
    dist_image = proof["embedded_runtime_trees"]["/app/dist"]["sha256"]
    proof["embedded_matches_source"] = {
        "backend_runtime_module": module_source == module_image,
        "static_ui": dist_source == dist_image,
    }
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--smoke-report", type=Path, required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--collector-image", required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--require-match", action="store_true")
    args = parser.parse_args()
    proof = build(args.repo, args)
    print(json.dumps(proof, indent=2, sort_keys=True))
    if args.require_match and not all(proof["embedded_matches_source"].values()):
        sys.exit("the image does not embed this working copy")
