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
publisher trust, or demonstrate runtime compatibility. Signature and difference
tooling remain separate increments.

## Local verification

`npm run check:sdk` performs frozen dependency sync, Ruff and the standalone test
suite. Distribution tests install both a direct wheel and a wheel rebuilt from
the sdist into fresh environments outside the checkout and assert API dependencies
are absent. CI configures Python 3.13 and 3.14; hosted results are distinct from
local verification. The API image installs the SDK noneditably, with no SDK source
directory in the final image. Deployment content proof includes installed SDK
source bytes and the airgap smoke checks shared types and the historical digest.
