# Authenticator and recovery codes

LotusPond Works supports an authenticator app as a second factor for browser sign-in. After enabling it, enter your password and then either the current six-digit authenticator code or one unused recovery code. Production platform operators must enable MFA before opening operator controls. Workspace roles do not by themselves impose an organization-wide MFA policy.

## Enable an authenticator

1. Open **Account security** and start authenticator setup. Enter your current password. Starting setup signs out other sessions and rotates this browser's session.
2. Add the displayed setup key to your authenticator app as a time-based account named Lotuspond. Keep the setup key private. Setup expires if you do not finish it promptly.
3. Enter a code from the app to confirm possession. Setup is not enabled until confirmation succeeds.
4. Save the recovery codes in your password manager or another secure place separate from your authenticator. They are displayed once. Confirm that you have saved them before leaving the screen.

Enabling MFA signs out previous sessions and rotates the current browser session. Password sign-in alone cannot create an authenticated session for an account with MFA enabled. Keep the clocks on the server and authenticator accurate. A code already accepted cannot be used again; wait for the app's next code when making another sensitive change.

## Recovery and changes

Each recovery code can be used once in place of an authenticator code. It still requires the account password. Regenerating codes requires your current password and a fresh authenticator or recovery code, invalidates the previous set, and signs out other sessions. Save the newly displayed codes immediately.

When the installation has mail configured, enrolling, removing or reissuing authenticator material also sends a notice to your account mailbox saying what changed and when. That message contains no link and no code, so treat any message that asks you to follow a link or send a code as an attack.

Disabling MFA also requires your password and a fresh second factor. Production platform operators cannot disable MFA. A password-reset link changes the password and signs out sessions; it does not remove or replace the authenticator. Mailbox verification does not substitute for the second factor.

If you lose both the authenticator and all recovery codes, there is no self-service bypass. Inside an organization, an owner or administrator can clear it for you through `POST /api/v1/auth/mfa/members/:id/reset`: it must be someone other than you, they confirm with their own password and record a reason, and it only removes your factor. No session, password or new factor is issued, so the administrator cannot use it to enter your account; you sign in with your own password and enrol again. You are emailed that it happened. An administrator cannot reset a platform operator's factor this way, and a personal workspace has no administrator to authorize it — there, losing every factor still has no recovery path. Do not manually clear database fields as a recovery procedure.

## Installation key

The API needs `LOTUSPOND_MFA_ENCRYPTION_KEY`: a distinct, stable URL-safe base64 encoding of 32 random bytes. Supply it through the installation's secret mechanism. Do not reuse the mail-encryption key or put the value in source, shell history, logs, bundles, or support messages. The API encrypts authenticator secrets; the database stores only digests of recovery codes and login challenges.

Back up the encryption key separately from the database and test their recovery together in an isolated installation. Replacing or losing the key makes existing encrypted authenticator secrets unusable. Automated key rotation is not provided. Plan a reviewed re-encryption migration before rotating it. All API instances must use the same key.

Without a configured key, new enrollment is unavailable and production platform-operator access remains blocked. An already enabled account never falls back to password-only sign-in. The background mail worker does not need the MFA key.

Authenticator codes work without outbound network access. Keep server and device clocks accurate and give users a way to retain their recovery codes.

## Security limits

Authenticator codes and recovery codes can be phished. This is not WebAuthn/passkey authentication, federation, or a compliance certification. Trusted-device exceptions are not implemented, and the administrator reset above is the only assisted lost-factor recovery.

The implementation follows the time-step and replay requirements in [RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238). Recovery and factor-change controls are informed by [OWASP's MFA guidance](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html); the limits above remain explicit.
