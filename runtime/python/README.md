# Lotuspond runtime contracts

`lotuspond-runtime==0.1.0` requires Python >=3.13 and `lotuspond-sdk==0.2.0`.
This independently installable distribution supplies pure, strict fleet models
and domain-separated connection/pool content identities. Importing the package
or `lotuspond_runtime.protocol` performs no I/O or runtime initialization.

The package currently grants no execution authority and supplies no production
client, transport, supervisor or secret resolver. Observations are declarations,
not attestations. Empty allowlists grant no capability. Destination-free operations
still require explicit catalog permission. Later runtime admission must recheck
current scoped authority, identity, pool/connection status and destination policy.

Public model/helper errors are bounded `FleetError` codes without caller input.
Models are frozen and strict; JSON arrays become tuples without scalar coercion.
Public validators and canonical helpers revalidate even constructed/mutated models.
Field membership is enforced independently of Pydantic's per-call `extra` option;
`ignore` and `allow` cannot discard or retain unknown fields. Existing SecretStr
wrappers must contain an exact string, including when their contents were mutated.
Ordinary secret model repr/JSON is redacted. Only `encode_issued_credential`,
`encode_prepared_credential` and `encode_acquired_session` return the intended
one-time secret as a plain string; callers must restrict these to private no-store
responses. `CredentialState` is private local state, never an API response.

Fleet JSON is limited to 65,536 bytes, depth 16 (root at depth zero), 4,096 values
and member names combined, and 8,192 UTF-8 bytes per string/member name. Canonical
config/policy documents have a separate 32,768-byte bound. Canonical set ordering
is explicit and does not give operation or destination rules precedence.

Supported JSON entry points are `FleetModelSubclass.model_validate_json(content)`
or `decode_fleet_json(content)` followed by a model/public validator or
`TypeAdapter(FleetModelSubclass).validate_python(decoded)`. They preserve the raw
byte, duplicate-key and decoded-value checks. Native Pydantic JSON validation
(such as `TypeAdapter(...).validate_json(content)`) is denied for fleet DTOs because
its parser discards duplicate-key and raw-byte evidence before model validation.
Use the bounded decoder even when integrating with a framework or adapter.

The fixture literals in `tests/fixtures` were independently calculated with Node
SHA256 during approved E03 planning; the suite checks exact bytes and identities
with pinned `rfc8785==0.1.4` and independent Node hashing. Their synthetic artifact
digest is protocol data and does not register an installed connector.

Development (repository root):

```sh
uv sync --frozen --project runtime/python
npm run check:runtime
```

Artifact tests independently build an SDK wheel, a direct runtime wheel and a
runtime wheel rebuilt from its sdist, install them outside the checkout without
source overrides, and probe with `python -I`. They record artifact SHA256 manifests
and interpreter identities in their temporary artifact directory. CI runs on
Python 3.13 and 3.14; these local checks do not establish production readiness.
