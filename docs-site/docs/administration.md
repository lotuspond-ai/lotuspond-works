# Administration

Production administration starts with explicit ownership and separation of duties.

Use [Operations](operations.md) for queue eligibility, expired leases, and worker poll observations. It shows a timestamped aggregate across the installation without customer evidence.

- Keep workspace membership current and review elevated roles.
- Issue expiring, scoped API keys per workload; revoke unused keys.
- Require independent approval for governed releases.
- Back up PostgreSQL with encryption and test restoration on an isolated system.
- Set telemetry and artifact retention by data class.
- Monitor API readiness, worker failures, database capacity, and Redis availability.
- Record upgrades, configuration changes, incident decisions, and recovery tests.

Production startup requires an authenticated PostgreSQL URL, Redis, and an HTTPS `LOTUSPOND_ORIGIN`. TLS termination, certificate rotation, external backup storage, alert delivery, and identity federation remain operator responsibilities unless a deployment adds them explicitly.

Production platform operators must enroll an [authenticator](mfa.md) and use an MFA-verified session before accessing operator APIs. Configure the API's dedicated encryption key before operator onboarding. An unenrolled operator can sign in to set up MFA but cannot use operator controls. Store recovery codes securely; production operators cannot disable their second factor.

Air-gapped operation prevents runtime egress only when the surrounding network enforces it. Set `LOTUSPOND_NETWORK_MODE=airgap`, block outbound traffic at the network boundary, and validate every configured integration. A flag alone is not an isolation control.

Use [offline account enrollment](offline-identity.md) to provision a new person or an explicitly authorized platform operator without email delivery. The person sets their own password through a single-use link. Never substitute a manual mailbox-verification flag for identity enrollment.
