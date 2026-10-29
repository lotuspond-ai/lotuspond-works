"""Task 10: context is read under current access for every activity, and kept in memory."""

import logging

from activity_env import build
from fixtures.activity_provider import agent_records, calls

from lotuspond_runtime.activity_worker import ActivityWorker
from lotuspond_runtime.context import ContextRef
from lotuspond_runtime.errors import FleetError

SOURCE = ContextRef(source_id="00000000-0000-4000-8000-000000000042", version=3)
CONTEXT_SENTINEL = "PRIVATE-CONTEXT-SENTINEL-3c9d"
PROMPT_SENTINEL = "PRIVATE-INSTRUCTIONS-SENTINEL-8e21"


class Sources:
    """Control-plane stand-in for current source access."""

    def __init__(self, content="synthetic"):
        self.allowed = True
        self.reads = []
        self.content = content

    def read(self, attempt_id, ref):
        self.reads.append((attempt_id, ref))
        if not self.allowed:
            raise FleetError("forbidden")
        return {"citation": self.content}


def records(**changes):
    return agent_records(context=(SOURCE,), max_steps=2, **changes)


def test_context_acl_change_blocks_new_read(tmp_path):
    sources = Sources()
    env = build(tmp_path, context=sources)
    worker = ActivityWorker(env.deps)
    env.server.root("model", agent=records())
    assert worker.run_once() == "succeeded"
    assert len(sources.reads) == 1 and calls(env.counter) == 1
    # Access changes: the next activity cannot reread the source, and no model runs.
    sources.allowed = False
    env.server.root("model", agent=records())
    assert worker.run_once() == "provider_failed"
    assert len(sources.reads) == 2
    assert calls(env.counter) == 1  # no provider call after the refusal


def test_missing_context_client_denies_rather_than_skipping(tmp_path):
    env = build(tmp_path, context=None)
    env.server.root("model", agent=records())
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    assert calls(env.counter) == 0


def test_context_and_instructions_reach_only_the_model_child(tmp_path, caplog):
    sources = Sources(CONTEXT_SENTINEL)
    # The model fixture fails unless both actually reached it (positive control).
    script = {
        "rounds": [
            {
                "tool_calls": [
                    {"name": "connector-fixture-read", "arguments": {"target": None, "input": None}}
                ],
                "require_seen": CONTEXT_SENTINEL,
            },
            {"output": None, "require_seen": PROMPT_SENTINEL},
        ]
    }
    env = build(tmp_path, context=sources, model_script=script)
    env.server.root("model", agent=records(instructions=f"Answer. {PROMPT_SENTINEL}"))
    with caplog.at_level(logging.DEBUG):
        assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert calls(env.counter) == 3
    # Nothing the control plane stores, nothing reported, journaled or logged holds
    # either: a model round stores only digests of its request and reply.
    stored = b"".join(env.server.values.values()).decode()
    reported = "".join(r.model_dump_json() for r in env.server.results)
    journaled = "".join(
        path.read_text() for path in env.journal_dir.iterdir() if path.name.startswith("attempt-")
    )
    for sentinel in (CONTEXT_SENTINEL, PROMPT_SENTINEL):
        for text in (stored, reported, journaled, caplog.text):
            assert sentinel not in text
