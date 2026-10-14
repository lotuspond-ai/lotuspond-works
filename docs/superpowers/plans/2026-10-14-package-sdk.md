# Standalone package SDK, diff and detached trust tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver independently installable, bounded local tooling for workflow-package v1 validation, deterministic differences, detached signatures and declared compatibility.

**Architecture:** Move the existing pure contract into one SDK implementation and retain backend import compatibility. Separate content comparison, cryptographic trust, capability declarations and local file/CLI boundaries. The API continues to store immutable canonical bytes; these tools confer no execution authority.

**Tech Stack:** Python >=3.13 (CI 3.13 and 3.14), Pydantic >=2.12,<3, cryptography >=50,<51, Hatchling, uv; existing pytest/Ruff, PostgreSQL integration and Docker artifact checks. OpenSSL is an independent test oracle, never a runtime dependency.

**Spec:** [Approved product completion design](../specs/2026-10-14-product-completion-design.md), “Next subsystem: standalone package SDK, diff and detached trust tooling.” Owner approved the written design and sequence on 14 October 2026. The owner approved this implementation plan and selected subagent-driven execution with review after each task on 14 October 2026.

## Global Constraints

- Preserve existing source, archive, data, branches and unrelated work. Foundation PR #1 and registry PR #2 are merged; prepare SDK work on an isolated feature branch from the reviewed integration state, retaining the draft readiness PR.
- Keep v1 canonical bytes stable, immutable package records readable, and schema upgrades additive. This subsystem needs no database migration.
- Preserve `npm run package:validate`, schema export/parity, existing Python imports, registry canonical bytes and existing validator/canonicalizer identifiers when semantics do not change.
- API/database/web/runtime dependencies must not be needed to install and use local package tooling. No HTTP client, provider access, secret resolution or runtime execution is added.
- Freeze v1 sorted-key UTF-8 canonicalization, explicit normalized defaults and significant array order. Preserve 1,048,576 input bytes, nesting 32 and 10,000 JSON items.
- A valid signature does not grant tenant authority, deployment admission or execution permission. Profiles are explicitly synthetic declarations and cannot produce a production-supported claim.
- Local key generation writes owner-only private material and refuses existing output paths. Signing refuses unsafe private-key permissions and avoids printing private keys.
- Read `AGENTS.md` and `SECURITY.md`. Use independently verified localhost test databases only. Public package publication, production infrastructure and external customer qualification are separate actions.
- Every task includes focused red/green tests and its user documentation. Run full application checks, isolated database tests, artifact checks and independent review before shipping; record exact commits, failures, skips and evidence limits.

## Review Focus

1. A mutated typed model or escaped Unicode input must not bypass validation or change the historical digest: Task 1 `test_typed_mutation_and_unicode_parity`.
2. Reordering declarations while editing an instruction must retain both changes without revealing the instruction by default: Task 2 `test_reorder_and_edit_are_independent_and_private`.
3. A trust set containing the same key twice, including allowed/revoked entries, must reject ambiguity: Task 3 `test_duplicate_or_revoked_trust_cannot_authorize`.
4. A named pipe, symlinked private key, pre-existing output or failure writing a keypair must not hang, expose a key or clobber a file: Task 3 `test_key_file_boundaries`.
5. A profile that matches node names but lacks a connector version, model revision or platform requirement must fail declared compatibility: Task 4 `test_partial_profile_fails_closed`.

---

## File map and common contracts

Paths below are repository-relative. Only files assigned to a task should be edited by its implementer; later tasks extend earlier modules through these interfaces.

| Files | Responsibility / owning task |
| --- | --- |
| `sdk/python/pyproject.toml`, `sdk/python/uv.lock`, `sdk/python/README.md` | Distribution `lotuspond-sdk` version `0.1.0`, standalone environment, console entry point and local user guide; Task 1, extended by Tasks 2–5. |
| `sdk/python/src/lotuspond_sdk/__init__.py`, `contract.py`, `input.py`, `metadata.py`, `errors.py`, `files.py`, `cli.py`, `__main__.py`, `py.typed` | Public exports, unchanged validator/decoder, shared identifiers, fixed-code errors, bounded regular-file loading and initial validate CLI; Task 1. File/key operations extended by Task 3; command dispatch by Task 5. |
| `sdk/python/src/lotuspond_sdk/diff.py` | Deterministic content differences and privacy-aware serialization; Task 2. |
| `sdk/python/src/lotuspond_sdk/signing.py`, `trust.py` | Versioned signature envelope/framing, external trust records and verification; Task 3. |
| `sdk/python/src/lotuspond_sdk/compatibility.py` | Strict target/requirements models and declaration-only comparison; Task 4. |
| `sdk/python/tests/conftest.py`, `test_contract.py`, `test_input.py`, `test_files.py`, `test_distribution.py` | Standalone fixtures, canonical byte oracle, bounds, installed-distribution smoke; Task 1. |
| `sdk/python/tests/test_diff.py`, `test_signing.py`, `test_trust.py`, `test_key_files.py`, `test_compatibility.py`, `test_cli.py` | Behavioral tests for Tasks 2, 3, 4 and 5 respectively. |
| `sdk/python/tests/fixtures/reviewed-action.canonical.json`, `signing-v1.json`, `README.md` | Pre-extraction golden canonical bytes and independently checked signature vector/provenance; Tasks 1 and 3. |
| `backend/lotuspond/package_contract.py`, `package_input.py`, `package_cli.py`, `packages.py`, `backend/pyproject.toml`, `backend/uv.lock` | Compatibility imports, local SDK dependency and shared metadata; Task 1. |
| `backend/tests/test_package_sdk_compatibility.py`, existing `test_package_contract.py`, `test_package_cli.py`, `test_packages.py` | Legacy module/CLI/registry equivalence and unchanged historic checks; Task 1, CLI parity extended by Task 5. |
| `deploy/Dockerfile`, `deploy/checks/content-proof.py`, `test_content_proof.py`, `airgap-smoke.py` | Noneditable SDK installation, SDK source-content proof, import/digest smoke in the actual API image; Task 1. |
| `.github/workflows/ci.yml`, `package.json` | SDK version matrix, wheel smoke, required local SDK check; Task 1, extended in Task 5. |
| `docs-site/docs/package-sdk.md`, `docs-site/mkdocs.yml`, `README.md`, `docs/CAPABILITIES.md`, `backend/lotuspond/app.py` | SDK usage, capability boundaries and API changelog; Tasks 1–5 update with actual delivered behavior. |
| `docs/plans/2026-10-14-package-sdk-progress.md` | Actual red/green, review, artifact and CI evidence; all tasks. |

Package validation retains the existing `ValidationError` and `PackageReadError` contracts. New operational failures use `SdkError(code: str)` from `errors.py`, with a closed code-to-message map, no supplied values/paths and no exception chaining in CLI output. Public report models use strict Pydantic fields, forbid extras, and revalidate received model instances. JSON serialization is deterministic, ASCII-safe for reports, and capped at 8 MiB; exceeding the cap raises `report_too_large` instead of silently dropping changes. CLI errors contain at most 20 sanitized entries. Package content canonicalization retains its existing UTF-8 format.

`metadata.py` exports `VALIDATOR_REVISION = "lotuspond.workflow-package.validator.v1.1"` and `CANONICALIZATION = "lotuspond.workflow-package.canonical-json.v1"`. Preserve `SCHEMA_VERSION = "lotuspond.workflow-package.v1"` in `contract.py`. Keep these imports available through `lotuspond_sdk` and the current backend modules.

## Task 1: Installable SDK with unchanged backend and artifact behavior

**Files:** Create the Task 1 files in the map; modify the listed backend, deployment, CI and documentation files. The committed v1 JSON schema must remain byte-identical.

**Interfaces:**
- Consumes: existing `validate_package(raw: object) -> WorkflowPackage`, `canonical_package_json(package: WorkflowPackage) -> bytes`, `package_digest(package: WorkflowPackage) -> str`, `decode_package_bytes(data: bytes) -> object`, `safe_validation_errors(error: ValidationError) -> list[dict]`.
- Produces: the same contract/decoder functions in the SDK, `files.read_regular(path: Path, *, max_bytes: int, private: bool = False) -> bytes`, `files.load_package(path: Path) -> WorkflowPackage`, `cli.main(argv: list[str] | None = None) -> int`, and the shared metadata/error contracts above. `lotuspond.package_cli.read_package(path: Path) -> object` retains its old decoded-object behavior.

- [ ] **Step 1: Freeze independent baseline bytes and write compatibility tests.** Copy canonical bytes from the current implementation before extraction into the golden fixture; the existing example must retain the digest below. Keep historical backend tests and parameterize equivalent standalone tests, including default `secret_ref: null`, non-ASCII instructions, invalid Unicode, duplicate keys, FIFO, byte/depth/item limits, oversized normalized models and typed-model mutation. Add wheel smoke that runs from a temporary directory with `PYTHONPATH` removed, checks the package module is inside that environment, and forbids API dependencies/imports.

```python
def test_historical_contract_identity(example, golden_bytes):
    assert canonical_package_json(validate_package(example)) == golden_bytes
    assert package_digest(validate_package(example)) == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"

def test_legacy_modules_share_the_contract():
    assert legacy.WorkflowPackage is lotuspond_sdk.WorkflowPackage
    assert legacy_input.PackageReadError is sdk_input.PackageReadError

def test_typed_mutation_and_unicode_parity(example, golden_bytes):
    package = validate_package(example)
    package.nodes[2].limits.max_steps = True
    with pytest.raises(ValidationError):
        validate_package(package)
    example["name"] = "हिंदी café"
    assert canonical_package_json(validate_package(example)) == golden_bytes.replace(b"Reviewed action example", "हिंदी café".encode("utf-8"))
```

- [ ] **Step 2: Run the new tests before extraction.** `backend/.venv/bin/python -m pytest sdk/python/tests/test_contract.py sdk/python/tests/test_input.py sdk/python/tests/test_files.py backend/tests/test_package_sdk_compatibility.py -q` must fail because the standalone SDK is unavailable. Record the intended failure, not an unrelated fixture failure.
- [ ] **Step 3: Extract one implementation and wire packaging.** Use `src/lotuspond_sdk`, Python >=3.13, runtime dependencies Pydantic and cryptography only; add `lotuspond-package = "lotuspond_sdk.cli:main"`. Move pure contract/decoder behavior unchanged; retain explicit compatibility exports for currently imported names and old CLI output/exit behavior. Backend depends on `lotuspond-sdk==0.1.0` with uv source `{path = "../sdk/python", editable = true}`. Generate both lockfiles with uv; do not hand-edit resolutions. Add `npm run check:sdk` using frozen SDK sync, Ruff and pytest, and invoke it from `npm run check`.
- [ ] **Step 4: Make the actual deployable artifact independent of the checkout.** Copy `sdk/python` to `/app/sdk/python` before dependency installation in the build stage; use `uv sync --no-editable` for production sync. The final image must import the installed SDK without `/app/sdk/python` or repository mounts. Extend content proof to compare `sdk/python/src/lotuspond_sdk` with the installed SDK directory found by the image's Python, including `__init__.py`/`py.typed` and excluding bytecode. Add a regression where changing only SDK source makes the proof fail; keep existing API/UI proof. Extend the actual airgap API smoke to assert SDK/backend type identity and the golden example digest, passing synthetic input without a repository bind.
- [ ] **Step 5: Verify isolation and parity.** Run frozen sync for SDK/backend, both focused test sets, `npm run check:contracts`, and deployment proof tests. `uv build --project sdk/python` followed by `test_distribution.py` must build wheel and sdist, install a wheel from each in separate fresh environments, execute from outside the checkout, and confirm FastAPI/SQLAlchemy/psycopg/Redis are absent. The CI SDK matrix runs these checks on Python 3.13 and 3.14; ordinary full CI/airgap jobs must pass with the moved dependency. Rebuild the SDK wheel from the sdist to prove source completeness. Document build/install/validate and the v1-only boundary.
- [ ] **Step 6: Commit the reviewed deliverable.** Stage only the Task 1 file set, inspect `git diff --cached --stat` and `git diff --cached --check`, then `git commit -m "feat: extract standalone workflow package SDK"`.

## Task 2: Deterministic package differences with private values omitted

**Files:** Create `diff.py` and `test_diff.py`; extend SDK public exports, README and docs-site SDK guide.

**Interfaces:**
- Consumes: Task 1 validation/canonicalization/digest.
- Produces: `diff_packages(before: object, after: object, *, include_values: bool = False) -> PackageDiff` in `diff.py`. `PackageDiff` fields: `before_sha256: str`, `after_sha256: str`, `changes: tuple[PackageChange, ...]`, `authority_granted: Literal[False] = False`. `PackageChange` fields: `category` in `graph|model|instructions|limits|connector|secret_reference|metadata`, `operation` in `added|removed|modified|reordered`, `declaration_id: str | None`, `before_path: str | None`, `after_path: str | None`, and optional `values` holding JSON `before`/`after` only in explicit detail mode. Paths are RFC 6901 pointers into the corresponding normalized document; absent locations are null.

- [ ] **Step 1: Write diff tests with independently expected changes.** Cover each category, add/remove, normalized defaults, equal content, invalid input, multiple changes, and a near-limit valid document. Reordering `nodes`, `models`, `connectors`, `secret_refs`, `edges` or connector `operations` must change the digest and emit `reordered`. Match declarations by `id`, edges by `(source,target)`, operations by value; also compare original sequence order. Classify `model_ref` as model, `connector_ref`/`operation` as connector, connector `secret_ref` and secret declarations as secret_reference; other node/edge topology as graph; package identity/name/version as metadata. A node kind change is one graph replacement, without attempting incompatible field comparison.

```python
def test_reorder_and_edit_are_independent_and_private(example):
    changed = deepcopy(example)
    changed["nodes"][2]["instructions"] = "PRIVATE-INSTRUCTION-SENTINEL"
    changed["nodes"].reverse()
    report = diff_packages(example, changed)
    assert {c.operation for c in report.changes} == {"reordered", "modified"}
    assert {c.category for c in report.changes} == {"graph", "instructions"}
    assert "PRIVATE-INSTRUCTION-SENTINEL" not in report.model_dump_json(exclude_none=True)
    assert "PRIVATE-INSTRUCTION-SENTINEL" in diff_packages(example, changed, include_values=True).model_dump_json()
    assert report == diff_packages(example, changed)
    assert diff_packages(example, deepcopy(example)).changes == ()
```

- [ ] **Step 2: Run `uv run --frozen --project sdk/python pytest sdk/python/tests/test_diff.py -q`.** Expect the missing diff interface to fail.
- [ ] **Step 3: Implement `diff_packages` and its models in `diff.py`.** Validate both inputs before any comparison. Sort output by `(category, declaration_id or "", before_path or "", after_path or "", operation)`; use source/target indices for actual pointers. Emit array reorder when the relative order of common elements changes, even alongside additions/removals. Default output includes identifiers/paths/digests, never arbitrary names, instructions, revisions or value payloads. Detail mode is local-only, explicit and documented as potentially sensitive. Do not infer behavioral compatibility, safety or authority from any diff.
- [ ] **Step 4: Run the diff tests and SDK check.** Assert both redacted/detail serialization stay bounded, ordering is repeatable, source inputs remain unchanged, and digest equality always yields an empty diff. Update examples with literal expected category/path output.
- [ ] **Step 5: Commit only Task 2 changes.** Inspect staged diff and run `git commit -m "feat: add deterministic privacy-aware package diff"`.

## Task 3: Detached Ed25519 signatures, independent trust and safe key files

**Files:** Create `signing.py`, `trust.py`, the three Task 3 test modules and signature fixture/provenance; extend `files.py`, `errors.py`, SDK exports and local key-lifecycle documentation. Read `evidence-core/src/signing.rs` for existing conventions; keep the package context distinct.

**Interfaces:**
- Consumes: Task 1 validated canonical bytes, digest and metadata.
- Produces in `signing.py`: `sign_package(raw: object, private_key: Ed25519PrivateKey) -> SignatureEnvelope`, `verify_package(raw: object, envelope: object, trust_set: object) -> VerificationReport`.
- `SignatureEnvelope` strictly contains `format_version: Literal[1]`, `algorithm: Literal["ed25519"]`, `context: Literal["lotuspond-workflow-package-signature-v1"]`, `schema_version`, `canonicalization`, `key_id`, `sha256`, `signature`. Schema/canonicalization are the current exact identifiers, key/digest are lowercase hex64, signature is lowercase hex128. No embedded public key.
- `trust.py` defines `TrustedKey(key_id: str, public_key: str, state: Literal["allowed", "revoked"])` (public key is raw Ed25519 lowercase hex64), and `TrustSet(format_version: Literal[1], keys: list[TrustedKey])`, maximum 64 keys. Key IDs equal SHA-256 of raw public bytes. Reject duplicate IDs/public keys and any fingerprint mismatch. Empty trust grants nothing.
- `VerificationReport` fields: `valid: bool`, `code` in `verified|untrusted_key|revoked_key|digest_mismatch|invalid_signature`, `sha256: str`, `key_id: str`, `contract_valid: Literal[True] = True`, `compatibility_status: Literal["not_checked"] = "not_checked"`, `authority_granted: Literal[False] = False`. Invalid envelope/trust structure raises `SdkError("invalid_signature_envelope"|"invalid_trust_set")`; well-formed negative trust/signature results are reports.
- Produces in `files.py`: `generate_keypair(private_path: Path, public_path: Path) -> str` returning key ID, `load_signing_key(path: Path) -> Ed25519PrivateKey`, `write_new(path: Path, data: bytes, *, private: bool = False) -> None`. PEM formats are unencrypted PKCS8 private and SPKI public. File operations require POSIX permission enforcement for private keys; unsupported platforms return a fixed `unsupported_key_platform` error, not a weakened check.

- [ ] **Step 1: Write cryptographic and file-boundary tests.** Include RFC 8032 section 7.1 test vector 1 to check the library/key encoding against known bytes; include a package fixture whose expected signature is independently produced by OpenSSL over the specified frame. Verify SDK signatures with OpenSSL and OpenSSL signatures with the SDK; tests must not derive expected signatures by calling the implementation. Cover changed canonical bytes, signature bytes, every metadata field, evidence-manifest context, unknown algorithm/version, malformed encoding/extra fields, wrong/unknown/revoked key, duplicate trust records and a mutated typed envelope/trust model.

```python
def test_duplicate_or_revoked_trust_cannot_authorize(example, key, allowed, revoked):
    envelope = sign_package(example, key)
    assert verify_package(example, envelope, allowed).code == "verified"
    assert verify_package(example, envelope, revoked).code == "revoked_key"
    with pytest.raises(SdkError) as error:
        verify_package(example, envelope, {"format_version": 1, "keys": allowed["keys"] + revoked["keys"]})
    assert error.value.code == "invalid_trust_set"
    assert verify_package(example, envelope, allowed).authority_granted is False

def test_key_file_boundaries(tmp_path):
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    generate_keypair(private, public)
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    original = private.read_bytes()
    with pytest.raises(SdkError):
        generate_keypair(private, public)
    assert private.read_bytes() == original
    private.chmod(0o644)
    with pytest.raises(SdkError):
        load_signing_key(private)
```

- [ ] **Step 2: Run `uv run --frozen --project sdk/python pytest sdk/python/tests/test_signing.py sdk/python/tests/test_trust.py sdk/python/tests/test_key_files.py -q`.** Expect missing interfaces; add the named-pipe/symlink, wrong-owner, partial-write and second-file collision cases before implementation. Use synthetic keys and bounded subprocess timeouts; never real credentials.
- [ ] **Step 3: Implement strict framing and verification.** Let `H` be the sorted-key compact ASCII JSON of all envelope fields except `signature`, and `C` the exact canonical package bytes. Sign exactly `b"lotuspond-workflow-package-signature-v1\x00" + len(H).to_bytes(8, "big") + H + len(C).to_bytes(8, "big") + C`. Recompute metadata/digest from validated content; verify against the independently supplied allowed key, never keys derived from the envelope. A revoked or unknown key cannot succeed even with a mathematically valid signature. Preserve all verification distinctions in fixed error codes.
- [ ] **Step 4: Implement safe local file operations.** Read regular files nonblocking with size+1 bounds and descriptor checks. Private-key reads use no-follow, current-user ownership and mode `& 0o077 == 0`; cap PEM at 4096 bytes and reject non-Ed25519/encrypted/malformed keys safely. Writes use exclusive/no-follow creation, private mode 0600 (public 0644 masked by umask), complete-write/fsync and no overwrite. Keypair paths must differ; on ordinary second-write failure remove only a first file created by this call whose inode still matches. If cleanup cannot be confirmed, report `keypair_incomplete` and require operator inspection; never remove a replaced/user-owned file. Document that two-file publication is not crash-atomic. Add tests for all Review Focus 4 cases, including injected partial-write/cleanup failures and no private bytes in exception/log output.
- [ ] **Step 5: Run cryptographic/file tests plus SDK check.** Mandatory OpenSSL cross-verification runs in the CI Linux matrix, with failure rather than skip if missing there. Record vector provenance and exact frame in the fixture README. Document independent trust provisioning, rotation overlap, immediate revoked-state rejection, private-key custody/backup, and limits of local verification; no hosted issuer/registry admission is introduced.
- [ ] **Step 6: Commit only Task 3 changes.** Inspect staged files for synthetic fixtures only, then `git commit -m "feat: add detached package signatures and explicit trust"`.

## Task 4: Strict declaration-only capability comparison

**Files:** Create `compatibility.py`, `test_compatibility.py`, `contracts/examples/synthetic-capabilities.v1.json`, `contracts/examples/synthetic-requirements.v1.json`; extend exports, SDK guide and README.

**Interfaces:**
- Consumes: Task 1 package validation/metadata, common report/error contracts.
- Produces: `check_declared_compatibility(raw: object, profile: object, requirements: object) -> CompatibilityReport`.
- `CapabilityProfile` requires `format_version: Literal["lotuspond.workflow-capabilities.v1"]`, `assurance: Literal["synthetic-declaration"]`, `schema_versions: list[str]`, `canonicalizations: list[str]`, `node_kinds: list[Literal["start", "end", "agent", "connector", "human_review"]]`, `connectors: list[ConnectorCapability]`, `models: list[ModelCapability]`, `platforms: list[PlatformCapability]`. Connector capability is exact `connector`, `version`, `operations`; model capability is exact `profile`, `revision`; platform capability is `os`, `architecture`, `capabilities: list[Identifier]`.
- `CompatibilityRequirements` requires `format_version: Literal[1]`, `os: Identifier`, `architecture: Identifier`, `required_capabilities: list[Identifier]`. Platform requirements are a separate explicit assessment input because v1 has no platform field; they never alter the v1 digest.
- All profile lists are bounded to 64 entries (connector operations 32), permit empty lists to declare no support, and are unique by their natural key. Use existing `Identifier`, `Version` and `ReferenceRevision` constraints for named capabilities, connector versions and model revisions. Unsupported schema/canonicalizer values, unknown fields and missing required fields are invalid; matching is exact with no wildcard, range or default platform. Input files are bounded as in Task 5.
- `CompatibilityReport` has `compatible: bool`, `package_sha256: str`, `profile_sha256: str`, `requirements_sha256: str`, `assurance: Literal["synthetic-declaration"]`, `contract_valid: Literal[True] = True`, `signature_status: Literal["not_checked"] = "not_checked"`, `authority_granted: Literal[False] = False`, and at most 20 sorted `reasons` with fixed `code` and sanitized `path`, plus `reasons_truncated: bool`. Profile/requirements digests use sorted compact UTF-8 JSON of their validated normalized content. Codes distinguish `unsupported_schema`, `unsupported_canonicalization`, `unsupported_node`, `unsupported_connector`, `unsupported_operation`, `unsupported_model`, `unsupported_platform`, `missing_capability`; invalid shapes raise `SdkError("invalid_capability_profile"|"invalid_compatibility_requirements")`.

- [ ] **Step 1: Write full and partial profile tests.** Use a complete synthetic profile for the existing example; remove or mismatch each required capability independently. Check all declared connectors/operations and models, including unused declarations. Unknown fields/version, duplicate/conflicting entries, a mutated typed profile, empty profiles and absent assessment requirements must fail. Successful output still denies authority and says synthetic.

```python
def test_partial_profile_fails_closed(example, full_profile, requirements):
    assert check_declared_compatibility(example, full_profile, requirements).compatible is True
    for field, expected in [("connectors", "unsupported_connector"), ("models", "unsupported_model"), ("platforms", "unsupported_platform")]:
        partial = deepcopy(full_profile)
        partial[field] = []
        report = check_declared_compatibility(example, partial, requirements)
        assert report.compatible is False
        assert expected in {reason.code for reason in report.reasons}
        assert report.authority_granted is False
```

- [ ] **Step 2: Run `uv run --frozen --project sdk/python pytest sdk/python/tests/test_compatibility.py -q`.** Expect the absent interface to fail.
- [ ] **Step 3: Implement strict models and exact comparison in `compatibility.py`.** Revalidate all inputs before comparing. Evaluate declared schema, canonicalization, every node kind, connector version/operation, model revision and explicit OS/architecture/capabilities. Return deterministic reasons without private values. Truncating reasons must retain `compatible=False`; it never converts an incomplete assessment into success. The result does not check credentials, tenant policy, real provider availability or runtime support.
- [ ] **Step 4: Run tests and SDK check.** Include exact version/operation/revision mismatch, required platform capability absent, conflicting platform entries and more than 20 refusal reasons. Assert package digest is unchanged by assessment context, profile/requirements changes alter their report digests, and no fixture is described as production-supported. Document the two synthetic JSON inputs and what the result can establish.
- [ ] **Step 5: Commit only Task 4 changes.** Inspect staged diff and run `git commit -m "feat: check declared package capabilities without execution authority"`.

## Task 5: Complete CLI journeys and independently review the delivered subsystem

**Files:** Extend SDK `cli.py`, `errors.py`, `README.md`, public exports and `test_cli.py`; update legacy CLI parity tests, CI, root README, capability register, SDK docs-site page/navigation, changelog and progress evidence.

**Interfaces:**
- Consumes: all public functions from Tasks 1–4; `cli.main(argv: list[str] | None = None) -> int` remains the entry point for `lotuspond-package`, `python -m lotuspond_sdk` and the legacy module.
- Commands: `validate PATH`; `digest PATH`; `canonicalize PATH --output PATH`; `diff BEFORE AFTER [--include-values]`; `keygen --private PATH --public PATH`; `sign PATH --key PATH --output PATH`; `verify PATH --signature PATH --trust PATH`; `compatibility PATH --profile PATH --requirements PATH`. No implicit network, key discovery, overwrite or credentials from environment.
- Exit status: 0 successful operation (a valid diff may contain changes); 2 argument/input/file/format failure; 3 well-formed signature/trust refusal or incompatible declaration. Preserve existing validate JSON exactly. Other commands emit one JSON report with `ok` and bounded command result/error. `--help` prints usage/returns 0; argument errors never echo supplied arguments. Outputs containing canonical bytes or signature envelopes go only to explicit exclusive files, with a digest/result on stdout; private keys never go to stdout.
- Bounded reads: package 1,048,576 bytes, PEM/envelope 4096, trust/profile 65,536, requirements 16,384. Structured auxiliary inputs use the existing duplicate-key/nonfinite/depth checks plus their own strict models/item limits. JSON output >8 MiB fails safely. Errors from any command are value-free, at most 20 entries, no traceback or stderr secret/path echo.

- [ ] **Step 1: Write installed-CLI journey tests.** Invoke real subprocesses from a temporary directory with sanitized environment, no checkout on import path and timeouts. Validate/digest/canonicalize → diff → generate synthetic keys → construct an independent explicit trust set from public PEM → sign/verify → mutate package and refuse → revoke key and refuse → compare full/partial synthetic capability profiles. Assert output bytes, keys, modes, exit codes and JSON shapes; no sentinel private value appears in default diff or any error. A second write to any output fails and preserves its bytes. Add malformed/oversized/FIFO auxiliary inputs, wrong key algorithm, duplicate trust JSON keys and unknown CLI options carrying private sentinel text.

```python
def test_installed_cli_preserves_validate_and_refuses_untrusted(example_path, invoke, signed, empty_trust):
    result = invoke("validate", str(example_path))
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"ok": True, "schema_version": "lotuspond.workflow-package.v1", "sha256": "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"}
    result = invoke("verify", str(example_path), "--signature", str(signed), "--trust", str(empty_trust))
    assert result.returncode == 3
    assert json.loads(result.stdout)["result"]["code"] == "untrusted_key"
    assert result.stderr == ""
```

- [ ] **Step 2: Run `uv run --frozen --project sdk/python pytest sdk/python/tests/test_cli.py -q`.** Expect unsupported command failures; assert legacy validate tests still pass before changing dispatch.
- [ ] **Step 3: Implement CLI dispatch and safe presentation.** Reuse public APIs, descriptor-bound file reads and exclusive writes; do not duplicate contract/trust/compatibility logic. Sanitize Pydantic and OS errors using fixed allowed field names and codes. Document every command, status, bound, import, dependency, supported Python version, Linux private-key restriction, trust lifecycle and declaration-only limitation. Update current capability/changelog wording only for delivered tooling; retain all open E02/runtime/production gates.
- [ ] **Step 4: Verify the complete artifact and backend.** Run SDK matrix tests, both wheel/sdist installed journeys, OpenSSL cross-verification, `npm run check`, `npm run test:database` against the identity-verified loopback DB, schema parity, deployment checks, strict MkDocs and `git diff --check`. Exercise the built API image and existing airgap installation/recovery workflow; verify SDK content proof. Use the saved cloud serial Node wrapper/subreaper only where required by this environment and record it separately from default CI. Do not substitute unit mocks for artifact checks; blocked checks remain open with their exact reason.
- [ ] **Step 5: Obtain independent whole-branch review.** Review immutable base/head for v1 byte/import/CLI compatibility, decoder/file bounds, crypto framing/trust, private output, capability semantics, installed artifact completeness and inherited tenant/registry behavior. Address material findings with a red regression where applicable and rerun affected checks. Task reviews, when selected, do not replace this final review.
- [ ] **Step 6: Commit and publish the concrete deliverable.** Stage only intended files; `git commit -m "feat: expose package SDK trust and compatibility commands"`. Push the isolated branch normally, create a draft PR with concrete behavior/tests/limitations and attach it to this chat. Check the diff against the actual base, remote head and all exact-head CI jobs; do not claim passes from an earlier head. Do not publish to a package index, merge the new feature PR or deploy without the applicable scope. Record completed SDK portions and retain executable-v2/trusted-admission/runtime requirements as open.

## Self-review and handoff

The plan covers every requirement in the approved next-subsystem section: extraction and compatibility (Task 1), content/diff privacy (Task 2), signature/trust/file boundaries (Task 3), explicit capability assessment (Task 4), and CLI/artifact/documentation/review closure (Task 5). All five Review Focus cases have assigned tests. Public names and report fields are shared across tasks; no task depends on an undefined service or executable v1 behavior.

Self-review added the container's noneditable installation/content proof, independent sdist rebuild, mixed allowed/revoked duplicates, keypair partial-failure behavior, model revision matching, explicit platform input and full installed CLI journey. These are verification and implementation details for the approved scope. No existing registry records need rewriting; rollback is the previous application artifact with its own locked dependencies and unchanged data.

Recommend subagent-driven execution because the five tasks establish reusable validation and trust boundaries, where review before subsequent tasks consume an interface reduces the cost of an error. Native execution remains available with one independent whole-branch review. The owner approved this plan and selected subagent-driven execution with review after each task on 14 October 2026; execution is in progress. Broader product subsystems remain in the approved design's requirement register and receive their own specifications/plans in dependency order.
