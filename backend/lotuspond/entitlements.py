"""Commercial plan entitlements.

These tiers are the product's own definition, not an installation setting: an operator
assigns one of them to a workspace and cannot invent a fourth. Keeping the ladder in
static server code means a request body can never widen what a workspace is entitled to.

Lotuspond processes no payment anywhere. A plan records what a workspace is entitled
to; collecting money for it is a separate commercial system that is not implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Plan:
    """What a subscription level entitles a workspace to.

    Only enforced entitlements belong here. An unenforced field would read as a
    promise the API does not keep.
    """

    name: str
    trace_retention_days: int
    monthly_trace_quota: int

    def retention_cutoff(self, current: datetime | None = None) -> datetime:
        """The instant before which this plan no longer promises to keep a trace."""
        moment = (current or datetime.now(UTC)).astimezone(UTC)
        return moment - timedelta(days=self.trace_retention_days)


PLANS: dict[str, Plan] = {
    "developer": Plan("developer", trace_retention_days=7, monthly_trace_quota=10_000),
    "team": Plan("team", trace_retention_days=30, monthly_trace_quota=250_000),
    "enterprise": Plan("enterprise", trace_retention_days=365, monthly_trace_quota=5_000_000),
}
DEFAULT_PLAN = "developer"


def plan_for(name: str | None) -> Plan:
    """Resolve a stored plan, falling back to the most restrictive tier.

    A database constraint keeps the column inside the ladder, so the fallback only
    covers a record written before that constraint existed. Degrading to the smallest
    entitlement is the safe direction: it never grants more than was paid for.
    """
    return PLANS.get(name or "", PLANS[DEFAULT_PLAN])


def month_start(current: datetime | None = None) -> datetime:
    moment = (current or datetime.now(UTC)).astimezone(UTC)
    return datetime(moment.year, moment.month, 1, tzinfo=UTC)
