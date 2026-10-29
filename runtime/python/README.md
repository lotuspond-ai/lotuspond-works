# Lotuspond runtime contracts

`lotuspond-runtime==0.2.0` requires Python >=3.13 and `lotuspond-sdk==0.3.0`.
This independently installable distribution supplies pure, strict fleet models
and domain-separated connection/pool content identities. Importing the package
or `lotuspond_runtime.protocol` performs no I/O or runtime initialization.

The package supplies a private control client, direct TLS transport, durable C/P
credential store and Linux lifecycle supervisor. It grants no execution authority.
Runtime 0.2.0 adds the durable-execution activity worker (below); production read
and model providers, connector and secret-provider integration remain separate
work, so outside test mode it refuses rather than claim work. Observations are declarations,
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
clock disagreement over 30 seconds fences the process. Heartbeats normally run
at 20-second intervals. Earlier renewal reserves the full twenty-second possible
conservative clock advance of a ten-second attempt, plus one second of loop/clock
headroom, before the unchanged rotation target. A further heartbeat inside that
window waits for rotation when the live lease can cover prepare, activation and
the next heartbeat; otherwise it renews current proof first.
Due rotation runs before a coincident heartbeat, and heartbeat completion causes
an immediate deadline check. A steady-state prepare with less than five minutes
remaining is fenced; nonzero jitter may absorb small scheduling delays while
that minimum still holds. An already-overdue credential recovered at startup
may make one immediate fresh rotation attempt after current credential/session
validation. That recovery never extends C's expiry or accepts expired proof;
success returns to normal scheduling. Shutdown uses SIGINT/SIGTERM and closes
owned transport resources.

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

Mounted-secret and destination test integration is available through the internal
`invoke_fixture` harness. The production supervisor has no fixture selector or
business invocation path. Its only connector is fixed `lotuspond-fixture` version
`1.0.0`, pinned to SHA256 of the exact packaged `fixture_connector.py` source
artifact (bounded to 65,536 bytes). Parent and child independently recheck that
identity. This is a synthetic source-artifact identity, not wheel attestation or
publisher trust, and differs from the frozen literal protocol fixture digest.

Provision mounted secrets as the worker's effective Linux user under an absolute
0700 root, with 0700 subdirectories and 0600 regular singly-linked version files.
The private 0600 mapping file also needs a 0700 parent and safe ancestors. Every
ancestor must be root/runtime-owned and cannot be group/other writable, except a
root-owned sticky temporary directory. Symlinks are refused at every component.
The mapping is operator input, never registration data, a command path or model
output. Example private mapping (synthetic names; no secret values):

```json
{"bindings":[{"binding_id":"payroll-test","version":1,"filename":"payroll/version-1"}]}
```

A scoped secret reference exposes only an opaque binding ID and integer version.
Current authority must load the binding from same-scope records and verify the
exact reference/version in the immutable connection configuration. The fixture
requires exactly one secret, one destination, and its fixed `inspect` operation.
It connects with verified TLS before opening mounted-secret paths, then requests
fresh current authority from its parent immediately before sending. It reads only
that selected version, at most 65,536 bytes, and never caches or hashes values.
Reads check the opened file and directory descriptors, ownership, mode, link count,
and file identity/size/mtime/ctime before and after reading, including replacement
of the named version file. Rotate by provisioning a new immutable version file,
new mapping metadata and new connection configuration; never overwrite a published
version in place. Root/administrator tampering between checks is a host trust
boundary. The synthetic fixture accepts only printable ASCII secret header values.

`EgressPolicy` and CA trust come from the operator and are fixed during an
invocation. Every DNS answer must pass policy before one address is selected and
pinned to the actual socket. TLS verifies the original hostname/SNI and supplied
CA. There are no proxies, redirects, connection reuse, DNS retry or reconnect.
Only explicit operator CIDRs can allow RFC1918/ULA private destinations; denied
hosts/IP ranges, metadata/link-local, mapped IPv6, reserved, unspecified, multicast
and production loopback remain refused. The internal `_for_test` constructor
permits only explicitly allowlisted loopback for local TLS tests, with all other
checks intact; no environment/config/CLI switch enables it in production. Paths
must be absolute ASCII resources with segment-prefix matching and no percent
encoding, dot segments, repeated slashes, backslashes, query or fragment. Fixed
connector code constructs method, origin and headers; inputs cannot override them.

The child starts only the fixed installed module using isolated Python, an empty
environment, closed inherited descriptors and private bounded IPC. Fleet/session
credentials are never passed. The entire IPC envelope is limited to 65,536 bytes;
metadata/base64 overhead and the bounded parser can conservatively reject an input
below its configured request-body maximum. No truncation, splitting or retry
occurs. Request/response bounds and the minimum of the configuration timeout and
30 seconds cover authorization, DNS, TLS, secret resolution, IPC and response.
Results contain only `observed`, `denied` or `uncertain` and byte counts. A failure
after a request may have been sent is uncertain and is never automatically retried.
Possible transmission begins before the parent attempts to deliver fresh send
permission; a missing later send notification cannot prove denial. Cleanup
acknowledgement failure fences future work and preserves an uncertain outcome
when sending was possible. Only a complete fixed-child pre-send denial with zero
attempted counts, no contradictory send event and successful child completion
can remain denied. Revocation cannot undo a sent external effect.

Internal parent-owned `InvocationLimits` default to one operator child and one
child per scoped pool. The process-wide gate uses the smaller operator/pool cap,
rejects reconfiguration while active, and never grants authority. Current pool
permission remains the authority dependency's responsibility. A single bounded
in-flight authority helper prevents stalled providers from accumulating threads;
a hung provider occupies that slot until it actually returns, fencing subsequent
checks. Provider cancellation is not claimed. While waiting on a sent request,
the parent checks authority at approximately 50ms intervals within the original
deadline, kills/reaps its child group on loss, and fences all future invocations
when cleanup cannot be confirmed. DNS helpers remain in that owned worker group;
standalone egress owns its separate DNS group. Process isolation bounds lifetime
and credential exposure; it provides neither hostile-host containment nor Python
memory zeroization.


Signed command polling and admission claims are internal foundations. The supervisor
checks independently supplied action records and current command trust before a
claim; production record, trust and business dispatch dependencies deny by default.
A claim closes admission only and never invokes a connector. Command keys have
separate trust from package publishers. Current trust snapshots last at most sixty
seconds; a newer revision can retain a previously allowed issuance key during an
explicit operator transition of at most five minutes, and revocation denies it.

Polling and record verification/claim are separate bounded stages. Each reserves
its full attempt budget before heartbeat and credential rotation targets. The
supervisor exposes only waiting, denied or claimed admission status and retains no
business effect loop. Authoritative deployment records must link the signed intent
to exact connection versions and secret references: SDK command bytes do not encode
those direct pins, and E03 supplies no production deployment record source.


## Durable execution activities (runtime 0.2.0)

`lotuspond-runtime activity-worker --config <file>` runs the E03 supervisor with an
activity thread that follows its live session. It polls only deliveries addressed
to that session, verifies each signed single-use delivery against independent
activity trust, claims it under a short lease and renews the lease, runs the
activity in a separate child, and reports the result as historical evidence. A
private invocation journal records each call before it starts and is the evidence
for reconciliation after a lost lease or stop; an uncertain call is never retried
blindly. A delivery the runtime cannot verify is declined, never run.

This distribution installs no production read or model provider. Outside
`mode: "test"`, or without `--activity-config`, the command exits 3 (`unavailable`)
before doing anything. In test mode `--activity-config <private file>` names a
reread trust file, a journal directory and up to eight synthetic `fixture-read` or
`fixture-model` descriptors: they exist only to qualify the protocol (Task 13's
conformance suite and installed acceptance), never to run customer work. While
idle the worker polls again after 0.2 s, backing off to 1 s.

Operator bootstrap and qualification are documented in
[`runtime-foundation.md`](../../docs-site/docs/runtime-foundation.md) and the
[recovery runbook](../../docs/operations/runtime-recovery.md). They cover private
file/CA provisioning, scoped administration, C/P response-loss recovery, current
trust, revocation, schema 13 forward repair and independent restore fencing.
The installed acceptance harness lives only under backend tests: the ordinary CLI
lifecycle and the separately authorized fixture-library/child journey have distinct
attribution. A forced valid-but-overdue startup credential qualifies recovery; it
does not establish natural wall-clock rotation or production business execution.
