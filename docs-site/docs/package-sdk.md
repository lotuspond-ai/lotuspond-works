# Local workflow-package SDK

The standalone `lotuspond-sdk` 0.1.0 Python distribution validates workflow-package
v1 locally, calculates canonical content digests, compares content and verifies
detached Ed25519 signatures against explicit independent trust. It uses the same validator
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
publisher trust, or demonstrate runtime compatibility. Detached signature
verification is a separate explicit operation described below.

## Deterministic local differences

```python
from copy import deepcopy
from lotuspond_sdk import diff_packages

package = load_package(Path("contracts/examples/reviewed-action.v1.json"))
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

## Detached signatures and independent trust

```python
from lotuspond_sdk import generate_keypair, load_signing_key, sign_package, write_new

# Use new paths in an operator-controlled directory, with synthetic local data.
key_id = generate_keypair(Path("publisher.private.pem"), Path("publisher.public.pem"))
envelope = sign_package(package, load_signing_key(Path("publisher.private.pem")))
write_new(Path("workflow.signature.json"), (envelope.model_dump_json() + "\n").encode("ascii"))
```

Private PEM is unencrypted PKCS8; public PEM is SubjectPublicKeyInfo (SPKI).
Private files are created with mode 0600, require current-user ownership and no
group/other access, and are read without following the final symlink. Key reads
are nonblocking, require a regular file and are limited to 4096 bytes. Encrypted,
malformed and non-Ed25519 private keys are rejected. Unsupported private-key
platforms fail closed with `unsupported_key_platform`; permissions are not
silently weakened. Use protected custody and access-controlled offline backups:
the SDK supplies neither a keystore nor secret recovery, and Python does not
promise zeroization of in-memory private-key objects.

All new outputs use exclusive creation and refuse existing files, directories,
symlinks and named pipes. `write_new(path, data, private=False)` requires bytes and
an actual boolean, completes short writes and fsyncs the file. Public output mode
is 0644 masked by the caller’s umask. Once a destination has been created, a
publication failure preserves it, including any concurrent replacement. Failed
standalone publication returns `output_incomplete`; failed keypair publication
returns `keypair_incomplete`. Both require operator inspection: a complete private
key, a partial key file or partial signature output can remain. Do not blindly
retry into the same paths. Pre-existing outputs are refused without mutation.
No failure path unlinks a destination; POSIX cannot atomically compare an inode
and unlink against hostile concurrent directory writers. Keep parent directories
operator-controlled. Two-file publication is **not crash-atomic** and does not
promise durable parent directory entries after power loss. No key bytes or
supplied paths appear in SDK error messages.

The verifier obtains and authenticates the publisher’s public key **out of band**.
A key ID is SHA-256 of raw 32-byte public key bytes; an envelope never embeds a
public key. Provision a separate JSON trust set with exactly these fields:

```json
{"format_version":1,"keys":[{"key_id":"<lowercase hex64 fingerprint>","public_key":"<lowercase hex64 raw Ed25519 public key>","state":"allowed"}]}
```

The placeholders must be replaced with independently authenticated real values.
The trust set allows at most 64 keys, forbids extras and duplicates, and checks
every fingerprint. It rejects the established small-order compressed public-key
encodings (both sign bits) and noncanonical y encodings (y >= 2^255 - 19), so
an allowed identity-point key cannot authorize a forged signature. These are
encoding checks based on libsodium’s published constants, with cryptography
performing signature verification; they are not a full subgroup validation or a
claim of equivalence to Rust’s `verify_strict`. An empty set grants no trust. To rotate, independently
provision the new public key, allow both keys during a deliberate overlap, and
change the old entry to `revoked` when old signatures must no longer be accepted.
Revocation rejects even a mathematically valid signature immediately when that
updated trust set is supplied; the SDK does not retrieve remote revocation state.

```python
import hashlib
from cryptography.hazmat.primitives import serialization
from lotuspond_sdk import read_regular, verify_package

# This PEM must already be authenticated through an independent trusted channel.
public = serialization.load_pem_public_key(
    read_regular(Path("authenticated.publisher.public.pem"), max_bytes=4096)
).public_bytes_raw()
trust_set = {"format_version": 1, "keys": [{
    "key_id": hashlib.sha256(public).hexdigest(),
    "public_key": public.hex(), "state": "allowed",
}]}
report = verify_package(package, envelope, trust_set)
print(report.model_dump_json())
```

`sign_package(raw, private_key)` and `verify_package(raw, envelope, trust_set)`
revalidate canonical package content and all mutable typed records. Envelope and
trust format versions require integer 1; booleans and floats are rejected.
`SignatureEnvelope` fixes Ed25519, context
`lotuspond-workflow-package-signature-v1`, current schema/canonicalization IDs,
lowercase hex64 digest/key ID and lowercase hex128 signature. Signing uses
`context || NUL || uint64be(len(H)) || H || uint64be(len(C)) || C`, where `H` is
sorted-key compact ASCII JSON of all envelope fields except `signature`, and `C`
is exact canonical UTF-8 package content. This frame is distinct from evidence
manifest signatures and binds both metadata and normalized content.

Malformed envelopes/trust sets raise fixed `invalid_signature_envelope` or
`invalid_trust_set` errors. Well-formed outcomes return `VerificationReport`:
`verified`, `untrusted_key`, `revoked_key`, `digest_mismatch` or `invalid_signature`.
Unknown/revoked trust takes precedence over digest/signature checks. The report
contains the recomputed content digest, named key ID, `contract_valid=True`,
`compatibility_status="not_checked"` and `authority_granted=False`; only
`verified` has `valid=True`. All report/envelope/trust serialization revalidates
models, is ASCII-safe and is bounded at 8 MiB.

A valid detached signature establishes integrity under the supplied allowed key.
It grants no tenant authority, registry admission, release approval, deployment
admission, runtime compatibility or permission to execute. No hosted issuer or
trust registry is introduced. CLI signature/key commands are a separate increment.

## Local verification

`npm run check:sdk` performs frozen dependency sync, Ruff and the standalone test
suite. Distribution tests install both a direct wheel and a wheel rebuilt from
the sdist into fresh environments outside the checkout and assert API dependencies
are absent. CI configures Python 3.13 and 3.14; hosted results are distinct from
local verification. The API image installs the SDK noneditably, with no SDK source
directory in the final image. Deployment content proof includes installed SDK
source bytes and the airgap smoke checks shared types and the historical digest.
