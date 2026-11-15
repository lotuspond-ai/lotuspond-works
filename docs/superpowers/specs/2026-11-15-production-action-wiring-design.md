# Production action wiring

Date: 15 November 2026 (Asia/Kolkata). Status: **design, decided by Claude under the owner's delegation ("proceed to build in the order", item 7); this change builds it.** It closes the gaps [GOAL.md](../../GOAL.md) names for Control ("the production command key provider (until then the action and task routes answer 503)") and the E05 row of [CAPABILITIES.md](../../CAPABILITIES.md) ("a production command key provider and its trust; without one the action and task routes answer 503 and only interpreter v1 runs").

## What exists

- `composition.compose()` already wires interpreter v2 and the action services whenever the authorities carry a command key provider (`commands`, with `sign` and `current`). Only the test worlds give one (`FileCommandKey`, `CommandKey`, both `test_only`). The operator directory has none, so production keeps interpreter v1 and the task and action routes answer 503.
- `fleet/signing.CommandSigner` signs a runtime command with a key file and checks it against the trust snapshot. Nothing composes it.
- **Production's installed catalog is empty** (`InstalledCatalog()`). Command admission (`fleet/commands._records`), connection configurations and secret-reference registration (`fleet/management`) all look connectors and secret bindings up in it. So outside test mode no connection to a real connector can be configured and no write can be admitted, even with a key.
- **The runtime** accepts its `actions` section (the `https-json` write providers and the command trust file) only in test mode. A runtime outside test mode may not report any connector.

## Decisions

1. **The command key lives in the operator directory.** `python -m lotuspond.authority command-key DIR` creates `command-signing.key` (an Ed25519 private key, hex) and `command-trust.json` (`{"revision": 1, "trust": TrustSet}`). It refuses if a key already exists.

   `OperatorCommandKey`:
   - rereads both files on every call, as every operator authority does;
   - `current(installation_id)` checks the installation and returns a `CommandTrustSnapshot` valid for 50 s (inside E03's 60 s, by the same margin the runtime's trust uses);
   - `sign` signs only with a key the snapshot allows, by the same checks as `CommandSigner`.

   It is not test-only.
2. **Actions are switched on by provisioning, not by a flag.** `OperatorAuthorities.commands` is the key provider when `command-signing.key` exists when the API or worker starts, and `None` otherwise (today's v1-only graph, unchanged). Removing the key and restarting switches actions off. Workers that should run v2 executions list `lotuspond.execution-interpreter.v2` in their `interpreter_ids`. Each runtime pins the trust it is given: `authority command-trust DIR` prints the public `TrustSet` for the runtime's `actions.trust_file`, so the runtime is provisioned independently (E03), never by the control plane.
3. **The installed catalog lives in the operator directory.**
   - `catalog.json` lists each installed connector: its pin (name, version, artifact digest) and its operations, each with an effect (`none`, `read` or `write`) and whether it requires a destination or a secret. It also lists the secret bindings (`binding_id`, `version`) that connections may reference.
   - `authority install-connector DIR FILE` validates one connector description and adds it (an identical one is a no-op; a changed one under the same pin is refused).
   - `authority secret-binding DIR BINDING VERSION` adds a binding.
   - The catalog is read once when the API or worker starts, because `FleetDependencies` holds an immutable `InstalledCatalog`. A change needs a restart. Without the file the catalog is empty, as today.
4. **The runtime outside test mode** accepts `actions` in its operator configuration:
   - only the `https-json` write side, strictly checked as in test mode;
   - its `trust_file` is the command `TrustSet` the operator installed (`OperatorCommandTrust`): reread on every call, valid for 50 s on the server-anchored clock.

   An `activity-worker` may then report exactly the connectors its write providers serve, which is the rule test mode already enforces. Every other command still reports none.
5. **No metering.** The operator entitlement has no `bound_action`, so dispatch does not consult a price (E08 is out of scope), as for activities.

## Out of scope

- Command key rotation without a restart. The runbook gives the steps: add a new key to every runtime's trust, then replace the signing key and restart.
- A catalog of qualified connectors shipped by LotusPond; the operator installs each.
- Hosted key custody (KMS/HSM).
- The live Anthropic call, which needs a key.
