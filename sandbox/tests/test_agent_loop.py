from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from lotuspond_sandbox.agent_loop import (
    AgentProtocolError,
    parse_agent_action,
    request_agent_action,
    validate_agent_url,
)


class AgentProtocolTests(unittest.TestCase):
    def test_url_is_explicit_loopback_only(self) -> None:
        self.assertEqual(
            validate_agent_url("http://127.0.0.1:8765/v1/action"),
            "http://127.0.0.1:8765/v1/action",
        )
        for rejected in (
            "https://127.0.0.1:8765/v1/action",
            "http://localhost:8765/v1/action",
            "http://user:secret@127.0.0.1:8765/v1/action",
            "http://127.0.0.1:8765/v1/action?target=elsewhere",
            "http://127.0.0.1:99999/v1/action",
        ):
            with self.subTest(rejected=rejected), self.assertRaises(AgentProtocolError):
                validate_agent_url(rejected)

    def test_action_parser_is_strict_and_bounded(self) -> None:
        self.assertEqual(parse_agent_action(b'{"type":"finish"}').type, "finish")
        self.assertEqual(
            parse_agent_action(b'{"type":"key","key":"ENTER"}').key,
            "ENTER",
        )
        for rejected in (
            b'{"type":"type_text","text":"line\\nbreak"}',
            b'{"type":"key","key":"CTRL_L"}',
            b'{"type":"finish","extra":true}',
            json.dumps({"type": "type_text", "text": "a" * 257}).encode(),
        ):
            with self.subTest(rejected=rejected), self.assertRaises(AgentProtocolError):
                parse_agent_action(rejected)

    def test_http_agent_response_is_consumed_without_redirects(self) -> None:
        completed = subprocess.CompletedProcess(
            (), 0, stdout=b'{"type":"finish"}', stderr=b""
        )
        with patch("subprocess.run", return_value=completed) as run:
            action, raw = request_agent_action(
                "http://127.0.0.1:8765/v1/action",
                {"schema_version": "test"},
                timeout=1,
            )
        self.assertEqual(action.type, "finish")
        self.assertEqual(raw, b'{"type":"finish"}')
        self.assertEqual(run.call_args.kwargs["timeout"], 1)
        self.assertEqual(
            set(run.call_args.kwargs["env"]),
            {"PYTHONDONTWRITEBYTECODE", "PYTHONPATH"},
        )

    def test_slow_agent_is_killed_at_whole_request_deadline(self) -> None:
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(("helper",), 0.25)
        ) as run, self.assertRaisesRegex(AgentProtocolError, "request failed"):
            request_agent_action(
                "http://127.0.0.1:8765/v1/action",
                {"schema_version": "test"},
                timeout=0.25,
            )
        self.assertEqual(run.call_args.kwargs["timeout"], 0.25)


if __name__ == "__main__":
    unittest.main()
