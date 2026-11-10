"""A bounded per-process memo of pure validation over exact content (finding 11).

Admission, signals and reservations re-read and re-check every authority input on
every call, and again after each provider wait: rows, digests and links, the current
trust snapshot, its expiry and catalog membership, grants and assurance (Review
Focus 2). They also redid pure work each time: validating, canonicalizing, compiling
and signature-checking the same immutable stored bytes. That work depends only on
its exact inputs (a signature's on the exact trust set too: verification reads no
clock), so its results are kept here, keyed by SHA-256 of those inputs.

- Keys serialize the inputs with `marshal` format 2: no object references, and the
  bytes decode back to the same structure, so different inputs never share a key
  (tuple and list, int and bool, str and bytes stay distinct). An input `marshal`
  cannot encode is not memoized.
- Only successes are kept: a refusal raises before anything is stored and is
  recomputed every time; a signature verdict is kept only when valid.
- Cached models are never shared: each caller gets its own deep copy.
- Nothing here grants authority, survives a restart or returns anything for which
  the caller did not supply the bytes.

Owner decision, 29 October 2026 (docs/superpowers/plans/2026-10-29-installation-capacity-lock.md,
part 5).
"""

import copy
import marshal
from collections import OrderedDict
from hashlib import sha256
from threading import Lock

from lotuspond_sdk.executable import (
    canonical_executable_json,
    compile_package,
    validate_executable_package,
)
from lotuspond_sdk.executable.catalog import canonical_catalog_document, validate_catalog
from lotuspond_sdk.executable.plan import canonical_plan_json
from lotuspond_sdk.executable.signing import verify_executable_package

ENTRIES = 512


class _Memo:
    def __init__(self, entries=ENTRIES):
        self.entries, self._items, self._lock = entries, OrderedDict(), Lock()

    def get(self, key, compute, *, keep=lambda value: True):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = compute()  # outside the lock: a racing duplicate computes the same value
        if keep(value):
            with self._lock:
                self._items[key] = value
                self._items.move_to_end(key)
                while len(self._items) > self.entries:
                    self._items.popitem(last=False)
        return value

    def clear(self):
        with self._lock:
            self._items.clear()


_MEMO = _Memo()


def _key(kind, *values):
    try:
        return sha256(marshal.dumps((kind, *values), 2)).digest()
    except ValueError:  # not a builtin-typed structure: compute without the memo
        return None


def _remembered(kind, values, compute, *, keep=lambda value: True):
    key = _key(kind, *values)
    return compute() if key is None else _MEMO.get(key, compute, keep=keep)


def package(raw):
    """`validate_executable_package(raw)`, as the caller's own copy."""
    return copy.deepcopy(_remembered("package", (raw,), lambda: validate_executable_package(raw)))


def package_bytes(raw) -> bytes:
    """`canonical_executable_json(raw)`."""
    return _remembered("package-bytes", (raw,), lambda: canonical_executable_json(raw))


def catalog(raw):
    """`validate_catalog(raw)`, as the caller's own copy."""
    return copy.deepcopy(_remembered("catalog", (raw,), lambda: validate_catalog(raw)))


def catalog_bytes(raw) -> bytes:
    """`canonical_catalog_document(raw)`."""
    return _remembered("catalog-bytes", (raw,), lambda: canonical_catalog_document(raw))


def workflow_verified(raw, envelope, trust) -> bool:
    """Whether `verify_executable_package` accepts this package under this exact trust set.

    `trust` is the trust set's JSON dump, so a revoked or replaced key changes the key
    of the memo. Only an accepting verdict is kept.
    """

    def verify():
        return verify_executable_package(raw, envelope, trust).valid is True

    return _remembered(
        "workflow-verified", (raw, envelope, trust), verify, keep=lambda valid: valid
    )


def plan(root, dependencies, documents):
    """`compile_package` of an exact closure: the caller's own plan and its canonical bytes."""

    def compile_closure():
        compiled = compile_package(
            root, dependencies=dependencies, catalog={"documents": documents}
        )
        return compiled, canonical_plan_json(compiled)

    compiled, content = _remembered("plan", (root, dependencies, documents), compile_closure)
    return copy.deepcopy(compiled), content


def remembered(kind, values, compute):
    """Any other pure function of builtin-typed `values`, as the caller's own copy."""
    return copy.deepcopy(_remembered(kind, values, compute))
