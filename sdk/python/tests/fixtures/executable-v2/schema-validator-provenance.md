# Independent schema test oracle

Task 10 uses exactly `jsonschema==4.26.0` as a **dev-only** Python dependency.
It is a maintained independent implementation of JSON Schema Draft 2020-12,
used to exercise exported package/plan shapes, all seven runtime record types,
seventeen event variants and the outer event/payload/reference matrix. Snapshot
parity alone cannot detect a schema that accepts invalid correlated records.

Source: [python-jsonschema/jsonschema v4.26.0](https://github.com/python-jsonschema/jsonschema/tree/v4.26.0).
Distribution: [PyPI jsonschema 4.26.0](https://pypi.org/project/jsonschema/4.26.0/).
License: MIT. Source and wheel URLs, exact transitive versions and SHA-256
hashes are recorded in `sdk/python/uv.lock`. Reviewed transitive packages are
attrs (MIT), jsonschema-specifications (MIT), referencing (MIT), and rpds-py
(MIT). This adds no SDK runtime dependency, engine, HTTP client or database.
Installed artifact environments omit this dev group and assert their isolation.
Schema tests deliberately do not use the SDK's own validator as their schema
acceptance oracle. SDK semantic hashes/authentication remain beyond JSON Schema.
