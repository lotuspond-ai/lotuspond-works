# Operations

Open **Administration → Operations** with a platform-operator account. Production requires an MFA-verified session. Refresh retrieves aggregate state across the installation; it does not expose workspace identifiers, prompts, transcripts, addresses, or credentials. Each response has a reference time. The page does not poll automatically or retain old figures after a failed refresh.

## Queues and worker observations

The identity-mail section shows recorded job states, work eligible for a claim, delayed retries, work in suspended workspaces, expired leases, and the age of the oldest eligible record. Eligibility respects retry limits and active references. Disabled queues can still contain eligible records. These classifications overlap the state totals; do not add them together. Oldest age starts at record creation, including earlier retry time.

The mail worker records the start of a poll in Redis under one fixed role key. The evaluation and incident-alert workers were retired on 2026-11-11. A marker is fresh for 150 seconds and expires after 300 seconds. A fresh marker means at least one process for that role polled recently. It does not count replicas, confirm task progress, or establish successful delivery. A slow SMTP conversation can make a functioning worker's marker stale. Missing, stale, invalid, and unavailable observations need investigation; none alone proves the process has stopped. Configure all API and worker instances to use the same installation's Redis, and synchronize their clocks.

Redis failure leaves database-derived queue counts available, with worker observations explicitly unavailable. Without Redis in development, observations are not configured. A failed database query returns a sanitized error instead of zero counts. Counts come from separate aggregate queries and can change while the snapshot is assembled; this is an operational view, not a transactionally consistent accounting report. Large installations should measure query cost against their expected retained data volume before setting an external polling interval.

Queue failures and SMTP acceptance have different meanings; acceptance does not establish that a person received the message.

## Investigating failures

1. If eligible work grows, check whether processing is enabled, the workspace is suspended, retry limits are exhausted, or leases have expired. Inspect the corresponding worker process and sanitized logs. A fresh poll with a growing queue can indicate slow work or insufficient capacity.
2. If observations are missing or stale, inspect Redis connectivity, worker configuration and clock alignment. Check running work before restarting a worker; a recent poll is not a progress signal. Do not delete jobs or reset leases to clear the display.
3. If `/api/v1/ready` fails, inspect database/schema and Redis availability. Worker observations are intentionally not readiness requirements. Confirm recovery using readiness, a new Operations snapshot, and the outcome of a known synthetic request.

For an isolated local failure drill, use the reviewed image bundle with `deploy/checks/airgap-smoke.py <archive> <trusted-checksum> --failure-recovery`. It stops only its own disposable Redis, checks that readiness fails and Operations reports the outage, then restarts it and checks recovery. This does not measure production capacity, deployment failover, mail delivery, or customer traffic.
