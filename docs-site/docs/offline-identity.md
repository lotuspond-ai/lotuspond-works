# Offline account enrollment

An air-gapped installation can provision a new person through an installation operator. This records an operator's identity attestation; it does not verify email ownership or perform identity proofing automatically.

## Before provisioning

Use the reviewed application release with the matching initialized schema, an internal HTTPS origin, Redis-backed rate limits, `LOTUSPOND_NETWORK_MODE=airgap`, and `LOTUSPOND_MAIL_ENABLED=false`. Keep public registration disabled unless your onboarding policy explicitly requires it. The CLI uses the installation's configured database and is a privileged operation. Confirm the installation and account before executing it; never reuse another project's environment.

Verify the person's identity using your organization's approved process. Record a reference to that check and to the responsible operator. Do not put identity-document numbers, scans, credentials, or unnecessary personal information in the references. Deliver the enrollment link through a channel that independently reaches the verified person.

## Provision a new account

Run the CLI inside the reviewed API runtime with its authorized configuration. This example deliberately creates an organization and one project:

```sh
python -m lotuspond.offline_provision provision \
  --email person@example.com \
  --name "Example person" \
  --attestation-source enterprise_record \
  --source-reference "approved-check-123" \
  --operator-reference "operator-456" \
  --organization-name "Example organization" \
  --project "Agent evaluation" \
  --expires-hours 24 \
  --token-file /protected/new-enrollment.txt \
  --confirm "PROVISION NEW OFFLINE IDENTITY"
```

Omit organization/project arguments for an individual account. A personal workspace is always created. Add `--platform-admin` only for a deliberately authorized installation operator. Supported attestation sources are `in_person`, `government_credential`, and `enterprise_record`; these labels describe the operator's check, not an application certification.

The token file must not already exist. The CLI creates it with owner-only read/write permissions and never prints the token or a password. Store it on a protected filesystem, share the link only with the intended person, and delete your copy after delivery. The token expires in 1–72 hours; the default is 24. The CLI refuses existing email addresses and never overwrites an existing account.

The person opens the link, reviews their provisioned account and organization, and chooses a password. Completion consumes the link and returns them to normal sign-in. Pending accounts cannot sign in or use provisioned authority. Email remains unverified. The account-security screen identifies operator attestation separately from mailbox verification.

## Replace a lost or expired pending link

Recheck the recipient and the operator authorization, then use the exact user ID returned by provisioning and its matching email:

```sh
python -m lotuspond.offline_provision reissue-enrollment \
  --user-id USER_UUID \
  --email person@example.com \
  --operator-reference "operator-456" \
  --expires-hours 24 \
  --token-file /protected/replacement-enrollment.txt \
  --confirm "REISSUE OFFLINE ENROLLMENT"
```

Reissue invalidates prior unconsumed links and retains the original active attestation. It refuses enrolled, disabled, or unauthorized accounts. This command cannot reset the password of an enrolled account. Existing-account recovery and identity-provider integration need a separately reviewed workflow.

If the installation later changes to connected mode with mail delivery, an offline-origin account must first complete enrollment and verify its mailbox through the signed-in verification flow before email recovery becomes available. An operator-attested email address alone cannot authorize a password reset.

## Access and operational controls

An active, completed attestation can authorize production organization membership and explicitly granted operator access. It does not grant either role by itself. Email invitations still require a matching verified mailbox; use deliberate direct membership administration for attested offline users. Scoped API keys remain separate from human sessions and cannot approve releases.

To terminate an offline account's access, run the explicit revocation command after verifying its exact identity:

```sh
python -m lotuspond.offline_provision revoke-identity \
  --user-id USER_UUID \
  --email person@example.com \
  --operator-reference "operator-456" \
  --reason "Access authorization ended" \
  --confirm "REVOKE OFFLINE IDENTITY"
```

This disables the account, revokes its attestations and pending enrollment links, deletes its login sessions, revokes attributed API keys, and records an audit event. Account, workspace and business evidence remain. It refuses accounts that were not provisioned offline. Reactivation is not provided by this command; use a separately reviewed recovery plan.

Review account and attestation audit events. Expired or revoked attestations do not authorize organization/operator access. The installation must define evidence retention, periodic identity review, privileged-role review, trusted delivery and recovery procedures. Authenticator MFA is a separate required step for production platform operators; see [authenticator and recovery](mfa.md). Federated identity and external identity proofing are not supplied by this flow.

The [installation drill](deployment.md) exercises synthetic enrollment and production-mode authorization on a disposable isolated database. Its result is separate from your internal TLS, human verification process and production acceptance.
