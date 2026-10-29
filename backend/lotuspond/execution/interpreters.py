"""Registered interpreter versions (ADR-0011, Task 11).

An execution pins the interpreter version admission recorded, and only a worker
that implements it may claim it. This registry is the build's closed list of those
versions. Admission and engines refuse anything else, and a worker refuses to start
while a non-terminal execution pins a version this build does not register: a
registered version stays installed until no non-terminal execution pins it. There
is no history rollover and no migration of a pinned execution to another version.

Each version also pins the SDK compiler revision whose plans it interprets
(Task 8: progression recompiles the pinned closure and compares plan bytes). A
version is registered only while this build's SDK compiles that revision, so an
SDK whose compiler changed cannot silently reinterpret existing executions. The
SDK carries one compiler revision, so such a build registers no version that
pins the old one: its workers refuse to start until no non-terminal execution
pins it, and new work needs a new interpreter version that pins the new compiler.
"""

from lotuspond_sdk.executable.metadata import COMPILER_REVISION
from sqlalchemy import select, tuple_

from .engine_contracts import INTERPRETER_V1
from .errors import ExecutionError
from .orchestrator import TERMINAL

# Interpreter version -> the compiler revision whose plans it interprets.
COMPILERS = {INTERPRETER_V1: "lotuspond.workflow-compiler.v2.1"}
REGISTERED = frozenset(
    version for version, compiler in COMPILERS.items() if compiler == COMPILER_REVISION
)


def require_interpreter(interpreter_id: str) -> str:
    """The version itself when this build registers it; `unsupported_interpreter` otherwise."""
    if type(interpreter_id) is not str or interpreter_id not in REGISTERED:
        raise ExecutionError("unsupported_interpreter")
    return interpreter_id


def unsupported_pins(db, *, limit: int = 16) -> tuple[str, ...]:
    """Pins of non-terminal executions this build cannot interpret, at most `limit`.

    An interpreter version it does not register (the version itself), or a plan
    compiled by another revision than the one its version pins (`compiler:` and
    that revision).
    """
    from ..models import ExecutablePublication, Execution, ExecutionDeploymentRevision

    live = Execution.state.not_in(tuple(TERMINAL))
    versions = set(
        db.scalars(
            select(Execution.interpreter_id)
            .where(live, Execution.interpreter_id.not_in(tuple(REGISTERED)))
            .distinct()
            .limit(limit)
        )
    )
    pinned = tuple_(Execution.interpreter_id, ExecutablePublication.compiler_id).in_(
        [(version, COMPILERS[version]) for version in sorted(REGISTERED)]
    )
    compilers = set(
        db.scalars(
            select(ExecutablePublication.compiler_id)
            .join(
                ExecutionDeploymentRevision,
                ExecutionDeploymentRevision.publication_id == ExecutablePublication.id,
            )
            .join(Execution, Execution.deployment_revision_id == ExecutionDeploymentRevision.id)
            .where(live, Execution.interpreter_id.in_(tuple(REGISTERED)), ~pinned)
            .distinct()
            .limit(limit)
        )
    )
    return tuple(sorted(versions | {f"compiler:{c}" for c in compilers}))[:limit]
