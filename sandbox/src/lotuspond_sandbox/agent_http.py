"""Fixed loopback HTTP helper killed by the controller at the request deadline."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

MAX_REQUEST_BYTES = 4 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None  # noqa: PLR1711, RET501


def fetch_agent_response(url: str, body: bytes, response_limit: int) -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect,
    )
    with opener.open(request, timeout=5.0) as response:
        if response.status != 200:
            raise ValueError("non-success status")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > response_limit:
            raise ValueError("response too large")
        value = response.read(response_limit + 1)
    if len(value) > response_limit:
        raise ValueError("response too large")
    return value


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    try:
        limit = int(sys.argv[2])
        if limit < 1 or limit > 4096:
            raise ValueError
        body = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError
        result = fetch_agent_response(sys.argv[1], body, limit)
    except (OSError, ValueError, urllib.error.URLError):
        raise SystemExit(1) from None
    sys.stdout.buffer.write(result)


if __name__ == "__main__":
    main()
