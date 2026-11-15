# ADR-0019: Databases, tenancy and placement

Date: 2026-11-16. Status: **proposed. On 2026-11-16 the owner asked how the databases should be laid out, given that LotusPond Works will serve organisations and will later need on-premises or enterprise-only privacy.** This record decides where each product's data lives and how one Works organisation can move to stricter isolation later. [ADR-0018](0018-one-lotuspond-account.md) decides the shared account. Nothing here is built yet.

## Context

**Works** has one PostgreSQL database per installation.

- Every tenant row carries `workspace_id`.
- Foreign keys are composite on `(workspace_id, …)`, so one workspace's row cannot point at another's ([DATABASE.md](../DATABASE.md)).
- A workspace is either personal or an organisation.
- The runtime that runs a customer's workflows already runs inside the customer's boundary, in runtime pools.
- The whole service also ships as an offline bundle with its own PostgreSQL.

**Health** keeps each region's personal data in a cell: its own database and file store.

- A default database holds only non-personal data: the directory, partner labs and the facility cache.

Enterprise customers will ask for some or all of these:

- their data in one region;
- their own database, or their own deployment;
- their own encryption keys;
- LotusPond staff kept out unless they approve;
- an installation on their own servers with no connection to LotusPond.

The layout must allow each of these later without rewriting the product.

## Decision

### 1. Separate databases, never shared across products

There are three kinds of database. No database is shared between them, and no product reads another's tables.

| Database     | Holds                                                                                      | Layout                                                                 |
| ------------ | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| **Accounts** | Identity only: emails, credentials, multi-factor secrets, sessions, enterprise connections | A global directory (non-personal), plus one cell per region            |
| **Health**   | Health records, profiles, consents, agent grants, files                                    | Unchanged: one cell per region, plus the non-personal default database |
| **Works**    | Organisations, workspaces, projects, packages, deployments, runs, audit                    | Per placement (section 3)                                              |

**Accounts:**

- The **directory** holds only:
  - an HMAC of each email, mapped to its region;
  - OpenID Connect client registrations;
  - each enterprise connection's verified domains, its identity-provider metadata, and the Works organisation that owns it.
- Each **account cell** holds that region's accounts, credentials and sign-in sessions, and its audit of sign-ins.
- Health's own email directory moves into Accounts. Health then finds the cell from the token's `region` claim instead.

### 2. Health stays as it is

- Health's `users` table becomes its account row, keyed by the Accounts subject.
- The password column goes once every account has moved (ADR-0018).
- Records never leave the cell, and no Works database ever holds Health data.
- Healthcare administration workflows in Works ([ADR-0017](0017-position-works-for-supply-chain-operations.md)) handle an organisation's own data in Works. They are not a path into LotusPond Health.

### 3. Every Works organisation has a placement

A placement says where an organisation's data lives. A **region** is chosen when the organisation is created: India, Europe or the United States. A personal workspace follows its owner's region. There are four placements, from cheapest to most isolated.

| Placement                | Database                                                                               | Application                                                                                    | For                                                             |
| ------------------------ | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **Shared**               | A shared PostgreSQL in the region                                                      | LotusPond's regional service                                                                   | Personal workspaces and most organisations                      |
| **Dedicated database**   | The organisation's own PostgreSQL database in the region, with its own backups and key | LotusPond's regional service                                                                   | Organisations that want their data separate, or their own key   |
| **Dedicated deployment** | The organisation's own database                                                        | Its own copy of the service, in the region or cloud account it chooses                         | Organisations that want no shared compute, or a private network |
| **Self-hosted**          | The organisation's own PostgreSQL                                                      | The offline bundle on its own servers, with its own identity provider or local mode (ADR-0018) | On-premises and air-gapped sites                                |

- On the cloud, a small **tenant directory** maps each organisation to its region, placement and database connection. The service routes each request through it, the way Health routes by cell. A self-hosted installation needs no directory: it serves only its own organisations.
- The schema is the same in every placement, and so is the migration chain. A dedicated database is simply a database with one organisation in it.
- **Not chosen:** a schema per tenant. It multiplies every migration by the number of tenants and isolates no better than a dedicated database.

### 4. The shared placement gets a second wall

In the shared placement, PostgreSQL row-level security is added under the existing workspace predicates and composite keys.

- Each request sets the workspace on its connection with `SET LOCAL`.
- Every tenant table has a policy that compares `workspace_id` with that setting.
- A query that forgets its predicate then returns nothing instead of another organisation's rows.
- The migration role and operator tools bypass the policy explicitly, and their use is audited.

### 5. Rules that keep an organisation movable

Moving an organisation to a stricter placement must be a copy, not a rewrite. So:

1. Every tenant row carries `workspace_id`, and no foreign key crosses workspaces. This is already true.
2. Identifiers are random UUIDs, never shared sequences, so rows from different databases never collide.
3. Nothing outside the tenant directory and published catalogues is shared between organisations. A package published for one organisation is copied, never referenced, as the registry already does.
4. Files and secrets are addressed by workspace, so they move with the rows.
5. An **organisation export and import** tool copies one organisation, with its files and audit, into another placement:
   - it checks row counts and hashes;
   - it switches the tenant directory;
   - it keeps the source read-only until the owner approves deletion.

### 6. Keys belong to the organisation

Sensitive columns and files use envelope encryption with a data key per organisation. Each data key is wrapped by a root key held in the region's key management service.

Works already encrypts mail and authenticator secrets with operator keys; this generalises that.

Bringing the customer's own key later then only replaces the wrapping key:

- in a dedicated placement, the root key can live in the customer's own key service;
- revoking it makes their data unreadable to LotusPond.

### 7. Enterprise privacy, mapped to placements

| Need                                                     | Shared         | Dedicated database | Dedicated deployment | Self-hosted                     |
| -------------------------------------------------------- | -------------- | ------------------ | -------------------- | ------------------------------- |
| Data kept in one region                                  | yes            | yes                | yes                  | the customer's choice           |
| Own backups and restore                                  | no, per region | yes                | yes                  | the customer's                  |
| Own encryption key                                       | no             | yes                | yes                  | the customer's                  |
| Enterprise SSO and SCIM (ADR-0018)                       | yes            | yes                | yes                  | yes, against their own provider |
| Audit export                                             | yes            | yes                | yes                  | yes                             |
| Retention set per organisation                           | yes            | yes                | yes                  | yes                             |
| No shared compute, private network                       | no             | no                 | yes                  | yes                             |
| Staff access only with the customer's approval, recorded | yes            | yes                | yes                  | not applicable                  |

A **support-access approval** applies wherever LotusPond runs the service. A LotusPond operator reaches an organisation's data only through a time-limited grant that the organisation approves. Each use is recorded in that organisation's audit.

## Consequences

- An organisation can start in the shared placement and move up when it asks, with no product change.
- Running cells and placements costs more operations work: one migration run per database, backups per placement, and monitoring per region. The migration tooling must apply the same reviewed chain to every database it is given and stop at the first failure, as Health's cell migrations already do.
- Row-level security needs care with connection pooling: the setting must be per transaction, never per session.
- The organisation export and import tool becomes the main way to deliver isolation, and it needs its own tests and restore drills.

## Open questions for the owner

1. **Which regions hosted Works opens first.** India only, or India and Europe together?
2. **Prices and thresholds** for the dedicated placements. This record decides only that they exist.
3. **Whether the first enterprise customer** needs a dedicated or self-hosted placement on day one. If so, export and import come before row-level security in the order of work.
