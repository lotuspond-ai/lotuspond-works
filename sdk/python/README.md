# Lotuspond Python SDK

Independent local tooling for workflow-package v1; Python 3.13 or later. Runtime
dependencies are Pydantic and cryptography. No API server, database, Redis,
HTTP client, model provider or runtime execution is needed.

Build and install from this repository:

```sh
uv build --project sdk/python
python -m pip install sdk/python/dist/lotuspond_sdk-0.1.0-py3-none-any.whl
lotuspond-package validate contracts/examples/reviewed-action.v1.json
python -m lotuspond_sdk validate contracts/examples/reviewed-action.v1.json
```

The CLI prints ASCII-safe JSON, exits 0 for a valid package and 2 for invalid
input, and sanitizes errors without revealing paths, field values or instructions.

```python
from pathlib import Path
from lotuspond_sdk import load_package, canonical_package_json, package_digest

package = load_package(Path("workflow.json"))
canonical_bytes = canonical_package_json(package)
digest = package_digest(package)
```

`validate_package(raw)` revalidates typed instances, including nested mutations.
`decode_package_bytes(data)` rejects duplicate keys and nonfinite numbers.
`PackageReadError.code` and sanitized Pydantic `ValidationError` retain existing
backend behavior; operational file failures use fixed-code `SdkError`.
`read_regular(path, max_bytes=..., private=False)` reads bounded regular files;
private reads additionally require current-user ownership, owner-only permissions
and a platform that can refuse symlinks atomically.

Input is limited to 1,048,576 bytes, depth 32 and 10,000 JSON items. Canonical
content uses sorted keys, UTF-8, explicit normalized defaults and significant
array order. Canonicalization and validator revision identifiers are exported
from `lotuspond_sdk`; the backend uses this same implementation.

Compare two validated packages locally:

```python
from copy import deepcopy
from lotuspond_sdk import diff_packages

after = deepcopy(package)
after.nodes[2].instructions = "Propose a different update."
after.nodes.reverse()
report = diff_packages(package, after)
[(change.category, change.before_path, change.after_path) for change in report.changes]
# [('graph', '/nodes', '/nodes'),
#  ('instructions', '/nodes/2/instructions', '/nodes/3/instructions')]
report.model_dump_json()
```

`diff_packages(before, after, include_values=False)` revalidates both inputs and
returns `PackageDiff` with content digests, a tuple of `PackageChange` records,
and `authority_granted=False`. Each change contains a category, operation,
declaration ID and RFC 6901 paths into the corresponding normalized document;
absent locations are `None`. Default output omits value payloads, including
names, instructions and revisions. Declaration IDs remain visible.

Declarations match by ID, edges by source/target and connector operations by
value. A changed relative order of common elements emits `reordered`, alongside
edits or additions/removals. Insertion alone does not count as a reorder. Output
sorts by category, declaration ID, before path, after path and operation, treating
missing IDs/paths as empty strings. Normalized defaults and object key order do
not create changes; array order remains significant for both diff and digest.

`include_values=True` explicitly enables potentially sensitive local detail.
Only actual booleans are accepted; a string such as `"false"` raises `TypeError`.
Detail records contain JSON `values.before` and `values.after`, with `None` for
absent values. Reorder payloads show declaration IDs, edge pairs or operation
values in sequence order. Report models forbid extra fields and revalidate typed
instances and serialization. JSON is ASCII-safe and capped at 8 MiB; oversized
reports raise `SdkError` with `code="report_too_large"` without dropping changes.

Validation and content digests check declarations and integrity only. They do
not install, execute, sign, authorize, resolve secrets or establish publisher
trust or runtime compatibility. A diff provides no behavioral compatibility,
safety, trust or authority assessment. Only workflow-package v1 is supported.

For development, run `uv sync --frozen --project sdk/python`, then `npm run
check:sdk`. Distribution tests build wheel and sdist and install each into a
fresh environment outside the checkout. Public package-index publication is
a separate action.
