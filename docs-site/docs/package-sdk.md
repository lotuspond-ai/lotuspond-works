# Local workflow-package SDK

The standalone `lotuspond-sdk` 0.1.0 Python distribution validates workflow-package
v1 locally and calculates canonical content digests. It uses the same validator
as the API registry, with no API, database, Redis or HTTP client dependency.
Python 3.13 or later is required. Public package-index publication is separate.

## Build and install

From the repository root:

```sh
uv build --project sdk/python
python -m pip install sdk/python/dist/lotuspond_sdk-0.1.0-py3-none-any.whl
lotuspond-package validate contracts/examples/reviewed-action.v1.json
```

The equivalent module command is `python -m lotuspond_sdk validate workflow.json`.
The existing repository command `npm run package:validate -- workflow.json` remains
available. Paths are relative to the caller. CLI output is ASCII-safe JSON; a
valid package exits 0 and invalid input exits 2. Errors omit sensitive values,
unknown field names and file paths, and contain at most 20 entries.

## Python interface

```python
from pathlib import Path
from lotuspond_sdk import load_package, canonical_package_json, package_digest

package = load_package(Path("workflow.json"))
canonical = canonical_package_json(package)
sha256 = package_digest(package)
```

`validate_package(raw)` accepts JSON-compatible objects and revalidates received
typed models. `decode_package_bytes(data)` retains bounded decoding and duplicate
key rejection. Input and normalized content are bounded at 1,048,576 bytes,
depth 32 and 10,000 JSON items. Canonical bytes retain sorted object keys, UTF-8,
explicit defaults and significant array order. `PackageReadError` and Pydantic
`ValidationError` preserve backend contracts. `safe_validation_errors(error)`
returns sanitized errors. Operational file errors use `SdkError.code`.

Only v1 declarations are supported. Validation and digests do not install or run
workflows, resolve credentials, grant approval or tenant authority, establish
publisher trust, or demonstrate runtime compatibility. Signature tooling remains
a separate increment.

## Deterministic local differences

```python
from copy import deepcopy
from lotuspond_sdk import diff_packages

after = deepcopy(package)
after.nodes[2].instructions = "Propose a different update."
after.nodes.reverse()
report = diff_packages(package, after)
[(c.category, c.operation, c.before_path, c.after_path) for c in report.changes]
# [('graph', 'reordered', '/nodes', '/nodes'),
#  ('instructions', 'modified', '/nodes/2/instructions', '/nodes/3/instructions')]
print(report.model_dump_json())
```

This example uses the reviewed-action package. `diff_packages(before, after,
include_values=False)` validates both inputs and compares normalized declarations.
The strict `PackageDiff` contains before/after SHA-256 digests, a tuple of
`PackageChange` records and `authority_granted=False`. Categories are `graph`,
`model`, `instructions`, `limits`, `connector`, `secret_reference` and `metadata`;
operations are `added`, `removed`, `modified` and `reordered`. Each change gives
its declaration ID and RFC 6901 paths into the corresponding normalized document.
Absent locations are null. Names, instructions, revisions and other values are
omitted by default; declaration IDs remain visible.

Declarations match by ID, edges by source/target, and connector operations by
value. Reordering the common elements of a sequence produces its own change,
including when fields are edited or declarations added/removed. Insertion alone
does not count as a reorder. Node kind changes produce one graph replacement.
Records sort by category, declaration ID, before path, after path and operation,
using empty strings for absent IDs/paths. Equal digests yield no changes;
object key order and explicit normalized defaults do not affect content identity.

`include_values=True` explicitly enables potentially sensitive **local-only**
detail. Invalid flag types raise `TypeError`; the string `"false"` cannot enable
detail. Detail carries JSON `values.before` and `values.after`, with null for
absent values. Reorder values contain ordered IDs, edge pairs or operations.
Report models forbid extra fields and revalidate typed instances and serialization.
ASCII-safe JSON serialization is bounded at 8 MiB; `SdkError.code` is
`report_too_large` when the cap is exceeded, without silently dropping changes.
Diffs describe content only and cannot establish behavioral compatibility,
safety, trust, approval, tenant authority or permission to execute.

## Local verification

`npm run check:sdk` performs frozen dependency sync, Ruff and the standalone test
suite. Distribution tests install both a direct wheel and a wheel rebuilt from
the sdist into fresh environments outside the checkout and assert API dependencies
are absent. CI configures Python 3.13 and 3.14; hosted results are distinct from
local verification. The API image installs the SDK noneditably, with no SDK source
directory in the final image. Deployment content proof includes installed SDK
source bytes and the airgap smoke checks shared types and the historical digest.
