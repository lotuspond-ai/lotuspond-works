# Runtime identity foundation

LotusPond Works has a separately installable Linux runtime identity client, scoped
connection metadata and process-session contracts. The opt-in API is for customer
owners and administrators. The current CLI registers no production connector, and
its durable-execution activity worker runs only synthetic test-mode providers (see
below). Test-only authorities qualify the foundation;
production recovery, dispatch, deployment-record and command-trust providers
remain unavailable or deny by default. Enabling the fleet feature alone cannot
authorize work. Existing evaluation runners remain a separate capability.

## Bootstrap on a Linux host

Obtain reviewed `lotuspond-sdk==0.3.0` and `lotuspond-runtime==0.2.0` wheels and
their independent artifact digests through your operator's private distribution.
No public package release is implied. Use Python 3.13 or 3.14 and install the
wheels in a dedicated virtual environment. Provision the control server's CA and
installation ID through an independent trusted channel. Use the final TLS origin
without a path, query, credentials or trailing slash; redirects and ambient
proxies are refused. Certificates must match the original hostname. TLS
verification cannot be disabled.

Run the following as the intended runtime user, replacing the synthetic paths
and origin after review. Keep the private directory out of backup exports,
terminal transcripts, telemetry and shared mounts. Ancestors must belong to root
or that user and cannot be writable by other users, except a root-owned sticky
temporary directory. Symlinks and multiply linked private files are refused.

```sh
umask 077
install -d -m 700 /home/runtime/lotuspond-private
install -d -m 700 /home/runtime/lotuspond-private/state
python3.14 -m venv /home/runtime/lotuspond-private/venv
/home/runtime/lotuspond-private/venv/bin/python -m pip install \
  /reviewed/lotuspond_sdk-0.3.0-py3-none-any.whl \
  /reviewed/lotuspond_runtime-0.2.0-py3-none-any.whl
install -m 600 /trusted/control-ca.pem /home/runtime/lotuspond-private/control-ca.pem
cat > /home/runtime/lotuspond-private/runtime.json <<'JSON'
{
  "origin": "https://control.example.invalid",
  "ca_file": "/home/runtime/lotuspond-private/control-ca.pem",
  "state_directory": "/home/runtime/lotuspond-private/state",
  "installation_id": "installation-from-operator",
  "observation": {"platform": "linux", "connectors": []}
}
JSON
```

The owner/admin creates a pool and publishes a policy using the scoped API:
`/api/v1/projects/{project_id}/environments/{environment_id}/runtime-pools`.
Workspace comes from the authenticated browser session, with the existing
verification/MFA requirements. Non-secret management mutations require a fresh
`Idempotency-Key`; retries with the same canonical request return the safe result,
changed content conflicts. Runtime credentials and API keys cannot administer the
fleet. Empty connection/connector allowlists grant no capability.

Issue a one-use enrollment with the exact Linux observation ceiling at
`.../runtime-pools/{pool_id}/enrollments`. Transfer the one-time response token
into a private 0600 file through the operator's secure provisioning channel,
without placing the response in shell history, argv, environment or logs. The
following reads that file; `--token-file -` also accepts bounded non-TTY stdin.

```sh
install -m 600 /secure-transfer/enrollment /home/runtime/lotuspond-private/enrollment
/home/runtime/lotuspond-private/venv/bin/lotuspond-runtime enroll \
  --config /home/runtime/lotuspond-private/runtime.json \
  --token-file /home/runtime/lotuspond-private/enrollment
/home/runtime/lotuspond-private/venv/bin/lotuspond-runtime run \
  --config /home/runtime/lotuspond-private/runtime.json
```

Remove the consumed provisioning copy through your custody policy. Preserve
uncertain runtime state privately until reconciliation. The runtime persists C
and optional P credentials in private atomic state; its separate session secret
stays only in supervisor memory. A copied C cannot replace an unexpired process
session. Enrollment lasts ten minutes, active credentials fifteen minutes, pending
activation sixty seconds, and process leases one minute. Heartbeats normally run
every twenty seconds. Rotation begins with at least five minutes remaining and
0–30 seconds of bounded jitter. Expiry uses server/database time; disagreement
beyond thirty seconds fences authority. Exit codes are 0 stopped/success,
2 invalid input, 3 unavailable/fenced and 4 unsafe storage.

## Durable execution activities

Runtime 0.2.0 adds the runtime side of durable execution (E04). A runtime polls for
signed, single-use deliveries addressed to its own session, claims each under a
short lease that it renews, runs the activity in a separate child, and reports the
result as historical evidence. A private journal records each invocation before it
starts, so a lost or stopped runtime is reconciled from evidence instead of being
retried blindly. A delivery the runtime cannot verify is declined, never run.

This release ships no production read or model provider.
`lotuspond-runtime activity-worker` refuses outside test mode (exit 3,
`unavailable`); only test mode runs the packaged synthetic providers that
qualify the protocol. The control plane's orchestrator and sweeper run from the
API image as `execution-worker --role orchestrator|sweeper`; see the operator guide
`docs/operations/durable-execution.md` for grants, provisioning, restore and
runbooks.

## Operations and capability limits

Inventory gives safe scoped identities, status, generations and observed times.
It is neither attestation nor a health score. Capacity policy bounds are
1–1000 instances per pool and 1–32 concurrent invocations; they are safety bounds,
not commercial license entitlements. The fixture's default process-local gate is
one child/operator and one child/pool, with no distributed capacity claim. API
limits per minute are 60 enrollment issuances per pool, 20 enrollment exchanges
per peer and 5 per token digest, 10 session acquisitions and 10 heartbeats per
instance, 10 prepare/activate/cancel calls in one shared rotation bucket per
instance, and 60 polls per instance. Claims and self reads have no additional
fleet-specific limiter. Rate-limit or dependency
failure stops the CLI; supervise restart under an operator policy rather than
assuming a mutation failed. Multi-process production limiter qualification is open.

Publish new immutable connection versions for connector/operation/destination or
secret-reference changes. Publish a new current pool policy to change capability;
historical revisions and immutable pins remain. Provision new mounted secret
version files and mapping metadata before a new reference/config version; never
rewrite published secret bytes in place. Revoke obsolete references after
consumers transition. Terminal revocation requires new records/re-enrollment.
Disable is reversible; revoke is terminal. Current connection, pool, workspace,
credential, session and recovery authority always apply independently of pins.

Private-network egress requires operator-reviewed explicit CIDRs. Every resolved
address must pass policy before one approved socket is pinned; TLS then verifies
the original host before late worker-only secret resolution. No redirect,
proxy, rebinding fallback, mapped IPv6 or metadata/link-local destination bypass
is provided. Command/engine/package inputs contain references only. The synthetic
child's explicit loopback exception exists only in a test constructor, with no
production CLI switch. Python memory zeroization and hostile-host isolation are
not promised.

Provision independent command-key trust separately from package publisher trust.
Verify the signing key's identity out of band; keep signing custody on the
operator side. Snapshots are fresh for at most sixty seconds, and a reviewed key
transition may overlap allowed keys for at most five minutes. Revoke compromised
keys in current trust immediately. E03 supplies no production trust provider or
automatic key-rotation scheduler. A command claim is an internal no-effect
admission, never business permission. A revoked oldest queued admission can deny
polling until its expiry, delaying later valid work. Uncertain admissions remain
for explicit reconciliation. Terminal tombstones survive at least 24 hours and
command expiry plus permitted skew. Exactly-once external effects are not claimed.

Recovery must fence before restore/restart, establish an independent current epoch
and grants, revoke stale proof and reconcile uncertain effects before re-enabling
anything. A new epoch alone never authorizes writes. The repository operator
runbook is `docs/operations/runtime-recovery.md`; production recovery custody,
rollback detection, deployment records, business actions/approvals/reconciliation,
licensing, production qualification and Studio/UI remain separate work.

Local artifact qualification runs the ordinary CLI against a real TLS API and
PostgreSQL, including response-loss recovery and a natural lost-lease wait. It
forces an already-overdue but valid startup credential in an owned fixture schema;
it does not claim natural ten-minute wall-clock renewal. A distinct explicitly
authorized installed-library fixture proves signed no-effect verification/claim
and fixed-child secret/TLS behavior. Neither path establishes production agent
execution or customer acceptance.
