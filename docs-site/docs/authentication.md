# Authentication and API keys

Human sessions and API keys are separate identities. Browser sessions use secure, HTTP-only cookies in production. Mutating session requests must come from the configured origin.

Automation uses workspace-bound API keys. Give each producer its own expiring key, grant only the required scope, record its owner and purpose, and revoke it when the workload ends. Keys must never approve releases.

## Account security

Use **Account security** to inspect active sessions and sign out sessions you no longer use. Changing your password requires the current password and signs out every other session. The current browser receives a new session cookie. Active sessions expire automatically; each account retains at most 20.

Use [authenticator MFA](mfa.md) to require an authenticator or one-use recovery code after your password. It is mandatory for production platform operators. Password recovery does not remove an enabled second factor.

The API exposes `GET /api/v1/auth/sessions`, `DELETE /api/v1/auth/sessions/:id` with an empty JSON body, and `POST /api/v1/auth/password` with `current_password` and `new_password`. The new password must contain 12–256 characters. These endpoints act on the signed-in account across its workspaces.

## Verify or recover an account

Request verification from **Account security**, then open the link sent to the account's email address. Verification links expire and work once. A queued request does not mean the message has reached your inbox. Installations must configure [identity mail](identity-mail.md) and its delivery worker.

Use **Forgot password** on the sign-in screen to request recovery. The acknowledgement does not disclose whether an account exists. Complete the link form with a new password, then sign in normally. A completed reset signs out every existing session; it does not automatically sign in the browser that opened the link.

## Organization invitations

An organization owner or administrator invites an email address and selects a role. Invitees sign in or create a personal account, verify the matching email address, and accept the invitation. Invitations expire and can be revoked. Accepting an invitation does not silently replace an existing member's role. Personal workspaces do not support invitations.

Production organization and operator authority require verified mailbox ownership or an active operator attestation from [offline enrollment](offline-identity.md). Email invitations always require ownership of the matching mailbox. Platform operators additionally require a session verified with [MFA](mfa.md). Identity verification and second-factor possession are separate checks. Identity federation remains open.

## Create an API key

A workspace owner or administrator creates a key in **API keys**, or sends `POST /api/v1/api-keys` with the workspace header and a body such as `{"name":"CI release gate","scopes":["read","run"],"days":30}`. The raw key is returned once. Store it immediately in the client's secret mechanism. Use `POST /api/v1/api-keys/:id/revoke` with an empty JSON body to revoke it.
