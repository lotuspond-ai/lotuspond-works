from dataclasses import replace

import pytest
from command_fixtures import command_case, delivery, records, snapshot

from lotuspond_runtime.errors import FleetError


def test_current_trust_required_even_with_valid_signature():
    from lotuspond_runtime.commands import validate_delivery

    case = command_case()
    for trust in (
        replace(snapshot(case), valid_until_ms=case.now),
        replace(snapshot(case), valid_until_ms=case.now + 60001),
        replace(snapshot(case), revision=0),
    ):
        with pytest.raises(FleetError):
            validate_delivery(
                delivery(case),
                identity=case.identity,
                records=records(case),
                trust=trust,
                now_ms=case.now,
            )


def test_revoked_and_untrusted_keys():
    from lotuspond_sdk.trust import TrustSet

    from lotuspond_runtime.commands import validate_delivery

    case = command_case()
    for keys in ([], [case.trust.keys[0].model_copy(update={"state": "revoked"})]):
        with pytest.raises(FleetError):
            validate_delivery(
                delivery(case),
                identity=case.identity,
                records=records(case),
                trust=replace(snapshot(case), trust=TrustSet(format_version=1, keys=keys)),
                now_ms=case.now,
            )
