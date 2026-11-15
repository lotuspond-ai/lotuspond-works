"""Exact, scoped executable admission. No downloads, plugin loaders or execution."""

import json
from dataclasses import dataclass, field
from hashlib import sha256
from threading import Event, Lock, Thread
from time import monotonic
from typing import Annotated

from lotuspond_runtime.protocol import Digest, SafeInt, Scope
from lotuspond_sdk.executable import canonical_executable_json
from lotuspond_sdk.executable.catalog import canonical_catalog_document, validate_catalog
from lotuspond_sdk.executable.dependencies import FAMILIES
from lotuspond_sdk.executable.metadata import COMPILER_REVISION, VALIDATOR_REVISION
from lotuspond_sdk.executable.package import ArtifactRef
from lotuspond_sdk.executable.signing import (
    executable_signature_envelope_json,
)
from lotuspond_sdk.executable.validation import validate_executable_package
from lotuspond_sdk.trust import TrustSet
from pydantic import BeforeValidator, Field

from ..fleet.authority import database_now_ms
from ..models import ExecutableDependency, ExecutablePublication, FleetMutationRequest
from . import memo
from .action_policies import Closure, admit_closure, check_catalog_policy
from .authorization import mark_replay, reauthorize, record, scoped
from .contracts import (
    Contract,
    PackageCheckRequest,
    PublicationRequest,
    PublicationView,
    array,
    canonical_value,
)
from .errors import ExecutionError, execution_service


class AuthoritySnapshot(Contract):
    """Independent scope-specific policy; package/client input can never create it."""

    scope: Scope
    expires_at_ms: SafeInt
    trust: TrustSet
    catalog: Annotated[tuple[Digest, ...], BeforeValidator(array), Field(max_length=448)] = ()
    release_required: bool = True
    capabilities: Annotated[tuple[str, ...], BeforeValidator(array), Field(max_length=64)] = ()
    destination_classes: Annotated[
        tuple[str, ...], BeforeValidator(array), Field(max_length=64)
    ] = ()
    operation_classes: Annotated[tuple[str, ...], BeforeValidator(array), Field(max_length=64)] = ()


class _Lookup:
    """One bounded, non-caching lookup slot; late/stuck answers never grant authority.

    A concurrent caller waits for the slot, and its timeout bounds the wait and the
    call together: the call gets only what the wait left. So at most one call is in
    flight, a stuck provider holds one thread and its slot, and every caller is
    refused within its own bound (owner decision, 2026-10-29; finding 3).
    """

    def __init__(self):
        self.lock = Lock()
        self.closed = False

    def _wait(self, timeout):
        """Wait for a slot another call holds; True once this caller holds it."""
        return self.lock.acquire(timeout=timeout)

    def call(self, provider, method, args, timeout, code):
        if provider is None or self.closed:
            raise ExecutionError(code)
        started = monotonic()
        if not (self.lock.acquire(blocking=False) or self._wait(timeout)):
            raise ExecutionError(code)
        remaining = timeout - (monotonic() - started)
        if self.closed or remaining <= 0:
            self.lock.release()
            raise ExecutionError(code)
        done, result = Event(), []

        def run():
            try:
                result.append(getattr(provider, method)(*args))
            except Exception:
                pass
            finally:
                done.set()
                self.lock.release()

        try:
            Thread(target=run, name="execution-authority", daemon=True).start()
        except Exception:
            self.lock.release()
            raise ExecutionError(code) from None
        if not done.wait(remaining) or self.closed or not result:
            raise ExecutionError(code)
        return result[0]


@dataclass(frozen=True)
class ExecutionDependencies:
    """Constructor-only integration boundary. No settings select synthetic providers."""

    authority: object | None = None
    assurance: object | None = None
    test_mode: bool = False
    timeout_seconds: float = 5.0
    _slot: _Lookup = field(default_factory=_Lookup, repr=False, compare=False)
    _assurance_slot: _Lookup = field(default_factory=_Lookup, repr=False, compare=False)

    def __post_init__(self):
        if not 0 < self.timeout_seconds <= 5:
            raise ValueError("Invalid authority timeout")
        if not self.test_mode and any(
            getattr(p, "test_only", False) for p in (self.authority, self.assurance)
        ):
            raise ValueError("Synthetic providers require test mode")

    def current(self, db, scope):
        try:
            raw = self._slot.call(
                self.authority, "current", (scope,), self.timeout_seconds, "trust_unavailable"
            )
            snapshot = AuthoritySnapshot.model_validate(raw)
            if snapshot.scope != scope or snapshot.expires_at_ms <= database_now_ms(db):
                raise ValueError
            return snapshot
        except Exception:
            raise ExecutionError("trust_unavailable") from None

    def close(self):
        self._slot.closed = self._assurance_slot.closed = True


DEFAULT_DEPENDENCIES = ExecutionDependencies()


def current_snapshot(db, snapshot):
    """Recheck expiry after any subsequent database lock or bounded provider wait."""
    if snapshot.expires_at_ms <= database_now_ms(db):
        raise ExecutionError("trust_unavailable")


def _trusted_workflow(raw, signature, snapshot):
    try:
        # The snapshot was fetched for this call; only the pure verdict is memoized.
        if not memo.workflow_verified(raw, signature, snapshot.trust.model_dump(mode="json")):
            raise ValueError
    except Exception:
        raise ExecutionError("trust_unavailable") from None


def publication_ref(row):
    return ArtifactRef(kind=row.kind, name=row.name, version=row.version, sha256=row.package_sha256)


def _read_publication(row, snapshot):
    """Reconstruct exact bytes; database attribution and a prior allow are not trust."""
    if row.status != "active":
        raise ExecutionError("dependency_unavailable")
    try:
        raw = json.loads(row.canonical_package)
        if row.kind == "workflow":
            content = memo.package_bytes(raw)
            signature = json.loads(row.canonical_signature)
            if executable_signature_envelope_json(signature) != row.canonical_signature:
                raise ValueError
            _trusted_workflow(raw, signature, snapshot)
            name, version = raw["package_id"], raw["version"]
        else:
            content = memo.catalog_bytes(raw)
            document = memo.catalog({"documents": [raw]}).documents[0]
            check_catalog_policy(document)
            name, version = document.name, document.version
            if row.kind != document.kind or row.package_sha256 not in snapshot.catalog:
                raise ExecutionError("trust_unavailable")
            if (
                row.canonical_signature is not None
                or row.canonical_plan is not None
                or row.compiler_id is not None
            ):
                raise ValueError
        if (
            content != row.canonical_package
            or sha256(content).hexdigest() != row.package_sha256
            or row.name != name
            or row.version != version
            or row.validator_id != VALIDATOR_REVISION
        ):
            raise ValueError
        return raw
    except ExecutionError:
        raise
    except Exception:
        raise ExecutionError("dependency_unavailable") from None


def _links(db, scope, publication_id):
    return list(
        db.scalars(
            scoped(ExecutableDependency, scope)
            .where(ExecutableDependency.publication_id == publication_id)
            .order_by(ExecutableDependency.id)
            .limit(512)
        )
    )


def _references(raw):
    if raw.get("schema_version") == "lotuspond.workflow-package.v2":
        return [d["ref"] for family in FAMILIES for d in raw[family]]
    definition = raw["definition"]
    return ([definition["model_ref"]] if raw["kind"] == "agent" else []) + [
        tool["connector_ref"] for tool in definition.get("read_tools", [])
    ]


def _check_dependency_links(db, scope, rows, sources):
    by_ref = {canonical_value(publication_ref(row).model_dump()): row for row in rows}
    for row in rows:
        required, reached = _references(sources[row.id]), set()
        while required:
            child = by_ref.get(canonical_value(required.pop()))
            if child is None:
                raise ExecutionError("dependency_unavailable")
            if child.id in reached:
                continue
            reached.add(child.id)
            required.extend(_references(sources[child.id]))
        links = _links(db, scope, row.id)
        if (
            len(links) != len(reached)
            or {link.dependency_publication_id for link in links} != reached
        ):
            raise ExecutionError("dependency_unavailable")
        for link in links:
            child = next(r for r in rows if r.id == link.dependency_publication_id)
            if (
                link.dependency_sha256 != child.package_sha256
                or link.canonical_dependency != canonical_value(publication_ref(child).model_dump())
            ):
                raise ExecutionError("dependency_unavailable")


def closure_of(root, rows, sources) -> Closure:
    """The root package, its workflow packages and catalog documents by digest."""
    return Closure(
        packages=(
            memo.package(root),
            *(memo.package(sources[row.id]) for row in rows if row.kind == "workflow"),
        ),
        catalog={
            row.package_sha256: memo.catalog({"documents": [sources[row.id]]}).documents[0]
            for row in rows
            if row.kind != "workflow"
        },
    )


def _resolve(db, scope, root, rows, snapshot):
    """SDK resolves reachability/digest/cycles; normalized persisted closure must agree.

    Returns the plan, the sources and the plan's canonical bytes.
    """
    if len(rows) > 511:
        raise ExecutionError("dependency_unavailable")
    sources = {row.id: _read_publication(row, snapshot) for row in rows}
    try:
        plan, content = memo.plan(
            root,
            [sources[row.id] for row in rows if row.kind == "workflow"],
            [sources[row.id] for row in rows if row.kind != "workflow"],
        )
        _check_dependency_links(db, scope, rows, sources)
        # E05's effect rules on the whole closure (publication, deployment, admission).
        admit_closure(closure_of(root, rows, sources))
        return plan, sources, content
    except ExecutionError:
        raise
    except Exception:
        raise ExecutionError("dependency_unavailable") from None


def load_publication(db, scope, identifier, snapshot):
    row = record(db, ExecutablePublication, scope, identifier)
    raw = _read_publication(row, snapshot)
    if row.kind != "workflow":
        raise ExecutionError("invalid_request")
    links = _links(db, scope, row.id)
    rows = [
        record(db, ExecutablePublication, scope, link.dependency_publication_id) for link in links
    ]
    for link, child in zip(links, rows, strict=True):
        if (
            link.dependency_sha256 != child.package_sha256
            or link.canonical_dependency != canonical_value(publication_ref(child).model_dump())
        ):
            raise ExecutionError("dependency_unavailable")
    _, sources, content = _resolve(db, scope, raw, rows, snapshot)
    if (
        row.compiler_id != COMPILER_REVISION
        or row.canonical_plan != content
        or row.plan_sha256 != sha256(content).hexdigest()
    ):
        raise ExecutionError("dependency_unavailable")
    return row, memo.package(raw), links, rows, sources


def mutation_replay(db, actor, operation, key, content, view):
    if operation not in {
        "execution.publish",
        "execution.deployment.create",
        "execution.deployment.status",
        "execution.context.register",
        "execution.context.version",
    }:
        raise ExecutionError("invalid_request")
    if (
        type(key) is not str
        or not 1 <= len(key) <= 128
        or any(ord(c) < 33 or ord(c) > 126 for c in key)
    ):
        raise ExecutionError("invalid_request")
    digest = sha256(
        canonical_value({"actor": actor.user_id, "api_key": actor.api_key_id, "request": content})
    ).hexdigest()
    row = db.scalar(
        scoped(FleetMutationRequest, actor.scope).where(
            FleetMutationRequest.operation == operation, FleetMutationRequest.request_key == key
        )
    )
    if row:
        if row.request_digest != digest:
            raise ExecutionError("conflict")
        mark_replay(db)
        try:
            return digest, view.model_validate(row.result)
        except Exception:
            raise ExecutionError("authority_unavailable") from None
    return digest, None


def remember(db, actor, operation, key, digest, result):
    db.add(
        FleetMutationRequest(
            **actor.scope.model_dump(),
            operation=operation,
            request_key=key,
            request_digest=digest,
            result=result.model_dump(mode="json"),
        )
    )
    db.flush()
    return result


def publication_view(row):
    return PublicationView(
        id=row.id,
        kind=row.kind,
        name=row.name,
        version=row.version,
        package_sha256=row.package_sha256,
        plan_sha256=row.plan_sha256,
        validator_id=row.validator_id,
        compiler_id=row.compiler_id,
    )


@execution_service
def publish_v2(
    db, actor, request: PublicationRequest, request_key: str, *, dependencies=DEFAULT_DEPENDENCIES
):
    actor = reauthorize(db, actor, "publish")
    try:
        request = PublicationRequest.model_validate(request)
        snapshot = dependencies.current(db, actor.scope)
        root = request.package
        workflow = (
            isinstance(root, dict) and root.get("schema_version") == "lotuspond.workflow-package.v2"
        )
        rows = [
            record(db, ExecutablePublication, actor.scope, identifier)
            for identifier in sorted(request.dependency_ids)
        ]
        if workflow:
            package = validate_executable_package(root)
            content = canonical_executable_json(package)
            signature = executable_signature_envelope_json(request.signature)
            _trusted_workflow(package, request.signature, snapshot)
            _, _, plan_content = _resolve(db, actor.scope, root, rows, snapshot)
            name, version, kind = package.package_id, package.version, "workflow"
        else:
            content = canonical_catalog_document(root)
            document = validate_catalog({"documents": [root]}).documents[0]
            check_catalog_policy(document)
            if sha256(content).hexdigest() not in snapshot.catalog or request.signature is not None:
                raise ExecutionError("trust_unavailable")
            # Catalog references have their own transitive model/read-tool closure.
            required = []
            if document.kind == "agent":
                required.append(document.definition.model_ref)
            required.extend(
                tool.connector_ref for tool in getattr(document.definition, "read_tools", ())
            )
            reached = set()
            by_ref = {canonical_value(publication_ref(row).model_dump()): row for row in rows}
            while required:
                ref = required.pop()
                child = by_ref.get(canonical_value(ref.model_dump()))
                if child is None:
                    raise ExecutionError("dependency_unavailable")
                if child.id in reached:
                    continue
                reached.add(child.id)
                raw_child = _read_publication(child, snapshot)
                typed = validate_catalog({"documents": [raw_child]}).documents[0]
                if typed.kind == "agent":
                    required.append(typed.definition.model_ref)
                required.extend(
                    tool.connector_ref for tool in getattr(typed.definition, "read_tools", ())
                )
            if reached != {row.id for row in rows}:
                raise ExecutionError("dependency_unavailable")
            # Apply the SDK's aggregate catalog depth/item/byte and per-kind caps,
            # not merely the individual document admission bounds.
            try:
                validate_catalog(
                    {"documents": [root, *[_read_publication(row, snapshot) for row in rows]]}
                )
            except Exception:
                raise ExecutionError("dependency_unavailable") from None
            _check_dependency_links(
                db, actor.scope, rows, {row.id: _read_publication(row, snapshot) for row in rows}
            )
            signature = plan_content = None
            name, version, kind = document.name, document.version, document.kind
        digest, replay = mutation_replay(
            db,
            actor,
            "execution.publish",
            request_key,
            request.model_dump(mode="json"),
            PublicationView,
        )
        if replay:
            stored = record(db, ExecutablePublication, actor.scope, replay.id)
            _read_publication(stored, snapshot)
            if stored.package_sha256 != sha256(content).hexdigest():
                raise ExecutionError("conflict")
            if workflow:
                load_publication(db, actor.scope, stored.id, snapshot)
            else:
                _check_dependency_links(
                    db,
                    actor.scope,
                    [stored, *rows],
                    {r.id: _read_publication(r, snapshot) for r in [stored, *rows]},
                )
            current_snapshot(db, snapshot)
            return publication_view(stored)
        existing = db.scalar(
            scoped(ExecutablePublication, actor.scope).where(
                ExecutablePublication.kind == kind,
                ExecutablePublication.name == name,
                ExecutablePublication.version == version,
            )
        )
        if existing:
            _read_publication(existing, snapshot)
            if existing.canonical_package != content or existing.canonical_plan != plan_content:
                raise ExecutionError("conflict")
            if workflow:
                load_publication(db, actor.scope, existing.id, snapshot)
            else:
                _check_dependency_links(
                    db,
                    actor.scope,
                    [existing, *rows],
                    {r.id: _read_publication(r, snapshot) for r in [existing, *rows]},
                )
            result = publication_view(existing)
        else:
            current_snapshot(db, snapshot)
            row = ExecutablePublication(
                **actor.scope.model_dump(),
                kind=kind,
                name=name,
                version=version,
                canonical_package=content,
                canonical_plan=plan_content,
                canonical_signature=signature,
                package_sha256=sha256(content).hexdigest(),
                plan_sha256=sha256(plan_content).hexdigest() if plan_content else None,
                validator_id=VALIDATOR_REVISION,
                compiler_id=COMPILER_REVISION if workflow else None,
                publisher_user_id=actor.user_id,
                publisher_api_key_id=actor.api_key_id,
            )
            db.add(row)
            db.flush()
            for dependency in rows:
                db.add(
                    ExecutableDependency(
                        **actor.scope.model_dump(),
                        publication_id=row.id,
                        dependency_publication_id=dependency.id,
                        dependency_sha256=dependency.package_sha256,
                        canonical_dependency=canonical_value(
                            publication_ref(dependency).model_dump()
                        ),
                    )
                )
            result = publication_view(row)
        current_snapshot(db, snapshot)
        return remember(db, actor, "execution.publish", request_key, digest, result)
    except ExecutionError:
        raise
    except (ValueError, TypeError, AttributeError):
        raise ExecutionError("invalid_request") from None


def _problems(error) -> list[dict]:
    """The SDK's own bounded diagnostics: closed codes and pointers, never values."""
    from lotuspond_sdk.executable.errors import ERROR_MESSAGES, ExecutableError

    if isinstance(error, ExecutableError):
        issues = [
            {"path": issue.pointer, "code": issue.code, "message": issue.message}
            for issue in error.issues
        ]
        return issues or [{"path": "", "code": error.code, "message": ERROR_MESSAGES[error.code]}]
    return [{"path": "", "code": "invalid_package", "message": ERROR_MESSAGES["invalid_package"]}]


@execution_service
def check_v2(db, actor, request, *, dependencies=DEFAULT_DEPENDENCIES) -> dict:
    """Validate, canonicalize, resolve and compile a workflow package without trust or storage.

    The Studio's check (docs/superpowers/specs/2026-11-15-studio-v2-authoring-design.md):
    the same validator, compiler and closure rules as `publish_v2`, but it neither verifies
    a signature nor records anything, so an author sees every problem before signing.
    """
    from lotuspond_sdk.executable import executable_digest
    from lotuspond_sdk.executable.errors import ExecutableError

    actor = reauthorize(db, actor, "publish")
    request = PackageCheckRequest.model_validate(request)
    root = request.package
    answer = {
        "valid": False,
        "package_sha256": None,
        "plan_sha256": None,
        "canonical": None,
        "problems": [],
    }
    if not isinstance(root, dict) or root.get("schema_version") != "lotuspond.workflow-package.v2":
        answer["problems"] = _problems(None)
        return answer
    try:
        package = validate_executable_package(root)
    except ExecutableError as error:
        answer["problems"] = _problems(error)
        return answer
    answer["canonical"] = canonical_executable_json(package).decode()
    answer["package_sha256"] = executable_digest(package)
    rows = [
        record(db, ExecutablePublication, actor.scope, identifier)
        for identifier in sorted(request.dependency_ids)
    ]
    try:
        snapshot = dependencies.current(db, actor.scope)
        sources = {row.id: _read_publication(row, snapshot) for row in rows}
        _, content = memo.plan(
            root,
            [sources[row.id] for row in rows if row.kind == "workflow"],
            [sources[row.id] for row in rows if row.kind != "workflow"],
        )
        _check_dependency_links(db, actor.scope, rows, sources)
        admit_closure(closure_of(root, rows, sources))
    except ExecutableError as error:
        answer["problems"] = _problems(error)
        return answer
    except ExecutionError as error:
        answer["problems"] = [{"path": "", "code": error.code, "message": error.code}]
        return answer
    answer["plan_sha256"] = sha256(content).hexdigest()
    answer["valid"] = True
    return answer
