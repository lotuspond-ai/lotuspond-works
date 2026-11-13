# Reviewed actions and human tasks qualification (E05 Task 12)

Approved E05 plan: `docs/superpowers/plans/2026-10-26-actions-human-tasks.md` (owner decisions of 29 October and 2 November 2026). Task 12 ran 12–13 November 2026 on branch `claude/e05-actions`.

This is local foundation evidence from Linux containers on Docker Desktop (arm64, 4 vCPUs). GitHub Actions has refused every job for this repository since 5 November (billing), so nothing here is hosted CI, a deployment, or production or customer qualification. Every provider and target is synthetic.

## What is tested, and how

E04 Task 13's harness, extended rather than replaced (the plan said "unchanged"; the edits are listed under deviations):

- one owned world per case;
- separate killable API, orchestrator, sweeper and runtime processes;
- the TCP proxy between runtimes and the API;
- real database time;
- cleanup census;
- required mode.

E05 adds only fixtures and one more owned process.

- **Action authorities, per world (`world.actions_mode`).** A world gets a command signing key and its trust, and the synthetic connector in an installed catalog. Composition then wires interpreter v2 and the action services (`composition._actions`). A world without them keeps E04's exact v1 graph, as production does today.
- **The synthetic target (`conformance/actions/target.py`).**
  - It is one more owned process serving loopback HTTPS under the world's synthetic CA, with its store outside every database cluster and backup.
  - Every applied write is one line of its own log. Write counts come only from that log; receipts are never counted as writes.
  - One-shot faults: `lose_response`, `lose_request`, `slow` and `hang_after_apply`. Read answers can be overridden per record (`reads.json`), and tier 3 adds latency (`latency.json`).
- **Runtimes.** Installed `lotuspond_runtime` processes in test mode, serving the target through the synthetic write and read providers over E03's `DestinationTransport`.
- **Reviewers.** People registered through the API (real sessions), made members of the tenant by operator provisioning. They act only through the public task routes.
- **Rules that decide.** The shared connector fixture's `present` rules test only that `/present` exists, so every read-back matched both its effect and its absence rules (undecidable), and any verification read passed. Conformance uses `driver.decisive_connector()`: `/present` true means present, false absent. Its verification rule is `eq true`.
- **Patient attempts.** Each attempt reserves 60 s of active time (E04's pattern), and the packages allow 1,000 s in all, so read-backs on a loaded host fit their budgets.
- **Fault injection.** Rollback before or after a commit gains E05's points:
  - in the API, by route: `decision`, `dispatch` (the poll that admits a command), `action_claim` and `receipt`;
  - in the orchestrator, by the rows a commit inserts: `intent`, `readback` and `outcome`.

## Cases

| Plan cases | File | Cases |
| --- | --- | --- |
| Spike 1–7 (tier 1), spike 15 | `actions/test_spike_writes.py` | 2 |
| Spike 16–22, ACT-8 to ACT-11 | `actions/test_reconciliation.py` | 7 |
| ACT-1 to ACT-7 | `actions/test_reviews.py` | 7 |
| Spike 26, 28, 34 | `actions/test_controls.py` | 3 |
| REVA-1 to REVA-5, spike 35 | `actions/test_revocation.py` | 6 |
| DISCA-1 to DISCA-3 | `actions/test_disconnection.py` | 3 |
| TEN-A1 to TEN-A4 | `actions/test_isolation.py` | 4 |
| PRIV-1 | `actions/test_privacy.py` | 1 |
| RESTORE-1 to RESTORE-5 (RESTORE-2 present and absent), spike 1–7 (tier 2) | `disruptive/test_action_restore.py` | 7 |

Installed acceptance (`scripts/checks/execution-smoke.py`) adds, per artifact variant:

- `reviewed_write_result_and_counts`;
- `rollback_action_{point}_{before,after}_commit` for the seven points above.

The report is refused unless at most one target write per intent was observed (`max_target_writes_per_intent`).

## Runs

All on the final code unless noted. The Linux jobs ran in the `ci.yml` jobs' own steps inside the local runner image, one job at a time, with the Mac held awake.

| Run | Where | Result |
| --- | --- | --- |
| Tier 1, the 33 action cases | Linux container, required mode | 33 passed (9 min 31 s) |
| Tier 1, every case (E04's and E05's) | Linux container, required mode | 78 of 80 passed (42 min); the two failures are below |
| Installed acceptance, both artifact variants | Linux container | passed (3 min 36 s), at most one target write per intent |
| Tier 2 (E04's disruptive cases and the seven above) | Linux container | 13 passed (3 min 9 s) |
| Tier 3, planning target | Linux container, 4 vCPUs | failed its throughput criteria; both write-safety criteria passed (below) |
| Runtime suite | Linux container, Python 3.13 and 3.14 | 802 passed on each |
| Backend action tests (`tests/execution/actions`) | macOS, local PostgreSQL | 154 passed |
| Backend execution and fleet tests | macOS | 1,134 passed; the 8 failures are this Mac's known fleet baseline, unchanged |

**The two tier 1 failures of the full run.**

- `test_reference_triage.py::test_the_prefix_runs_every_scenario_on_the_real_providers` fails the same way at this branch's HEAD before Task 12 (`timeout_before_dispatch` on its third step), so Task 12 did not cause it. It is not fixed here.
- The installed acceptance failed in that run (registration rate limits and one rollback wait). It was fixed afterwards and passes alone (row above).

**Tier 3.** This was the `load` job as `ci.yml` runs it: 5 admissions/s for 60 minutes, a 5-minute burst and a 10-minute recovery, 10,000 waiting executions, 8 environments with 3 runtimes each, one API worker. One in five admissions was a reviewed write: a person reviews it after an exponential wait (mean 30 s), and the target answers with log-normal latency.

| Criterion | Observed | Passed |
| --- | --- | --- |
| At most one target write per intent | at most 1 over 877 writes | yes |
| At most one provider call per attempt | at most 1 over 2,661 calls, none unattributed | yes |
| Admission p95 below 1 s | 2.71 s | no |
| Runnable queue age p95 below 30 s | 1,594 s | no |
| No sustained backlog growth (final 30 min) | grew by about 1,490 | no |
| Burst backlog recovers within 10 min | did not recover | no |
| Revocation wait p95 1 s, max 5 s | p95 1.38 s, max 2.84 s | no |

The one API worker was CPU-bound for the whole steady phase: 3,537 CPU-seconds in 3,600 s. Of the API CPU attributed to service calls, E05's routes took about a quarter. The largest are the action poll (184 s), task decisions (110 s), action claims (77 s, 82 ms each), and receipts and record fetches (54 s each, about 59 ms each).

The host was at its usual speed: `store_activity_value`, whose path Task 12 does not touch, took 9.38 ms of CPU at p50, against 9.11 ms in the 3 November calibration.

E04's own tier 3 already failed the backlog criteria at 5/s on this VM (PR #27's runs, which used three API workers and no waiting executions). Admission and revocation passed in those runs and failed in this one. No same-configuration E04 control was run, so how much of that regression comes from E05's calls and how much from the CI configuration's single API worker is not separated here.

Of 4,842 reviewed writes, 3,939 had a decision but no runtime had claimed them by the end. Decision to claim took 20 minutes at p50 (claimed ones only). The reviewed path queued behind the saturated API like everything else; no write ran twice.

## Product changes this task required

Each was found by a case failing on real processes; none is inferred.

1. **No real runtime could receive an action command.** E03 refuses a command whose connector is not in the instance's observation ceiling. The runtime CLI refused any configuration that reported a connector, a guard from before any executable connector existed. Now a test-mode runtime may report the synthetic connector its action providers serve, and its worker refuses to start reporting one they don't. Production mode still refuses all (`runtime/python/src/lotuspond_runtime/cli.py`).
2. **A real runtime could not read back.** Test mode's activity configuration did not accept the synthetic target's read provider, so read-backs and verification reads never ran (`activity_fixture.py`).
3. **The runtime's test command trust refused itself under load.** `FixtureCommandTrust` stamped each snapshot valid until its own clock reading plus 60 s. The worker validates it against a `now` it read a moment earlier, and `validate_snapshot` requires `valid_until <= now + 60 s`. Any clock movement between the two readings made the snapshot read as too long-lived: `trust_unavailable`, then a write stopped `not_started` before its provider ran. It is test-only code, but it made unrelated cases fail at random under load. Both test providers now give 50 s. Found by test-mode notes the worker now writes beside its synthetic provider's call counter: why a write stopped before its provider ran, as a closed code.
4. **An unclaimed dispatch that timed out hid its cause (decision 7; ACT-4, ACT-6).** Such a step always failed `timeout_before_dispatch`, reported as `provider_unavailable`. Now its authority is rechecked as a new dispatch would be:
   - a reviewer who has since lost the eligible role fails it `approval_revoked`;
   - an expired intent fails it `approval_expired`;
   - otherwise, when the dispatch step's retry policy lists `timeout_before_dispatch`, one more command is sent under the same intent (ACT-11's "another runtime may take the retry within bounds"). A command that expired unclaimed provably ran nowhere, since E03 refuses its claim from then on.

   `approval_revoked` joined the failure vocabulary; it had a column value but no producer. The cause replaces `timeout_before_dispatch` even when no retry would follow: ACT-4 and ACT-6 require exactly those codes.
5. **A claimed write's receipt was refused after its initiator lost `execute` (REVA-5).** E03's status report rebuilds the command's records under current authority. A receipt or stop of a write already claimed now records what happened even if the initiator's `execute`, the deployment revision or the entitlement has since changed. Its integrity, epoch, recipient, expiry and connection still decide: E03 refuses any report of a command past its expiry (which the intent's expiry bounds), and a connection or pool revoked after the claim still refuses it; such a write lapses to a read-back. Fetching records to start a write, a claim and a renewal keep every check.
6. **Composition gained interpreter v2 and the action services,** only over a command key provider. Production has none yet, so its graph and its 503 for the action routes are unchanged.
7. **The least-privilege worker grants lacked E05's tables.** E04's tier 2 ENG-4 case failed: a sweeper under the operations guide's grants refused to start, because the sweeper reads E05's tables since Tasks 5–8. The grants were measured over Task 12's tier 1 action cases with E04's privilege recorder and added to `docs/operations/durable-execution.md`. ENG-4 then passed.

## Deviations from the plan's wording

- **The harness was extended, not left unchanged.** `world.py` (action authorities, command key, synthetic catalog), `harness.py` (composition over them, E05 rollback points, service-call and lookup attribution), `processes.py` (the target process, the runtime's action configuration, `Hand`'s action client), `driver.py` (the decisive connector, reviewed-action packages, members with real sessions, connection writes) and `journey.py` (the reviewed write and its rollbacks). E04's own cases run unchanged on it: tier 1's E04 cases and tier 2 pass.
- **ACT-6 depends on timing.** Its demotion must land before the dispatch attempt is created; if the attempt came first the poll would refuse it `forbidden` and the reason would differ. It passed in every recorded run.
- **ACT-10** runs in its own environment: a deployment refuses a connection that allows a write its package never makes, so the `submit_once` (no `target_key`) write has its own connection.

- **ACT-12 (reviewer without source access)** has no real-process form. Every proposal value is stored under the workspace response policy, never a source pin, so withdrawing source access cannot reach a proposal. Only Task 5's unit test, which forces the refusal, covers it. Open for the owner: propagate source pins into intent values, or restate ACT-12.
- **RESTORE-2 (present).** The plan expects a restored intent whose write happened after the backup to end `observed`. As built in Task 10, an intent the restored rows never dispatched fails `recovery_epoch_changed` even when its effect reads back present, because those rows lack the decision and dispatch that wrote it. Its write is read back and never resent. Open for the owner, as recorded at Task 10.
- **A lapsed write keeps its slot until its stop is confirmed.** Spike 20–22, DISCA-1 and REVA-2 read the effect back and then wait, at the deployment's one active slot, for the write's compute stop. A runtime restarted after a kill or a cut-off reports its journaled write only after its previous session lease, beyond these cases' bounds, and a revoked one never does. So the cases confirm the stop as the runbook says, an owner's `compute_stopped` resolution, and verification then runs.
- **Spike 18–19** gives the dispatch step a second attempt; the plan's resend needs "retry attempts left".
- **REVA-1.** With its connection revoked, no runtime of that pool can authenticate, so the approved write waits reserved with reason `authority_revoked` and no command; the case cancels it before restoring the connection.
- **REVA-3** accepts `authority_unavailable` for a claim under a pool policy with no connection.
- **Receipt and stop paths.** The runtime routes keep runtime 0.3.0's `/actions/receipts` and `/actions/stops` (Task 11).

## Observed limits

- **Local, not hosted.** These are Linux containers on one Mac's Docker VM. Hosted CI is billing-blocked, and nothing here was deployed.
- **Synthetic target and connector.** No real system of record was written. The write side of `https-json` is not built yet.
- **Tier 3 throughput fails** at the planning target on this host (above). Its safety criteria hold. The API's per-call CPU for the action routes (claim 82 ms, receipt and record fetch about 59 ms) is the first place to look before any capacity claim.
- **The lookup slot.** It was suspected while the random `not_started` writes were unexplained, but the measurements ruled it out: in tier 3, action calls waited for it 10–16 ms at p50 and at most 236 ms, and none was refused.
- **Diagnostics left in test mode.** The runtime's test-mode notes of why a write stopped before its provider ran (product change 3) stay. They are written only in test mode, beside the synthetic provider's counter.
- **Open for the owner:** ACT-12's restatement, RESTORE-2 (present), and whether a lapsed write should keep its slot until its stop is confirmed (all under deviations).
