# Offline identity enrollment and trace cost provenance

Status: implemented for local validation. Date: 2026-09-21.

## Requirements

An isolated installation must be able to enroll a new person without an email service. The operator must deliberately grant organization ownership and platform administration. Enrollment must never turn an unverified email address into a verified mailbox. Existing accounts and data must survive upgrades unchanged.

Trace ingestion must distinguish a reported zero from an unavailable cost. Existing numeric values have uncertain measurement provenance and must remain available without being counted as known spend.

## Enrollment decision

The database stores an operator attestation separately from the user and from a hashed, expiring enrollment token. A privileged installation operator records an identity-check method and references to the check and operator. These references are pointers to the operator's evidence system, not copies of identity documents. The application records the claim; it does not independently validate the evidence or certify an identity assurance level.

```mermaid
sequenceDiagram
    participant Operator
    participant CLI
    participant Database
    participant Person
    participant API
    Operator->>CLI: Explicit new person, attestation, optional organization/admin
    CLI->>Database: Pending account + personal workspace + attestation + token digest
    CLI-->>Operator: Enrollment link in a new private file
    Operator-->>Person: Deliver through an independently trusted channel
    Person->>API: Inspect token; choose own password
    API->>Database: Lock, revalidate, consume token, set password, audit
    API-->>Person: Enrollment complete; sign in
    Person->>API: Normal password login
    API->>Database: Check active account and completed enrollment
```

The CLI refuses to overwrite an existing email address. Organization creation and projects require explicit arguments; personal workspace creation remains mandatory. The placeholder password is a salted Argon2 hash of a discarded random value. Pending accounts cannot authenticate through passwords, sessions, or attributed API keys.

Changing the installation to connected mode does not make an attested account's email a recovery authority. Offline-origin accounts require completed enrollment and actual mailbox verification before mail-based password recovery can be issued or consumed. The recovery endpoint keeps its generic acknowledgement for ineligible accounts. Ordinary public-signup recovery retains its separate contract.

Enrollment endpoints run only in airgap mode with mail disabled. They use the same body bounds, origin checks and distributed production rate limits as the other identity endpoints. Tokens appear only in a URL fragment, are removed before the UI mounts, and are retained in browser memory only. Inspection does not consume a token. Completion checks expiry, revocation, account suspension, and the active matching attestation under locks, and consumes the token once. It clears prior sessions and attributed API keys instead of activating credentials that might already exist for the pending account. It does not sign in the user. An explicit operator reissue action invalidates prior pending links and refuses already enrolled accounts.

Production organization and operator access accept either mailbox verification or an active operator attestation linked to completed enrollment. The API exposes the source separately from `email_verified`, which remains false for this flow. Invitations addressed to an email still require ownership of that mailbox. Direct membership administration can use an operator-attested identity. The exact-identity revocation CLI disables the user, revokes pending links and attestations, deletes sessions, revokes attributed keys and retains business evidence. An expired or revoked attestation ceases to authorize organization/operator access and attributed organization keys.

## Storage and concurrency

`offline_identity_attestations` records the user, check source, references, creation/expiry/revocation timestamps. `offline_enrollments` records the user, matching attestation, unique token digest, expiry, consumption and revocation. A composite foreign key prevents linking another user's attestation. User-row serialization and token state checks prevent competing redemptions from replacing the password. Audit rows retain the provisioning and enrollment decisions without credentials.

Provisioning commits only after its exclusive mode-0600 token file has been written. An ordinary failure removes the newly written file and rolls back the transaction. Filesystem and database commits are not atomic across process or machine failure: an interrupted command requires the operator to check the account and reissue a pending enrollment. No destructive rollback or automatic replacement of an existing identity is permitted.

## Cost decision

`traces.cost_usd` becomes nullable. `cost_measurement` is one of `reported`, `unavailable`, or `unknown` and is derived by the server. A supplied finite, bounded amount, including zero, is reported; an omitted/null amount is unavailable. A schema upgrade preserves all old amounts and labels them unknown. The system never infers a historical zero was measured.

Overview cost is a subtotal of reported amounts accompanied by measurement counts. A missing measured subtotal remains null. Unavailable and unknown values cannot silently become zero or a complete total. Reported values remain producer assertions, not independently reconciled billing. Run-result release gates and the gateway usage ledger retain their separate contracts.

## Limits and later work

The operator with database and CLI access is trusted to attest identities and grant administration. This is not federated identity, MFA, an automated proofing service, or a guarantee that a claimed email belongs to the person. Secure delivery, evidence retention, TLS, workstation control and revocation procedures belong to the installation operator. Forgotten passwords for an already enrolled airgap account require a separate reviewed recovery workflow; pending-link reissue is not password recovery.

As enrollment volume grows, replace one-off CLI handoffs with an audited identity-provider integration and add MFA before broader privileged rollout. Keep the separate attestation and mailbox semantics. Large trace volumes will require partitioning and retention policies; preserve the measurement markers in exports and aggregates.

## References

- [OWASP Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html): informs token expiry, single use, secure handling and normal sign-in after a password is established.
- [NIST SP 800-63A-4](https://pages.nist.gov/800-63-4/sp800-63a.html): distinguishes identity proofing from enrollment and authenticator binding. This implementation does not claim NIST conformance or an identity assurance level.

References checked 2026-09-21. Local test evidence belongs in `docs/VALIDATION.md`.
