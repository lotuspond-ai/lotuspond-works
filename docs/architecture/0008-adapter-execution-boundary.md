# 0008 — What a process group is for, and where untrusted code actually runs

Status: accepted, 2026-09-22. Refines [0006](0006-supervised-evaluation-runners.md) and
[0007](0007-customer-execution-distribution.md).

## The question left open

Earlier records kept repeating that "process groups are not a hostile-code sandbox"
without saying what they *are* for, or where code that genuinely cannot be trusted is
supposed to run. That left the impression of a half-built sandbox, and invited someone to
eventually treat the process group as a security boundary because nothing said otherwise.

## Decision

**A process group is a lifecycle bound, not a privilege bound.** It exists so that a
scenario cannot outlive its deadline, cannot hold the runner open, and cannot leave
orphans behind when it is cancelled. It bounds *time and resources for processes we own*.
It grants no isolation from the host and is not claimed to.

That is adequate because of who runs the adapter. **The supervised runner is customer-side
tooling.** An operator starts it on their own machine and points it at their own adapter:
`npm run runner -- ./adapter.mjs`. The adapter is that operator's code, running under that
operator's account, with the rights they already have. Lotuspond is not interposing
between a stranger and a host; it is executing the customer's own evaluation logic on the
customer's own infrastructure. Adding a sandbox there would protect the operator from
themselves while adding a boundary the product cannot honestly promise to maintain.

**Code we did not write runs in a container instead.** The Gym path ships a task image
Lotuspond selected, so it runs in Docker with `--network none`, an immutable pinned image,
a short-lived run grant, and verified teardown. That is the isolation boundary, and it is
the one the product describes to customers.

**These two paths stay separate in the packaging.** The execution image that carries
`runner-http`, `runner-mcp`, `voice` and `gate` never receives a Docker socket; Gym access
lives on the separate host-side kit. That separation is already asserted by
`deploy/checks/execution-smoke.py` and stays a required property.

## What changes

`LOTUSPOND_ADAPTER_ROOT` is added as optional defence in depth. When an operator runs the
runner as a long-lived service rather than from their own shell, the adapter argument is no
longer something a human typed each time, so it becomes worth constraining. With the
variable set, the runner imports an adapter only from inside that directory; symlinks are
resolved before the check so a link inside the root cannot point out of it, and a missing
path is refused rather than silently allowed. Unset, behavior is unchanged — an operator
running it by hand keeps naming their own file.

This narrows which file can be imported by a service whose arguments something else on the
host might influence. It is explicitly **not** a claim that the adapter is sandboxed once
imported.

## Consequences

- The security boundary for customer-supplied evaluation logic is the customer's own host
  and account, stated plainly rather than implied by silence.
- Anything Lotuspond itself supplies for execution goes in a container, not a process group.
- Multi-tenant hosted execution — running one customer's adapter on Lotuspond's own
  infrastructure — is **out of scope under this decision**. It would need a real isolation
  boundary (separate kernels or a hardened runtime) and its own record; process groups must
  not be quietly promoted into that role.
- `SECURITY.md` and `docs/CAPABILITIES.md` state the boundary in these terms, so "workload
  isolation proof" is no longer an open item pretending a sandbox is nearly finished. What
  remains open is hosted multi-tenant execution, which is a different product decision.
