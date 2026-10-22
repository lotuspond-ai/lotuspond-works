# Durable-engine probe: findings and design implications

Date: 14 October 2026 (Asia/Kolkata). Status: approved local feasibility probe completed and independently reviewed. Current review findings: 0 Critical, 0 Important, 0 Minor; two documentation findings were corrected and independently closed. This records research, not a product dependency, runtime implementation, deployment, or production qualification.

**Superseded in part on 22 October 2026.** The [engine recommendation](#engine-recommendation) below (carry Temporal forward) is superseded by [ADR-0011](../architecture/0011-postgresql-durable-execution.md): the owner confirmed a Lotuspond-owned PostgreSQL state machine for E04. The observations, measurements, data-handling findings and receipts remain valid research evidence, and the document is otherwise unchanged.

## Customer need and scope

An agent request must survive a process restart or a long human wait. If an external write succeeds but its acknowledgement is lost, recovery must discover that effect before trying again. Revocation and current permissions must remain effective after restoring older workflow history. Those needs justify durable orchestration infrastructure; they do not prescribe a customer-facing engine console or queue-management screen. The product rationale remains [agent needs and UI boundaries](../AGENT_PRODUCT_RATIONALE.md).

The owner approved a disposable Temporal server/Python-worker probe with synthetic records, actual failure/recovery experiments, resource/history measurements and an engine recommendation. The source tree, existing datastores and customer resources were outside the probe's mutation scope. All probe code remains throwaway.

## Obtained artifacts and topology

- Temporal server 1.32.1, official Linux amd64 release binary; archive SHA256 `4260448d650c1ba7ee7683ff0c93d5c6a1709260e3a7ef543bfa34e06cff6051`.
- Temporal Python SDK 1.34.0; full experiments on CPython 3.13.13 and a real worker/client journey on 3.14.5.
- Lotuspond SDK 0.2.0 installed from the reviewed wheel, SHA256 `ed5aeaa0fd4cc417eaa4a9ba69627e5dccb7c9e000e4716397a4d2ea5e216118`. Complete public validators/binding helpers were used. This wheel's content identity is independent of the separately discovered backend dependency-declaration mismatch.
- Dedicated PostgreSQL 16.6 image `postgres@sha256:557fea37a744d5f4c8faab304b0a90858b53ab119735a88c131fd19dab802f36`, its own labeled volume and internal network. Actual database access used the inspected container address; Docker did not publish the requested localhost mapping on the internal network.
- One all-in-one server, four history shards, separate default/visibility databases on that PostgreSQL instance, one namespace with 24-hour retention, localhost server listeners, no UI, no HA, no archival, and no inter-component TLS/authentication. This was the released server with real PostgreSQL persistence, not a time-skipping or in-memory test server. It was not a production topology.
- A separate SQLite simulator held current authority, physical effect rows and audit observations. It was never restored with the engine databases. Its effects table deliberately allowed duplicate business keys.

## Observations

| Required behavior | Observed result and boundary |
| --- | --- |
| Persist waits across failures | A killed/restarted worker resumed the same run; a separately killed server plus restarted database retained that run and completed one effect after the signal. |
| Deduplicate admissions and commands | Temporal rejected repeated workflow IDs, including changed payloads. Exact payload conflict detection and signal inbox deduplication came from the probe adapter. |
| Recover an uncertain write | The target simulator committed an effect, then the worker was killed before returning its result. Retry read the existing effect and returned reconciled; one physical effect remained. |
| Recheck approval and authority | Matching approval wrote once. Changed current intent, expired records, revoked authority and a model approver were refused. Separate complete SDK checks rejected changed input with an old decision and a decision-only expiry. |
| Pause/cancel | A paused wait wrote nothing until resume. Stop at the prewrite barrier prevented a write. Cancellation during a wait and after an uncertain committed effect completed, retaining the already committed effect. Reversible pause during an already dispatched activity was not separately tested. |
| Pin versions and reject incompatible replay | Old/new definitions ran on distinct workflow types/queues and pinned synthetic hashes. The old history replayed against its implementation and rejected incompatible timer-for-activity code. Automatic fleet version enforcement and actual compiled-package execution were not tested. |
| Restore without resurrecting authority | Cold snapshots from before writes/revocation restored only engine databases. Current recovery-epoch checks refused old commands and retained one effect. The unsafe control bypassed guards and wrote a second effect despite revocation. |
| Resume under current recovery authority | Fresh epoch-2 bindings reconciled the existing allowed effect without another write; the revoked action remained denied. |
| Preserve pure contract semantics | Installed transition helpers distinguished duplicates, conflicts, gaps, uncertain effects, verified readback and cancellation. They remained synthetic supplied-state checks, not an authenticated durable event store. |

The independent review verified original/updated manifests, both installed SDK payloads, snapshot integrity, physical effect/audit records, history identities, measurements and decoded payloads. It independently replayed the old history and reproduced the incompatible-code rejection. The final simulator contained 113 physical effect rows and 487 audit rows; those counts are evidence of this finite experiment, not a workload target.

## Costs and data handling

At 1, 10 and 100 waiting executions, roughly 11.63-second samples recorded worker RSS of 82.09, 82.97 and 91.09 MiB, and server RSS of 177.54, 180.54 and 190.04 MiB. The worker accumulated no measurable CPU ticks in those idle windows; this does not mean zero cost. At 100 waits PostgreSQL Docker memory was 104.4 MiB and the two database sizes summed to 24,339,398 bytes. Docker/process memory accounting differs, and prior experiments contributed to those totals.

Sample waiting histories had four events and 6,397 exported JSON bytes. A batch of 100 trivial local effects finished in 1.292 seconds; this is not remote-tool or model throughput. Worker and uncertain-write recovery took approximately 10.06 and 16.06 seconds under the selected timeout/retry settings. Server/database recovery took 50.70 seconds including a diagnosed readiness race and manual startup retry; it is not a clean recovery benchmark. The retained final PostgreSQL directory/WAL sample was 88,544,116 bytes.

Default history payloads contained the synthetic sentinel after base64 decoding. Initial literal-text absence checks were explicitly superseded. Production design must address minimized payload references, history access, codecs/encryption, key custody, retention/deletion, backups and log redaction. Temporal's workflow sandbox is a determinism aid, not isolation for hostile customer code.

The simulator serialized authority and effect insertion in one local SQLite transaction. Real providers do not share that transaction. Remote idempotency, authorization/write races, query uncertainty, credential revocation, network isolation and trustworthy evidence require connector-specific qualification. There is no generic exactly-once external-write claim.

## Engine recommendation

Superseded on 22 October 2026 by [ADR-0011](../architecture/0011-postgresql-durable-execution.md). Retained as the recommendation made at the time.

Carry Temporal forward as the preferred candidate behind a narrow orchestration adapter. Use it for history, progression, waits, retries and replay. Lotuspond must own customer authority, exact business identity, command/event deduplication, connection permissions, action decisions, current recovery authority and verified business outcomes. An engine namespace or queue is not a tenant authorization boundary.

The existing leased evaluation worker has useful claim/reclaim patterns but is not a business workflow engine. Building from it would require owning durable multi-step progression, timers/signals, cancellation, history, version evolution and recovery. Evaluation `Run` and `EvaluationJob` remain separate from business `Execution`.

[DBOS Python 3.2.0](https://github.com/dbos-inc/dbos-transact-py/releases/tag/3.2.0), pinned source `d324156616281d7d4923bc60851617bdbb70ea5e`, is a credible alternative with a library/PostgreSQL deployment model and potentially fewer separate services. Its comparison here is official documentation/source only; it was not benchmarked or empirically rejected. Customer deployment constraints and operational staffing can reopen the choice.

MIT notices for Temporal and available dependency/distribution inventories were retained. This is not complete supply-chain, security or redistribution qualification; transitive native dependencies, interpreters, distribution notices and update policy remain release work. No production sizing, sustained load, HA, RPO/RTO or availability claim follows from this probe.

## Preservation, review and next work

The local evidence directory is `/tmp/lotuspond-engine-probe.3kakzU` in the task environment. It contains source, exact command history, failed and successful logs, histories, snapshots, dependency inventories, comparison notes and independent review. Large generated evidence and dependencies are deliberately not committed to the source repository.

| Receipt | SHA256 |
| --- | --- |
| Final detailed report | `b001786827e78caee565b25b1d616831b88a3fc8002e28d33598160b2d5c92d4` |
| Final artifact manifest, 171 entries | `bfe82c3d705abc7c2439741efde401d1b73ac45ed3a2e307bb235e7430a6b6a1` |
| Unchanged source manifest, 14 entries | `c8a5be633dd3080c25537e42842a94ae932601e2a3444a45d45edab168b6a105` |
| Independent review with documentation closure | `78a595be2df94060df2e3a2b539185de62e2ab893256275fd27a28a8caf5df09` |

The two documentation corrections aligned the final storage number with its raw receipt and required fresh ledger/barrier/PID/output state plus resource rebinding for reproduction. Original reviewed documents/manifests remain preserved. Known harness limitations also remain disclosed: reconstructed loaded source after a readiness-loop edit and one reconstructed initial worker startup line overwritten by supplemental log naming. No finding depends solely on that reconstructed line.

All probe services stopped; only the exact owned container, volume and network were removed after final dumps. Evidence and downloaded artifacts remain. Product source stayed clean at `00c7ba90520ece413470c45074ba7310dadfebc1` throughout the probe. Subsequent SDK packaging repair and E03 design work have their own source/evidence records.

The approved sequence continues with E03 connections and runtime identity, then E04/E05/E06 execution, actions and outcomes. This research introduces no product dependency or UI feature and changes no approved scope. The first customer's workflow, systems, permitted autonomy, data placement and procurement requirements remain customer decisions before external qualification.
