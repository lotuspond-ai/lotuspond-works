# Administration

Production administration starts with explicit ownership and separation of duties.

- Keep workspace membership current and review elevated roles.
- Issue expiring, scoped API keys per workload; revoke unused keys.
- Require independent approval for governed releases.
- Back up PostgreSQL with encryption and test restoration on an isolated system.
- Set artifact retention by data class.
- Monitor API readiness, worker failures, database capacity, and Redis availability.
- Record upgrades, configuration changes, and recovery tests.

Production startup requires an authenticated PostgreSQL URL, Redis, and an HTTPS `LOTUSPOND_ORIGIN`. TLS termination, certificate rotation, external backup storage, and identity federation remain operator responsibilities unless a deployment adds them explicitly.

Production platform operators must enroll an [authenticator](mfa.md) and use an MFA-verified session before accessing operator APIs. Configure the API's dedicated encryption key before operator onboarding. An unenrolled operator can sign in to set up MFA but cannot use operator controls. Store recovery codes securely; production operators cannot disable their second factor.

Never substitute a manual mailbox-verification flag for identity verification.
