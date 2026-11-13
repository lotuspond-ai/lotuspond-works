"""The generic `https-json` connector's read side: fixed GET routes over pinned TLS.

The installed connector artifact is a routes document. It maps each catalog operation
to one HTTPS GET path, whose `{name}` placeholders are filled from the read's target.
Its canonical (RFC 8785) SHA-256 is the connector's pinned `artifact_sha256`, so a
changed route is a different connector, never a silent edit. Reads go only to the
connection's single destination, through `DestinationTransport` (one resolution, the
egress policy on every resolved address, the operator's CA, no proxy, no redirect),
with the connection's one secret as a bearer token. Nothing is written.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import rfc8785
from lotuspond_sdk.activity import UsageObservation

from .destination import DestinationTransport, EgressPolicy
from .errors import FleetError
from .providers import ActivityFailure, ProviderResult

ROUTES_FORMAT = "lotuspond.https-json.routes.v1"
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]{0,63})\}")
_SEGMENT_VALUE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_TOKEN = re.compile(r"[\x21-\x7e]{1,4096}")


def routes_digest(routes) -> str:
    return hashlib.sha256(rfc8785.dumps(routes)).hexdigest()


def validate_routes(routes, connector) -> dict:
    """The routes document, checked against the pinned connector; anything else denies."""
    if (
        type(routes) is not dict
        or set(routes) != {"format", "connector", "operations"}
        or routes["format"] != ROUTES_FORMAT
        or routes["connector"] != {"name": connector.name, "version": connector.version}
        or routes_digest(routes) != connector.artifact_sha256
        or type(routes["operations"]) is not dict
        or not 1 <= len(routes["operations"]) <= 64
    ):
        raise ValueError("Routes do not match the pinned connector")
    for operation, route in routes["operations"].items():
        if (
            type(route) is not dict
            or set(route) != {"method", "path"}
            or route["method"] != "GET"
            or type(route["path"]) is not str
            or not route["path"].startswith("/")
            or len(route["path"]) > 512
            or any(c in route["path"] for c in "%\\?#")
        ):
            raise ValueError(f"Invalid route for {operation}")
    return routes


def route_path(template: str, target) -> str:
    """The path with every placeholder filled from the target, each value a plain segment."""
    if type(target) is not dict:
        raise ActivityFailure("provider_failed", reached=False)

    def fill(match):
        value = target.get(match.group(1))
        if type(value) is not str or not _SEGMENT_VALUE.fullmatch(value):
            raise ActivityFailure("provider_failed", reached=False)
        return value

    return _PLACEHOLDER.sub(fill, template)


class HttpsJsonReadProvider:
    """`ReadProvider` for one installed `https-json` connector."""

    def __init__(self, descriptor, secrets, *, test_mode=False):
        settings = descriptor.settings_data() or {}
        self.descriptor = descriptor
        self._secrets = secrets
        self._routes = validate_routes(settings.get("routes"), descriptor.connector)
        egress = settings.get("egress") or {}
        self._policy = EgressPolicy(
            tuple(egress.get("allowed_private_cidrs", ())),
            tuple(egress.get("denied_cidrs", ())),
            tuple(egress.get("denied_hosts", ())),
        )
        self._ca = Path(settings["ca_file"])
        if not self._ca.is_absolute():
            raise ValueError("The CA file must be an absolute operator path")
        # Loopback destinations (a local stand-in) only for a test-mode worker.
        self._loopback = test_mode is True and settings.get("test_loopback") is True

    def invoke(self, records, arguments, deadline_ms) -> ProviderResult:
        operation = records.envelope.operation
        route = self._routes["operations"].get(operation)
        connection = records.connection
        if (
            route is None
            or operation not in self.descriptor.operations
            or connection is None
            or len(connection.destinations) != 1
            or len(connection.secrets) != 1
            or len(self._secrets) < 1
        ):
            raise ActivityFailure("unavailable", reached=False)
        if type(arguments) is not dict or arguments.get("input") is not None:
            raise ActivityFailure("provider_failed", reached=False)
        rule = connection.destinations[0]
        path = rule.path_prefix.rstrip("/") + route_path(route["path"], arguments.get("target"))
        try:
            token = self._secrets[0].decode("ascii").strip()
        except UnicodeError:
            raise ActivityFailure("unavailable", reached=False) from None
        if not _TOKEN.fullmatch(token):
            raise ActivityFailure("unavailable", reached=False)
        limits = connection.data_handling
        deadline = time.monotonic_ns() + limits.timeout_ms * 1_000_000
        transport = (
            DestinationTransport._for_test(self._policy, self._ca)
            if self._loopback
            else DestinationTransport(self._policy, self._ca)
        )
        transport._worker_process = True  # the DNS helper stays in the child's group
        sent = False
        try:
            with transport.connect(rule, path, deadline) as connected:
                try:
                    body = connected.send(
                        "GET",
                        b"",
                        {"Authorization": f"Bearer {token}"},
                        limits.max_response_bytes,
                    )
                finally:
                    sent = connected.sent
            value = json.loads(body)
        except FleetError:
            # Nothing sent: positively not reached. Anything sent may have arrived.
            if sent:
                raise ActivityFailure("provider_failed") from None
            raise ActivityFailure("unavailable", reached=False) from None
        except ValueError:
            raise ActivityFailure("invalid_output") from None
        # A connector read has no metered model cost: zero is known, not assumed.
        usage = UsageObservation(
            input_tokens=0, output_tokens=0, cost_microusd=0, completed_at_ms=0
        )
        return ProviderResult(value, usage)
