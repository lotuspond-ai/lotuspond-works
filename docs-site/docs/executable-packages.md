# Executable package v2 foundations

The standalone Python SDK has a separate `lotuspond_sdk.executable` namespace
for executable definitions. This increment provides bounded JSON decoding,
reviewed RFC 8785 canonicalization, strict validation/report helpers and fixed
value-free errors. Typed authoring, compilation, runtime-facing records and
explicit v2 CLI commands are subsequent implementation tasks. No workflow is
executed and no local artifact grants authority.

The schema identifier is `lotuspond.workflow-package.v2`; the canonicalizer is
`lotuspond.workflow-package.jcs.v2`. V1 retains its existing APIs, registry
format and historical canonical bytes.

Package raw input and normalized content are each at most 1,048,576 bytes, with
32 container levels and 10,000 items, including object keys. The wire boundary
is 65,536 bytes. Reports use 8,388,608 bytes, depth 32 and 262,144 items, counting
complete ASCII output and its final newline. Package admission always uses its
own smaller limits.

JCS includes normalized defaults, preserves array order and sorts object names
by UTF-16 units, including names such as `10` before `2`. Strings retain their
Unicode code points without normalization. Duplicate keys, invalid Unicode,
nonfinite numbers, cycles, coercions and unknown model fields are refused.
Known models are projected and revalidated after mutation or bypassed
construction without invoking their serializers.

Integer fields reject booleans, floats and values outside
±9,007,199,254,740,991. Declared numbers use the separate finite IEEE-754 domain:
JCS writes `1.0` as `1`, negative zero as `0`, and some large floats as integer
spellings. The raw decoder permits bounded integers until the declared field
chooses the numeric domain, so rereading that output remains possible. Exact
quantities will use fixed-scale decimal strings in the typed-schema increment.

Canonicalization calls exactly pinned `rfc8785==0.1.4` under Apache-2.0, reviewed
with source SHA-256
`e545841329fe0eee4f6a3b44e7034343100c12b4ec566dc06ca9735681deb4da`.
Attributed RFC number vectors and a separate Node oracle verify the numeric
spellings and Unicode ordering; Node is a test tool rather than an SDK runtime
dependency.

`ExecutableError` carries a closed code/message, at most 20 sanitized issues
and an explicit truncation flag. Pointers retain only fixed field names and
numeric offsets; supplied names and values never enter ordinary diagnostics.
Truncation preserves failure. Safe file/decode failures retain the existing
SDK operational error codes.
