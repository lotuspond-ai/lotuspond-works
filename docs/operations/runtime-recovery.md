# Runtime identity recovery and incidents

E03 is the connected-installation Linux identity foundation. This runbook does
not authorize a production migration or restore. Shipped production recovery
is unavailable; record, dispatch and command-trust dependencies deny by default.
There is no automatic rollback witness or production custody provider. E09 must
qualify these integrations and each customer's backup/restore procedure.

## Provisioning and current authority

Follow the exact private wheel installation, directory, config, CA and enrollment
commands in `docs-site/docs/runtime-foundation.md`. Keep SDK 0.2.0/runtime 0.1.0
in a dedicated Linux/Python 3.13 or 3.14 environment. Provision the final TLS
hostname/CA and installation identity independently. Private directories are 0700;
config/token/state/lock/key/secret-map files are 0600 and singly linked. Safe
ancestors must belong to root/runtime user; symlinks are refused. Never put secret
values or credential-bearing URLs in argv, environment, logs, audit or model
context. Do not export private state with diagnostic bundles.

Owner/admin customer membership and existing verification/MFA gates manage scoped
connections/pools. Empty allowlists grant nothing, observations are declarations,
and the ordinary CLI's production connector list is empty. Inventory exposes
identity, status, generation and observed times, not attestation or health.
Capacity bounds (1–1000 instances, 1–32 invocations) are safety ceilings, not
licensing. Fixture process-local default gates are one child/operator and one
child/pool; distributed quotas remain open.

Rotate mounted secrets by first provisioning a new immutable private version file
and mapping, then publishing a new secret reference/connection config and pool
policy. Verify transition before revoking the old reference. Do not overwrite
published secret bytes. Current pool/connection/workspace/credential/recovery
revocation applies independently of immutable pins. Operator egress explicitly
allowlists reviewed private CIDRs; fixed TLS verifies the original hostname before
late worker-only secret resolution. Redirects, proxies, rebinding fallback,
metadata/link-local and mapped IPv6 bypasses are refused.

Provision command trust independently from package trust and obtain key identity
out of band. Keep signing custody with the operator. Snapshots are fresh for at
most sixty seconds; reviewed allowed-key transitions overlap at most five minutes.
Revoke compromised keys in current trust immediately. E03 supplies no production
trust provider or rotation scheduler. Polling can be blocked by a revoked oldest
admission until expiry. A claim closes no-effect admission; it does not execute.

## Restart and C/P recovery

1. Stop the affected supervisor. Preserve its private C/P state unchanged; do not
   copy it to another host or delete it to force takeover. Confirm the exact
   owned process group is gone. Unconfirmed cleanup fences further execution.
2. Restart through the ordinary CLI. It first retries already-active P without
   session proof; pending P still requires its original current session. Active-P
   retry preserves a later Q. Prepare/activate/cancel share ten calls/minute per
   instance. A lost response is uncertain and never extends credential expiry.
3. Wait for the previous one-minute server lease before acquisition. Session
   proof stays in memory; a copied C cannot displace a live session. Acquisition
   advances generation; rotation preserves it. Lost acquire responses also occupy
   the lease until expiry. Current identity, epoch and policy must revalidate.
4. Enrollment lasts ten minutes, C fifteen minutes, pending activation sixty
   seconds. Heartbeat normally runs every twenty seconds. Rotation starts with
   at least five minutes remaining plus bounded 0–30-second jitter. One valid but
   already-overdue startup C can rotate after fresh proof. It never accepts an
   expired credential or extends C. Server/database time decides; disagreement
   beyond thirty seconds fences authority.
5. If both C and P are unusable, explicitly revoke the instance/enrollment and
   re-enroll a replacement under a reviewed ceiling. Retain old state and
   uncertain admissions for reconciliation. Never edit token/revision/expiry or
   generation fields by hand. Exit 2 means invalid input, 3 unavailable/fenced,
   and 4 unsafe storage. A 429/503/transport failure never proves no commit.

## Fence before restore or installation restart

1. Independently disable dispatch and external sends before restore/restart.
   Stop supervisors and, where needed, revoke provider credentials/network access.
   Preserve admission and possible-send evidence. Disable is reversible; revoke
   is terminal. For compromise, revoke affected identities, enrollments and keys.
2. Establish the installation identity and current monotonic recovery epoch from
   an authority outside the database being restored. Missing witness or current
   grants means remain fenced. Changing a value in restored state is insufficient.
3. Restore only under the separately reviewed customer procedure while the
   independent authority remains fenced. Revoke old credentials, sessions and
   pending successors; invalidate old generations through qualified transitions.
   Re-enrollment may be necessary. Do not manually repair proof in database rows.
4. Revalidate customer membership/workspace, current pool policy, connector
   inventory, exact connection/secret versions, deployment/action records, command
   trust and independent business grants. A fresh epoch alone never enables writes;
   local signatures and historical immutable pins cannot revive withdrawn grants.
5. Reconcile every uncertain/in-progress effect against the downstream authority.
   Revocation cannot undo a possible send. Do not automatically retry, reverse or
   declare success. Terminal tombstones remain at least 24 hours and beyond command
   expiry plus skew; uncertain records await explicit reconciliation/retention.
6. Obtain separate production migration/recovery authorization and customer
   acceptance before releasing the fence. E03's restored machine-record fixture
   proves denial with no new effects; it is not a full customer backup/restore
   drill, automatic rollback detection or exactly-once effect guarantee.

## Schema 13 forward repair

The additive v12→v13 migration preserves environment config, evaluation Run/Runner,
registry bytes and identity/MFA data. It creates no automatic capabilities,
connections, enrollment or authority. The v12 API refuses readiness on v13. There
is no destructive downgrade: unknown/customized/partial schemas need reviewed,
data-preserving forward repair. Never run the empty-database initializer over an
installation. Future production migration requires separate authorization,
verified backup and customer-specific repair/restore evidence. Reconstructed
v2–v11 fixtures are not actual historical deployment snapshots and do not qualify
those installations. Python memory zeroization/hostile-host containment is open.

## Reproduce local installed qualification

Use only independently verified loopback `lotuspond_platform_test`. The operator
supplies the expected server directory from owned setup, never from the target's
answer. Transfer its URL through a private file:

```sh
umask 077
install -d -m 700 /tmp/lotuspond-fleet-check
install -m 600 /secure-test-setup/database-url /tmp/lotuspond-fleet-check/database-url
uv sync --frozen --project backend
backend/.venv/bin/python scripts/checks/fleet-smoke.py \
  --database-url-file /tmp/lotuspond-fleet-check/database-url \
  --expected-data-directory /operator-owned/test-postgres/data \
  --report /tmp/lotuspond-fleet-check/new-report.json
```

The report must be new. The harness verifies database name, resolved loopback,
actual server address/port and independently expected directory. It builds
noneditable SDK/API/runtime wheels outside checkout, runs isolated consumers and
binds only loopback TLS. Each schema has a unique name and random schema-COMMENT
ownership marker. Cleanup removes only that verified schema and owned processes,
confirms absence, and retains the existing server. Failed private artifacts and
receipts remain beside the safe report; upload only explicit safe report/manifests
and wheels, never private configs/tokens/keys/requests.

The ordinary CLI journey uses empty connector observations. It shortens only a
fixture C expiry while still valid to force startup recovery, discards a committed
activation response, restarts, naturally waits out the old lease and heartbeats
without extending P expiry. It does not accelerate product clocks/TTLs or prove
natural ten-minute scheduling. A distinct explicitly enrolled installed-library
worker proves signed no-effect verification/claim and a fixed child with a mounted
synthetic secret over actual approved TLS. Production settings reject the hooks.

Qualification commands set `LOTUSPOND_PLATFORM_TEST_DATABASE_URL` and independent
`LOTUSPOND_FLEET_TEST_DATA_DIRECTORY` per invocation. Required PostgreSQL cases
must execute with zero database-related skips. Preserve actual root/backend,
Python 3.13/3.14 artifact, schema, image/dependency, deployment and strict-docs
results, including failures and source/artifact/image identities. Hosted exact-head
CI, deployment and customer qualification are separate gates. E04/E05 execution,
actions, approvals and reconciliation, E08 licensing, E09 production, Studio/UI,
enterprise capabilities and the agent-first rationale remain active goals.
