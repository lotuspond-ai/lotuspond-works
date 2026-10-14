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

Validation and content digests check declarations and integrity only. They do
not install, execute, sign, authorize, resolve secrets or establish publisher
trust or runtime compatibility. Only workflow-package v1 is supported.

For development, run `uv sync --frozen --project sdk/python`, then `npm run
check:sdk`. Distribution tests build wheel and sdist and install each into a
fresh environment outside the checkout. Public package-index publication is
a separate action.
