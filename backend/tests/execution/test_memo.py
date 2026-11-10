"""Finding 11, part 5: the per-process memo of pure validation (no database needed)."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from lotuspond.execution import memo


@pytest.fixture(autouse=True)
def empty():
    memo._MEMO.clear()
    yield
    memo._MEMO.clear()


class Counted:
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result, error

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.result() if callable(self.result) else self.result


def test_the_same_exact_input_is_computed_once_and_each_caller_gets_a_copy(monkeypatch):
    validate = Counted(result=lambda: {"steps": [{"id": "a"}]})
    monkeypatch.setattr(memo, "validate_executable_package", validate)
    first = memo.package({"package_id": "p", "version": "1"})
    first["steps"].append({"id": "mutated"})  # a caller changing its own copy
    second = memo.package({"package_id": "p", "version": "1"})
    assert len(validate.calls) == 1
    assert second == {"steps": [{"id": "a"}]}


def test_a_refusal_is_never_kept(monkeypatch):
    validate = Counted(error=ValueError("invalid_package"))
    monkeypatch.setattr(memo, "validate_executable_package", validate)
    for _ in range(3):
        with pytest.raises(ValueError):
            memo.package({"package_id": "p"})
    assert len(validate.calls) == 3


@pytest.mark.parametrize(
    "inputs",
    [
        ({"a": 1}, {"a": True}, {"a": 1.0}, {"a": "1"}),
        ([1, 2], (1, 2)),
        ({"a": b"x"}, {"a": "x"}),
        ({1: "a"}, {"1": "a"}),
    ],
)
def test_inputs_of_different_types_never_share_a_key(monkeypatch, inputs):
    validate = Counted(result=lambda: object())
    monkeypatch.setattr(memo, "validate_executable_package", validate)
    results = [memo.package(value) for value in inputs]
    assert len(validate.calls) == len(inputs)
    assert len({id(result) for result in results}) == len(inputs)


def test_an_input_marshal_cannot_encode_is_computed_without_the_memo(monkeypatch):
    validate = Counted(result="validated")
    monkeypatch.setattr(memo, "validate_executable_package", validate)
    model = object()
    assert memo.package(model) == memo.package(model) == "validated"
    assert len(validate.calls) == 2


def test_a_signature_verdict_is_keyed_by_the_exact_trust_set_and_kept_only_when_valid(monkeypatch):
    class Report:
        def __init__(self, valid):
            self.valid = valid

    verdicts = {"active": True, "revoked": False}
    calls = []

    def verify(raw, envelope, trust):
        calls.append(trust["keys"][0]["state"])
        return Report(verdicts[trust["keys"][0]["state"]])

    monkeypatch.setattr(memo, "verify_executable_package", verify)
    raw, envelope = {"package_id": "p"}, {"key_id": "k"}
    active = {"keys": [{"key_id": "k", "state": "active"}]}
    revoked = {"keys": [{"key_id": "k", "state": "revoked"}]}
    assert memo.workflow_verified(raw, envelope, active) is True
    assert memo.workflow_verified(raw, envelope, active) is True
    # Revoking the key changes the trust set: never answered from the earlier verdict.
    assert memo.workflow_verified(raw, envelope, revoked) is False
    assert memo.workflow_verified(raw, envelope, revoked) is False
    assert calls == ["active", "revoked", "revoked"]


def test_a_plan_and_its_bytes_come_from_one_compilation(monkeypatch):
    compile_package = Counted(result=lambda: {"plan": ["step"]})
    monkeypatch.setattr(memo, "compile_package", compile_package)
    monkeypatch.setattr(memo, "canonical_plan_json", lambda plan: b"plan-bytes")
    first, content = memo.plan({"root": 1}, [{"dep": 1}], [{"doc": 1}])
    first["plan"].append("mutated")
    second, again = memo.plan({"root": 1}, [{"dep": 1}], [{"doc": 1}])
    assert len(compile_package.calls) == 1
    assert content == again == b"plan-bytes"
    assert second == {"plan": ["step"]}
    # Another closure is another key.
    memo.plan({"root": 1}, [{"dep": 2}], [{"doc": 1}])
    assert len(compile_package.calls) == 2


def test_the_memo_is_bounded_and_drops_the_least_recently_used(monkeypatch):
    validate = Counted(result=lambda: "validated")
    monkeypatch.setattr(memo, "validate_executable_package", validate)
    monkeypatch.setattr(memo._MEMO, "entries", 3)
    for n in range(3):
        memo.package({"n": n})
    memo.package({"n": 0})  # recently used again
    memo.package({"n": 3})  # evicts {"n": 1}
    assert len(memo._MEMO._items) == 3
    before = len(validate.calls)
    memo.package({"n": 0})
    memo.package({"n": 2})
    assert len(validate.calls) == before
    memo.package({"n": 1})
    assert len(validate.calls) == before + 1


def test_concurrent_callers_each_get_a_correct_copy(monkeypatch):
    barrier = threading.Barrier(8)

    def validate(raw):
        return {"id": raw["id"], "items": [raw["id"]]}

    monkeypatch.setattr(memo, "validate_executable_package", validate)

    def call(n):
        barrier.wait(5)
        result = memo.package({"id": n % 2})
        result["items"].append("mine")
        return result

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(call, range(8)))
    assert all(r == {"id": n % 2, "items": [n % 2, "mine"]} for n, r in enumerate(results))
    assert memo.package({"id": 0}) == {"id": 0, "items": [0]}
