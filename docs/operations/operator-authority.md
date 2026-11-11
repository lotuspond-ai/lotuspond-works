# Operator authority directory

The durable execution engine needs authorities that live outside its database, so that restoring the database can never rewind them:

- the recovery epoch;
- whether this installation may execute, and its limits;
- which package publishers it trusts and which catalog documents it has admitted;
- the key that signs activity deliveries to runtimes;
- the artifact encryption key.

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
- `release-required off` lets a deployment be admitted without a release decision. Leave it on. It exists only until the package release binding (reference workflow milestone 5) ships, and turning it off is a deliberate operator decision.

## After a database restore

```bash
python -m lotuspond.authority new-epoch /etc/lotuspond/authority
```

A new epoch holds every execution that was admitted before the restore until an owner releases it, as `docs/operations/durable-execution.md` describes.

## Each attempt's reservation

`entitlement.json` holds `demand`: what every attempt reserves of tokens, cost and active time while it runs. Once an attempt stops it is charged what it reported. The defaults are 8,000 input tokens, 1,000 output tokens, 2,000 micro-USD and 20 s, the same for every kind of attempt. They must leave room under the smallest agent limits of the packages you run. A read reserves what a model round does, a known simplification.
