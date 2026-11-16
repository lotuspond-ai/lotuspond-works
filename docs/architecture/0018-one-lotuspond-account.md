# ADR-0018: One LotusPond account for Health and Works

Date: 2026-11-16. Status: **accepted. On 2026-11-16 the owner asked for one sign-up page for LotusPond Health and LotusPond Works, with single sign-on between them, then accepted this design the same day, confirmed the domain names, and opened Health in India only at first.** This record decides how sign-up, sign-in and single sign-on work across both products, on LotusPond's cloud and on a customer's own servers. [ADR-0019](0019-database-tenancy-and-placement.md) decides where each product's data lives. Nothing here is built yet.

## Context

LotusPond has two products ([ADR-0015](0015-name-the-product-lotuspond-works.md)), and today each has its own accounts.

**LotusPond Works** (this repository, Python):

- Keeps its own `users` with Argon2 password hashes.
- Uses a `lotuspond_session` cookie that is HttpOnly and `SameSite=Strict`.
- Supports authenticator (TOTP) multi-factor sign-in, and lets an organisation require it.
- Has personal and organisation workspaces with role memberships.
- Has an offline enrollment path for air-gapped installations ([ADR-0003](0003-offline-identity-and-cost-provenance.md)).
- Ships as a hosted service and as an offline bundle that a customer runs on its own servers ([ADR-0007](0007-customer-execution-distribution.md)).

**LotusPond Health** (`lotuspond-ai/lotuspond-health`, TypeScript):

- Keeps its own accounts, also with Argon2id hashes.
- Stores each account in a regional cell (India, Europe, United States, international). A cell is a separate database and file store.
- Uses a small directory, keyed by an HMAC of the email, to find which cell holds an account.
- Browsers use a session cookie; the iPhone and Android apps use a bearer token.
- Runs its own OAuth 2.1 server so AI agents can reach the Model Context Protocol (MCP) endpoint. The person chooses whose records each agent may see.

So a person who uses both products signs up twice, with two passwords. The two products also enforce different rules for the same person.

Works will serve organisations, and organisations will ask for:

- sign-in through their own identity provider (Okta, Microsoft Entra ID, Google Workspace);
- automatic provisioning and removal of their people;
- installations that never reach LotusPond's cloud.

Health holds health records, the most sensitive data LotusPond keeps. It must stay walled off from Works and from any employer.

## Decision

1. **One LotusPond account, one sign-up page.**
   - The service is **LotusPond Accounts**, at `accounts.lotuspond.ai`. Every product sends people to its sign-up and sign-in pages: `/signup?product=health` or `/signup?product=works`.
   - The page asks for only what an account needs:
     - an email address;
     - a password or passkey;
     - a name;
     - the country of residence;
     - agreement to the LotusPond account terms.
   - The page is drawn in the LotusPond design system ([ADR-0016](0016-adopt-the-lotuspond-design-system.md)). The `product` parameter changes only its words and where it returns, never what is collected.

2. **Accounts is an OpenID Connect provider, and the products sign in through it.**
   - Health and Works are relying parties: they trust the sign-ins Accounts issues.
   - They use the authorization-code flow with PKCE:
     - each product's server is a confidential client;
     - Health's iPhone and Android apps are public clients that open the system browser, following RFC 8252.
   - Products receive a few standard claims:
     - `sub`, the account's stable identifier;
     - `email` and `email_verified`;
     - `name`;
     - `region`, the account's home cell;
     - `amr`, how the person signed in;
     - `org`, set only for a managed account (decision 7).

3. **Single sign-on comes from the Accounts session; each product keeps its own session.**
   - Accounts sets a host-only, Secure, HttpOnly cookie on `accounts.lotuspond.ai`.
   - A person signed in there moves into the other product without typing anything.
   - Each product still creates its own host-only session after the callback, exactly as today. Works keeps `lotuspond_session` with `SameSite=Strict`; the products share the registrable domain `lotuspond.ai`, so the callback is a same-site request.
   - No cookie is ever set on the parent domain `.lotuspond.ai`. A Health session must not be readable by Works or by the company site. A self-hosted Works runs on the customer's own domain anyway.
   - Signing out of Accounts, or disabling an account, ends every product session through OpenID Connect back-channel logout.

4. **The account holds identity; each product holds everything else.**
   - Accounts stores:
     - the email address and whether it is verified;
     - credentials: password hash, passkeys and authenticator secrets;
     - the name;
     - the country of residence and the home region;
     - the managing organisation, if there is one.
   - Product-specific consent stays in the product, because consent must be specific:
     - Health still confirms age, records its health-data consent, and keeps family profiles, records and agent grants.
     - Works still creates organisations and memberships.
   - After sign-up, Accounts hands the person to the product's own onboarding.

5. **A wall between the products.**
   - Neither product can ask Accounts, or the other product, whether a person uses the other product. There is no cross-product data interface.
   - Accounts records which products a person has signed in to, only to show that person a "Connected products" list.
   - An organisation administrator never sees it. An administrator sees only what Works shows about that organisation's members.

6. **Accounts follows Health's data-residency cells.**
   - Accounts has the same four regional cells, chosen from the country of residence at sign-up.
   - A global directory maps an HMAC of the email to the cell, so the email itself is never stored outside the cell.
   - The `region` claim lets each product go straight to the matching cell of its own, as Health's tokens already do ([ADR-0019](0019-database-tenancy-and-placement.md)).

7. **Organisations live in Works. Enterprise sign-in is connected at the account layer.**
   - A Works organisation can own an **enterprise connection** in Accounts. A connection has:
     - one or more verified email domains;
     - an upstream identity provider, using SAML 2.0 or OpenID Connect;
     - optionally, a SCIM 2.0 directory.
   - When someone enters an address on a verified domain, Accounts sends them to the company's identity provider instead of asking for a password.
   - An account created or claimed this way is a **managed account**, and its `org` claim names the organisation.
   - When SCIM removes a person, or the connection is deleted, the managed account is disabled and its sessions end everywhere.
   - Works maps directory groups to roles.

8. **Health does not accept managed accounts.**
   - A person's employer must never be able to suspend, reset, sign in as, or audit the account that holds their health records.
   - Health asks such a person to use a personal account, and says why.
   - The same person can use both: a managed account for work and a personal account for Health.

9. **Works trusts one configured issuer, so it still runs on a customer's own servers.**
   - On LotusPond's cloud, the issuer is LotusPond Accounts.
   - In a dedicated or on-premises installation, the issuer is one of these:
     - the customer's own identity provider, directly;
     - a LotusPond Accounts instance shipped in the offline bundle;
     - for an air-gapped site with neither, Works' existing local accounts, kept as **local mode**. Local mode is limited to the operator-attested enrollment of ADR-0003.
   - Works stores each person by `(issuer, subject)`, never by email alone, so moving an organisation between issuers is a reviewed re-link rather than a guess.

10. **Multi-factor sign-in moves into Accounts.**
    - The authenticator code from Works moves to Accounts, and passkeys are added.
    - An organisation's "require multi-factor" setting becomes a check in Works: the sign-in's `amr` must show a second factor. If it does not, Works asks Accounts for a step-up sign-in using `acr_values` and `max_age`.
    - For an enterprise connection, the company's identity provider supplies the second factor, and its `amr` is passed through.

11. **Health's API lives only in Health.**
    - Health's whole API is built, deployed, versioned and documented only in `lotuspond-ai/lotuspond-health`, and served only from `health.lotuspond.ai`. That covers its endpoints, its MCP server at `/mcp`, and its OAuth 2.1 authorization for AI agents.
    - Works never hosts, proxies or calls Health's API, and holds no copy of its code or its reference documentation.
    - Accounts only signs people in. It serves no health data and no Health endpoint.
    - Works' healthcare administration workflows ([ADR-0017](0017-position-works-for-supply-chain-operations.md)) connect to a customer's own systems, never to LotusPond Health.
    - An AI agent's access is a grant to health data, chosen per profile and per kind of record, so Health keeps its own authorization server. Only its sign-in step changes: the consent page uses the Accounts session instead of Health's own password form.

12. **Built from proven parts.**
    - Accounts is LotusPond's own service for sign-up, sign-in, consent, account storage in cells, and multi-factor sign-in.
    - The OAuth 2.0 and OpenID Connect protocol is handled by **Ory Hydra**, an OpenID Certified server under Apache 2.0. Hydra stores no users and delegates login and consent to a separate app, which is exactly this split.
    - Enterprise SAML and SCIM go through **Ory Polis** (formerly BoxyHQ SAML Jackson), which is open source and self-hostable.
    - Both run in each region and inside the offline bundle.
    - A hosted identity service was not chosen: it would hold every account in the vendor's region and could not ship in an air-gapped bundle.

## Moving existing accounts

1. Both products' Argon2 hashes are imported into Accounts unchanged. Each existing account's region comes from Health's cell for Health accounts. Works accounts, which have none yet, go to `global`; the person is asked for a country at their next sign-in.
2. An address is unique in Accounts. When the same address exists in both products, the two are joined only after the person proves control of each product's account:
   - for an account whose address that product had already verified, mailbox verification is enough;
   - otherwise, the person signs in once with that product's old password.

   This prevents someone who signed up for one product with another person's unverified address from reaching that person's account in the other product.

3. Each product keeps its own user rows and adds the `(issuer, subject)` link. It drops its password column only after a reviewed, data-preserving migration and a period with both sign-in paths available.

## Consequences

- One password, one place to change it, and one place to switch on passkeys or an authenticator.
- Enterprise sign-in, provisioning and removal are built once, for every product that serves organisations.
- Accounts becomes a critical service: if it is down, nobody can sign in, although existing product sessions keep working until they expire. It needs its own monitoring, backups, regional deployment and incident plan.
- Sign-up for Health gains a redirect, and its apps change from a password form to a system-browser sign-in.
- Works' identity module splits:
  - one part becomes a relying party;
  - local mode stays for air-gapped installations;
  - multi-factor code moves into Accounts.

## Settled by the owner, 2026-11-16

The owner's words:

> Go ahead with this, at first only India is accepted for health. Use those sub domains u mentioned

> Health api should be in the health only and not in the works.

The second statement is decision 11.

1. **Domain names.** Accounts is at `accounts.lotuspond.ai`, and hosted Works at `works.lotuspond.ai`. Health stays at `health.lotuspond.ai`.
2. **Health opens in India only at first.**
   - Health sign-up through Accounts accepts only people resident in India, and their accounts and records are kept in the India cell.
   - The other cells stay in the design, so opening a region later is configuration, not a redesign.
   - A person from another country can still create a LotusPond account for Works; only Health refuses them, and says so.

## Still open

1. **Legal review.** Moving Health's account identity (not records) out of Health into Accounts changes the Health privacy policy's list of who processes account data. It should be reviewed before launch, as Health's consent text was.
2. **Order of work.** The suggested order:
   1. Accounts with password and passkey sign-in, starting with the India cell;
   2. Works as the first relying party;
   3. Health, including its apps;
   4. enterprise connections when the first organisation asks.

## References

- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html) and [Back-Channel Logout 1.0](https://openid.net/specs/openid-connect-backchannel-1_0.html)
- [RFC 8252, OAuth 2.0 for Native Apps](https://www.rfc-editor.org/rfc/rfc8252) and [RFC 7636, PKCE](https://www.rfc-editor.org/rfc/rfc7636)
- [SCIM 2.0, RFC 7644](https://www.rfc-editor.org/rfc/rfc7644)
- [Ory Hydra](https://github.com/ory/hydra) and [Ory Polis](https://github.com/ory/polis)

References checked 2026-11-16.
