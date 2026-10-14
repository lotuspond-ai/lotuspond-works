# Lotuspond runtime contracts

`lotuspond-runtime==0.1.0` requires Python >=3.13 and `lotuspond-sdk==0.2.0`.
This independently installable distribution supplies pure, strict fleet models
and domain-separated connection/pool content identities. Importing the package
or `lotuspond_runtime.protocol` performs no I/O or runtime initialization.

The package supplies a private control client, direct TLS transport, durable C/P
credential store and Linux lifecycle supervisor. It grants no execution authority.
Business polling, invocation, connector and secret-provider integration are separate
work; the supervisor cannot execute business work. Observations are declarations,
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


The explicit `lotuspond-runtime` console command (or `python -m lotuspond_runtime`)
uses a private operator config. Importing the package/protocol remains inert.
Provision the directory and files as the runtime's effective user: private config,
enrollment token and state/lock files are 0600, and their parent/state directories
are 0700. Ancestors must belong to root or that user and cannot be group/other
writable, except a root-owned sticky temporary directory. Symlinks are refused;
state and lock files must be regular files with one hard link. Storage uses pinned
Linux directory descriptors, an exclusive held flock, compare/revision checks,
0600 unique temporary files, file fsync, atomic rename, and directory fsync.
Pre-create the private state directory; this client does not choose host custody.
Private state deliberately stores the usable C and optional P credential tokens.
It never stores the process session secret.

Example operator config (synthetic paths/origin; provision the CA independently):

```json
{
  "origin": "https://control.example.invalid",
  "ca_file": "/etc/lotuspond/control-ca.pem",
  "state_directory": "/var/lib/lotuspond-runtime/private",
  "installation_id": "installation-from-operator",
  "observation": {"platform": "linux", "connectors": []}
}
```

Config rejects unknown/duplicate fields, coercions, secrets and control characters
in paths. CA/state paths are absolute. Config/token command arguments may be
relative to a private working directory; they still undergo the same descriptor
checks. The CA is a bounded regular file, with no symlink final component. The
origin has no path (including trailing slash), query, fragment or credentials.
HTTPS verifies the original hostname against only the independently supplied CA.
`mode` defaults to `production`; explicit `development`/`test` may use HTTP only
for a loopback hostname/address that also resolves to a loopback socket. These
modes do not supply connector implementations or execution authority.

This distribution currently registers no production connector. CLI observations
must therefore have an empty connector list; putting an arbitrary artifact pin
in config cannot claim installed support. Shared RuntimeObservation/RuntimeClient
remain declaration contracts for separately injected test fixtures and later fixed
catalog registration. No arbitrary plugin loader or production fixture selector
is provided.

```sh
lotuspond-runtime enroll --config /private/runtime.json --token-file /private/enrollment
lotuspond-runtime run --config /private/runtime.json
```

`--token-file -` accepts at most 128 bytes from non-TTY stdin. Tokens have distinct
canonical enrollment/runtime/session prefixes and 256-bit shapes. There is no
argv/environment token option, ambient credential fallback, proxy support,
redirect following, or TLS-verification bypass. Request/response bodies are at
most 65,536 bytes, response headers at most 16,384 bytes/64 fields; duplicate
headers and compressed/transfer-encoded responses are refused. Each full control
attempt has a ten-second deadline across owned DNS subprocess resolution,
connect, TLS, send and reads. One resolved socket is selected and connected once;
DNS helper environments contain no control secrets, and owned process groups are
killed/reaped. Unconfirmed cleanup fences subsequent use.

Library callers construct `RuntimeSupervisor(client, store, observation,
installation_id=...)`; the required keyword is the independently provisioned
config value, never an installation inferred from a response.

Enrollment persists C durably before session acquisition. Session proof stays in
supervisor memory. Rotation starts with at least five minutes remaining plus
0–30 seconds of jitter, using confirmed server time and monotonic elapsed time;
clock disagreement over 30 seconds fences the process. Heartbeats run every
20 seconds. Shutdown uses SIGINT/SIGTERM and closes owned transport resources.

The supervisor persists C/P before activation and retains uncertainty after a
lost response or storage failure. On restart it first tries P's metadata-only
active retry without session proof, otherwise verifies C with safe self-state.
It waits for a previous session's lease to expire before acquisition. Pending P
cannot activate without the lost original session. Acquire-response loss also
leaves the lease occupied until server expiry. A 429, 503 or transport failure
stops the process; none proves a mutation failed. No expiry is extended locally.
If neither credential remains usable, explicit operator revoke/re-enrollment is
required. Existing/uncertain state and temporary diagnostic files are never
automatically removed; preserve them privately and reconcile before changing
custody. Python memory zeroization and hostile-host isolation are not promised.

Output contains fixed outcome codes and enrollment instance/generation metadata.
Exit codes: 0 success/stopped, 2 invalid input, 3 unavailable/fenced, 4 unsafe
storage. C/P/session/enrollment secrets never appear in output or DNS/worker argv
or environments. The C/P disk format is private, bounded to 8192 bytes, and must
never be exposed as an API response.
