# Operator authority directory

The durable execution engine needs authorities that live outside its database, so that restoring the database can never rewind them:

- the recovery epoch;
- whether this installation may execute, and its limits;
- which package publishers it trusts and which catalog documents it has admitted;
- the key that signs activity deliveries to runtimes;
- the artifact encryption key;
- optionally, the key that signs runtime write commands, and the connectors and secret bindings installed for them (reviewed actions, below).

On a single installation they come from one private directory on the host that runs the API and the execution workers. Without it the API builds no execution services, and `execution-worker` refuses to start (`engine_unavailable`). That is the behaviour before this directory existed.

This is local, single-installation tooling. It is not a hosted service, a licence or a key-management system. Back the directory up separately from the database, and keep it at least as private as the database credentials.

## Create it

```bash
python -m lotuspond.authority init /etc/lotuspond/authority --installation-id <installation-id>
```

The installation ID must equal `LOTUSPOND_FLEET_INSTALLATION_ID`. `init` refuses a directory that is not empty. It writes:

- a new activity-signing key and the trust set that runtimes pin (`activity-trust.json`);
- a new artifact key;
- recovery epoch 1;
- no trusted publisher and no admitted catalog document;
- release decisions required;
- **execution switched off.**

Every file is owner-only. A file that is readable by others, owned by another user or reached through a link is refused.

## Configure the API and workers

| Process            | Settings                                                                                                                   |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| API                | `LOTUSPOND_FLEET_ENABLED=true`, `LOTUSPOND_FLEET_INSTALLATION_ID=<id>`, `LOTUSPOND_AUTHORITY_DIR=/etc/lotuspond/authority` |
| `execution-worker` | `LOTUSPOND_EXECUTION_WORKER_AUTHORITY_DIR=/etc/lotuspond/authority`, `LOTUSPOND_EXECUTION_WORKER_INSTALLATION_ID=<id>`     |

Give each runtime a copy of `activity-trust.json` as the trust file in its activity configuration (see the runtime README).

## Decide what may run

```bash
python -m lotuspond.authority trust-publisher /etc/lotuspond/authority <ed25519-public-key-hex>
python -m lotuspond.authority admit /etc/lotuspond/authority <catalog-document-sha256> ...
python -m lotuspond.authority execution /etc/lotuspond/authority on
```

Each command prints a summary and never prints key material. The API and workers reread the files on every lookup, so a change takes effect on the next request with no restart:

- removing a publisher or switching execution off stops new work at once;
- `release-required off` lets a deployment be admitted without a release decision. Leave it on: a release of a package evaluation run binds exactly that package version (`docs/superpowers/specs/2026-11-15-package-proof-design.md`). Turning it off is a deliberate operator decision. To prove a package with it on, evaluate it on an evaluation deployment (`"purpose": "evaluation"` in the deployment's configuration): it is admitted without a release, only a package evaluation starts it, and it never writes (`docs/superpowers/specs/2026-11-17-evaluation-deployments-design.md`).

## Reviewed actions

Writes to a business system (E05) are off until you provision a command key. Without one, only interpreter v1 runs, and the action and human task routes answer 503.

1. Install each connector a workflow writes through, and each secret binding its connections reference:

   ```bash
   python -m lotuspond.authority install-connector /etc/lotuspond/authority connector.json
   python -m lotuspond.authority secret-binding /etc/lotuspond/authority erp_token 1
   ```

   `connector.json` describes one connector:

   ```json
   {
     "pin": {"name": "erp", "version": "1.0.0", "artifact_sha256": "<routes document sha256>"},
     "operations": {
       "get_order": {"effect": "read", "requires_destination": true, "requires_secret": true},
       "update_order": {"effect": "write", "requires_destination": true, "requires_secret": true}
     }
   }
   ```

   An identical description again is a no-op, and a different one under the same pin is refused. The catalog (`catalog.json`) is read when the API and workers start, so restart them after changing it. Without it nothing is installed: no connection to a connector can be configured and no write can be admitted.

2. Provision the command key, then give each runtime its trust:

   ```bash
   python -m lotuspond.authority command-key /etc/lotuspond/authority
   python -m lotuspond.authority command-trust /etc/lotuspond/authority > command-trust.json
   ```

   Install `command-trust.json` on each runtime host as a private file (owner-only, in a private directory). Name it as `actions.trust_file` in the runtime's provider configuration, beside its `https-json` write providers (see the runtime README). The runtime verifies every command against this file, which it rereads on each command. It refuses a command issued under a newer trust revision than its own.

3. Restart the API and the execution workers. With the key present they compose interpreter v2 and the action and task services. List both `lotuspond.execution-interpreter.v1` and `lotuspond.execution-interpreter.v2` in each worker's interpreter IDs.

To switch actions off, remove `command-signing.key` and restart. Each signature rereads the key and its trust, so a key removed from `command-trust.json`'s trust stops signing on the next command. Rotating the key without downtime is not built yet. To rotate, add the new key to every runtime's trust first, then replace `command-signing.key` and `command-trust.json` together and restart.

## After a database restore

```bash
python -m lotuspond.authority new-epoch /etc/lotuspond/authority
```

A new epoch holds every execution that was admitted before the restore until an owner releases it, as `docs/operations/durable-execution.md` describes.

## Each attempt's reservation

`entitlement.json` holds `demand`: what every attempt reserves of tokens, cost and active time while it runs. Once an attempt stops it is charged what it reported. The defaults are 8,000 input tokens, 1,000 output tokens, 2,000 micro-USD and 20 s, the same for every kind of attempt. They must leave room under the smallest agent limits of the packages you run. A read reserves what a model round does, a known simplification.
