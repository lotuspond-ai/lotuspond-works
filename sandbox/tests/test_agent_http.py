from __future__ import annotations

import unittest
from typing import Self
from unittest.mock import patch
from urllib.request import ProxyHandler

from lotuspond_sandbox.agent_http import fetch_agent_response


class AgentHttpTests(unittest.TestCase):
    def test_ambient_proxy_configuration_is_disabled(self) -> None:
        class Response:
            status = 200

            def __init__(self) -> None:
                self.headers = {"Content-Length": "17"}

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *args: object) -> None:
                del args

            def read(self, limit: int) -> bytes:
                self.limit = limit
                return b'{"type":"finish"}'

        class Opener:
            def open(self, request: object, *, timeout: float) -> Response:
                del request, timeout
                return Response()

        with patch("urllib.request.build_opener", return_value=Opener()) as build:
            result = fetch_agent_response(
                "http://127.0.0.1:8765/v1/action", b"{}", 4096
            )
        handlers = build.call_args.args
        proxies = [handler.proxies for handler in handlers if isinstance(handler, ProxyHandler)]
        self.assertEqual(proxies, [{}])
        self.assertEqual(result, b'{"type":"finish"}')


if __name__ == "__main__":
    unittest.main()
