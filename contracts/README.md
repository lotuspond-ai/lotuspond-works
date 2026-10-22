# Declarative workflow package contract

This is the first E02 contract foundation. It validates a bounded declaration; it does **not** execute a workflow, contact a model or connector, resolve credentials, enforce approval, qualify an integration, or authorize a deployment.

Every package in `examples/` is synthetic: `reviewed-action` (quoted on the public introduction), `document-intake`, `case-approval`, `knowledge-answer` and `record-reconciliation`. They are process shapes shared across industries, with placeholder `synthetic-*` connectors, not qualified integrations. They are also the Studio's starting patterns. `backend/tests/test_package_contract.py` validates each one, and `web/src/lib/studioPatterns.test.ts` requires the console to offer every one with the CLI's digest.

This pre-runtime topology is not yet a stable executable SDK contract. Consumers must pin the schema/model revision. Contract changes require explicit compatibility review; future data mapping, approval bindings and execution semantics cannot be silently added to strict version one.

## Validate locally

From the repository root, with the existing backend environment installed:

```sh
cd backend
.venv/bin/python -m lotuspond.package_cli validate ../contracts/examples/reviewed-action.v1.json
.venv/bin/python export_package_schema.py --check
```

The validation CLI writes one JSON result to stdout and returns `0` for valid or `2` for invalid. Errors contain bounded field paths, codes and fixed messages, without raw rejected values or unknown field names. It does not echo the input path. `--help` prints ordinary usage information.

## Version-one scope

`schema_version` is exactly `lotuspond.workflow-package.v1`. Package and connector versions use a numeric `major.minor.patch` subset with no leading zeroes or prerelease suffixes. IDs are bounded lowercase names. Objects reject undeclared fields and type coercion; numeric limits cannot be strings or booleans.

The top level declares package identity, entry node, model profile/revision references, connector versions/operations, secret reference names, nodes and edges. Secret references contain names only. Credentials, URLs, connection configuration, customer documents, executable code and arbitrary extension objects have no fields in this contract. Free-text instructions and names are not a secret scanner: authors must still keep private content and credentials out of them.

Supported node kinds are `start`, `end`, `agent`, `connector` and `human_review`. Agent nodes require explicit step, deadline, input/output token and micro-USD cost limits. Their limits are declarations only until a runtime enforces them. Connector operations must appear in their declared connector reference; that list is not a verified capability or permission grant.

The Python validator requires unique IDs within each list, declared references, unique edges, exactly one start matching the entry, at least one end, no incoming entry edge, no outgoing end edge, no nonterminal dead ends, reachability of every node and an acyclic graph. A human-review node expresses topology only; there is no approval binding or guarantee that write actions require review. Graph branching is structurally allowed without defining conditions, data mapping or scheduling semantics. Those executable semantics require later versioned contracts.

Bounds are 1 MiB UTF-8 input, 32 JSON nesting levels, 10,000 JSON values/keys, 128 nodes, 256 edges and 32 references of each kind. A connector declares at most 32 operations. The CLI rejects duplicate JSON keys, invalid UTF-8, nonfinite numbers, oversized numeric tokens, deep inputs and nonregular files before model validation. Programmatic validation additionally rejects cyclic/non-JSON objects.

## Public Python interface

```python
from lotuspond.package_contract import WorkflowPackage, validate_package, package_digest

package = validate_package(raw_json_object)
digest = package_digest(package)
```

`validate_package` returns `WorkflowPackage` and raises a Pydantic `ValidationError` for invalid input. Do not print a raw library error representation in an external boundary; the CLI explicitly sanitizes structured errors. `WorkflowPackage.model_validate` applies the same bounds and graph rules. Validation is not an immutable-storage API: callers must revalidate after changing in-memory objects.

`canonical_package_json` revalidates and serializes the model, including defaults, as sorted-key, compact UTF-8 JSON without ASCII escaping or nonfinite numbers. Array order remains significant; Unicode is not normalized. `package_digest` is SHA-256 over those bytes. Omitted and explicit null optional secret references normalize identically. This is a project-specific canonical form, **not** RFC 8785, a signature, publisher verification or a trust decision. Do not use it as proof of authorization or provenance.

The generated Draft 2020-12 JSON schema identifies `urn:lotuspond:workflow-package:v1` and describes field shapes and local bounds. Cross-reference, graph and overall input limits require the Python validator; schema-only validation is not full package admission. Regenerate the snapshot with `.venv/bin/python export_package_schema.py` from `backend/`, then review the diff. Tests and `--check` detect snapshot drift.
