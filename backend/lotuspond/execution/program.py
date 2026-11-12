"""Pinned interpreter program: verified package closure, compiled plan and occurrences.

Loads only stored canonical bytes and checks their digests against the pins; it
does not consult publisher trust or customer authority (dispatch and capacity
transactions do that under current authority). Occurrence paths name one exact
step or region occurrence:

    path    := segment ("/" segment)*
    segment := step_id | step_id ":" selector | step_id "#" record
    selector:= "then" | "else" | branch_id | item_index | "call"
    record  := "select" | "items" | "input" | "dispatch"

A segment with a selector opens a region (decision branch, parallel branch,
iteration item or subworkflow call). A final `#` segment names a composite's
pinned record; `#` never appears in an identifier, so a record cannot collide
with a branch ID. Step IDs are unique within each package.
"""

import json
import re
from dataclasses import dataclass
from hashlib import sha256

from lotuspond_runtime.protocol import Scope
from lotuspond_sdk.executable import (
    canonical_executable_json,
    compile_package,
    validate_executable_package,
)
from lotuspond_sdk.executable.catalog import validate_catalog
from lotuspond_sdk.executable.package import (
    ActionDispatchStep,
    DecisionStep,
    IterationStep,
    OutcomeVerificationStep,
    ParallelStep,
    SubworkflowStep,
)
from lotuspond_sdk.executable.plan import CompiledPlan, canonical_plan_json

from ..models import ExecutableDependency, ExecutablePublication, ExecutionDeploymentRevision
from . import memo
from .action_policies import Closure, admit_closure
from .authorization import record, scoped
from .interpreters import COMPILERS

MAX_PATH = 1024
# E05 adds `#dispatch` (an action dispatch step's command attempts) and `#reconcile`
# (its read-back attempts, Task 8), and `#outcome` (an outcome verification step's
# evaluated outcome, Task 9; its read attempts are the step's own record).
RECORDS = frozenset({"#select", "#items", "#input", "#dispatch", "#reconcile", "#outcome"})
_INDEX = re.compile(r"0|[1-9][0-9]{0,3}")


class ProgramError(Exception):
    """Fixed integrity or occurrence code; never stored values or caller text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StepRef:
    package_sha256: str
    step_id: str
    kind: str
    occurrence_path: str
    region_path: str
    selector: str | None
    step: object
    package: object


@dataclass(frozen=True)
class Program:
    root_sha256: str
    plan: CompiledPlan
    packages: dict
    catalog: dict

    @property
    def root(self):
        return self.packages[self.root_sha256]

    def child(self, package, workflow_ref):
        declaration = next(d for d in package.subworkflows if d.id == workflow_ref)
        return declaration.ref.sha256, self.packages[declaration.ref.sha256]

    def document(self, package, family, local):
        declaration = next(d for d in getattr(package, family) if d.id == local)
        return self.catalog[declaration.ref.sha256]


def program_from(package, sources, plan=None):
    """Build from already verified publication sources (admission/signal services)."""
    packages, catalog = {}, {}
    for source in sources.values():
        # The same canonical bytes as the validated child's, without validating again.
        if source.get("schema_version") == "lotuspond.workflow-package.v2":
            packages[_digest(memo.package_bytes(source))] = memo.package(source)
        else:
            document = memo.catalog({"documents": [source]}).documents[0]
            catalog[_digest(memo.catalog_bytes(source))] = document
    root = _digest(canonical_executable_json(package))
    packages[root] = package
    return Program(root, plan, packages, catalog)


def _digest(content):
    return sha256(content).hexdigest()


_CACHE: dict = {}


def load_program(db, scope: Scope, execution) -> Program:
    """Verified, digest-pinned closure for an execution's deployment revision."""
    revision = record(db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id)
    publication = record(db, ExecutablePublication, scope, revision.publication_id)
    links = list(
        db.scalars(
            scoped(ExecutableDependency, scope)
            .where(ExecutableDependency.publication_id == publication.id)
            .order_by(ExecutableDependency.id)
            .limit(512)
        )
    )
    rows = [
        record(db, ExecutablePublication, scope, link.dependency_publication_id) for link in links
    ]
    contents = [publication.canonical_package, publication.canonical_plan]
    for link, row in zip(links, rows, strict=True):
        if link.dependency_sha256 != row.package_sha256:
            raise ProgramError("integrity")
        contents.append(row.canonical_package)
    if (
        publication.kind != "workflow"
        or publication.canonical_plan is None
        # The interpreter version pins the compiler whose plans it interprets (Task 11).
        or publication.compiler_id != COMPILERS.get(execution.interpreter_id)
        or sha256(publication.canonical_package).hexdigest() != publication.package_sha256
        or sha256(publication.canonical_plan).hexdigest() != publication.plan_sha256
        or any(sha256(row.canonical_package).hexdigest() != row.package_sha256 for row in rows)
    ):
        raise ProgramError("integrity")
    key = sha256(b"\x00".join(sha256(c).digest() for c in contents)).hexdigest()
    cached = _CACHE.get(key)
    if cached is not None:
        program, pinned = cached
        if pinned != execution.interpreter_id:
            raise ProgramError("integrity")
        return program
    try:
        raw = json.loads(publication.canonical_package)
        root = validate_executable_package(raw)
        plan = CompiledPlan.model_validate_json(publication.canonical_plan)
        packages = {publication.package_sha256: root}
        catalog, workflows, documents = {}, [], []
        for row in rows:
            source = json.loads(row.canonical_package)
            if row.kind == "workflow":
                packages[row.package_sha256] = validate_executable_package(source)
                workflows.append(source)
            else:
                catalog[row.package_sha256] = validate_catalog({"documents": [source]}).documents[0]
                documents.append(source)
        # Progression follows the package bodies; the pinned plan must be exactly
        # what the SDK compiles from this closure (pointers, digests, limits).
        compiled = compile_package(raw, dependencies=workflows, catalog={"documents": documents})
        if canonical_plan_json(compiled) != publication.canonical_plan:
            raise ValueError
    except Exception:
        raise ProgramError("integrity") from None
    if plan.package_sha256 != publication.package_sha256:
        raise ProgramError("integrity")
    program = Program(publication.package_sha256, plan, packages, catalog)
    # E05: a stored package never bypasses the effect rules, and it runs only under
    # the interpreter those rules select (owner decision 15).
    try:
        pinned = admit_closure(program_closure(program))
    except Exception:
        raise ProgramError("integrity") from None
    if len(_CACHE) > 64:
        _CACHE.clear()
    _CACHE[key] = (program, pinned)
    if pinned != execution.interpreter_id:
        raise ProgramError("integrity")
    return program


def program_closure(program) -> Closure:
    others = tuple(p for sha, p in program.packages.items() if sha != program.root_sha256)
    root = program.root
    return Closure(packages=(root, *others), catalog=dict(program.catalog))


def stored_closure(db, scope: Scope, publication_id) -> Closure:
    """A publication's closure from its stored canonical bytes, digest-checked; no trust."""
    publication = record(db, ExecutablePublication, scope, publication_id)
    links = list(
        db.scalars(
            scoped(ExecutableDependency, scope)
            .where(ExecutableDependency.publication_id == publication.id)
            .order_by(ExecutableDependency.id)
            .limit(512)
        )
    )
    packages, catalog = [validate_executable_package(json.loads(publication.canonical_package))], {}
    for link in links:
        row = record(db, ExecutablePublication, scope, link.dependency_publication_id)
        digest = sha256(row.canonical_package).hexdigest()
        if digest != row.package_sha256 or digest != link.dependency_sha256:
            raise ProgramError("integrity")
        source = json.loads(row.canonical_package)
        if row.kind == "workflow":
            packages.append(validate_executable_package(source))
        else:
            catalog[row.package_sha256] = validate_catalog({"documents": [source]}).documents[0]
    return Closure(packages=tuple(packages), catalog=catalog)


def _segments(path):
    if type(path) is not str or not 1 <= len(path) <= MAX_PATH:
        raise ProgramError("occurrence_unresolved")
    return path.split("/")


def _find(body, step_id):
    return next((step for step in body.steps if step.id == step_id), None)


def _region(program, package_sha256, package, step, selector):
    """Return the (package digest, package, body) a composite selector opens."""
    if isinstance(step, DecisionStep) and selector in ("then", "else"):
        return package_sha256, package, step.then_body if selector == "then" else step.else_body
    if isinstance(step, ParallelStep):
        branch = next((b for b in step.branches if b.id == selector), None)
        if branch is not None:
            return package_sha256, package, branch.body
    if isinstance(step, IterationStep) and _INDEX.fullmatch(selector):
        if int(selector) < step.max_items:
            return package_sha256, package, step.body
    if isinstance(step, SubworkflowStep) and selector == "call":
        digest, child = program.child(package, step.workflow_ref)
        return digest, child, child.body
    raise ProgramError("occurrence_unresolved")


def resolve_occurrence(plan: Program, occurrence_path: str) -> StepRef:
    """The exact step or region occurrence a persisted path names, or a fixed refusal."""
    program = plan
    segments = _segments(occurrence_path)
    digest, package = program.root_sha256, program.root
    body, region_path = package.body, ""
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if "#" in segment:
            step_id, _, record = segment.partition("#")
            selector = "#" + record
            if ":" in segment or selector not in RECORDS:
                raise ProgramError("occurrence_unresolved")
        else:
            step_id, separator, selector = segment.partition(":")
            if separator and not selector:
                raise ProgramError("occurrence_unresolved")
        step = _find(body, step_id)
        if step is None:
            raise ProgramError("occurrence_unresolved")
        path = (region_path + "/" if region_path else "") + segment
        if last:
            if selector in RECORDS:
                valid = (
                    (selector == "#select" and isinstance(step, DecisionStep))
                    or (selector == "#items" and isinstance(step, IterationStep))
                    or (selector == "#input" and isinstance(step, SubworkflowStep))
                    or (
                        selector in ("#dispatch", "#reconcile")
                        and isinstance(step, ActionDispatchStep)
                    )
                    or (selector == "#outcome" and isinstance(step, OutcomeVerificationStep))
                )
                if not valid:
                    raise ProgramError("occurrence_unresolved")
            elif selector:
                _region(program, digest, package, step, selector)
            return StepRef(
                digest, step.id, step.kind, path, region_path, selector or None, step, package
            )
        if not selector or selector in RECORDS:
            raise ProgramError("occurrence_unresolved")
        digest, package, body = _region(program, digest, package, step, selector)
        region_path = path
    raise ProgramError("occurrence_unresolved")


def region_body(program: Program, region_path: str):
    """(package digest, package, body) of a region occurrence; "" is the root body."""
    if region_path == "":
        return program.root_sha256, program.root, program.root.body
    ref = resolve_occurrence(program, region_path)
    if ref.selector is None or ref.selector in RECORDS:
        raise ProgramError("occurrence_unresolved")
    return _region(program, ref.package_sha256, ref.package, ref.step, ref.selector)


def child_path(region_path: str, segment: str) -> str:
    path = (region_path + "/" if region_path else "") + segment
    if len(path) > MAX_PATH:
        raise ProgramError("occurrence_unresolved")
    return path
