"""Database authority boundaries for durable execution records."""

from datetime import timedelta

import pytest
from sqlalchemy.exc import DataError, IntegrityError

from lotuspond import models
from lotuspond.models import EXECUTION_TABLES, V16_TABLES, Base, new_id, now
from tests.fleet.test_schema import load, records

MODEL_NAMES = (
    "ExecutablePublication",
    "ExecutableDependency",
    "ExecutionDeployment",
    "ExecutionDeploymentRevision",
    "DeploymentBinding",
    "DeploymentAssuranceBinding",
    "ExecutionArtifact",
    "ExecutionContextSource",
    "ExecutionContextVersion",
    "Execution",
    "ExecutionStepInstance",
    "ExecutionAttempt",
    "ExecutionWait",
    "ExecutionRequest",
    "ExecutionOutbox",
    "ExecutionInbox",
    "ExecutionEvent",
    "ExecutionControlRequest",
    "ExecutionUsageReservation",
    "ExecutionCapacity",
    "ExecutionSchedulerCursor",
    "ExecutionActivityDelivery",
    "ExecutionLease",
    "ExecutionReconciliationObservation",
)


@pytest.fixture
def engine(execution_database):
    """Route schema checks through positive-control owned PostgreSQL cleanup."""
    from copy import deepcopy

    engine = execution_database()
    deepcopy(Base.metadata).create_all(engine)
    return engine


def test_scoped_records_exist():
    assert all(hasattr(models, name) for name in MODEL_NAMES)


def execution_rows(n=0):
    values = records(n)
    scope = {
        field: values["connections"][field]
        for field in ("workspace_id", "project_id", "environment_id")
    }
    ids = {
        key: new_id()
        for key in (
            "publication",
            "deployment",
            "revision",
            "artifact",
            "execution",
            "step",
            "attempt",
            "wait",
            "source",
            "source_version",
            "dependency",
            "delivery",
            "key",
            "policy",
            "run",
            "release",
        )
    }
    values["api_keys"] = dict(
        id=ids["key"],
        workspace_id=scope["workspace_id"],
        name="fixture",
        token_hash=str(n) * 64,
        prefix="fixture",
        scopes=["execute"],
        expires_at=now() + timedelta(days=1),
        created_by_user_id=values["users"]["id"],
    )
    values.update(
        {
            "executable_publications": dict(
                scope,
                id=ids["publication"],
                kind="workflow",
                name="fixture",
                version="1.0.0",
                canonical_package=b"{}",
                canonical_plan=b"{}",
                canonical_signature=b"{}",
                package_sha256="a" * 64,
                plan_sha256="b" * 64,
                validator_id="fixture",
                compiler_id="fixture",
                publisher_user_id=values["users"]["id"],
                publisher_api_key_id=ids["key"],
            ),
            "execution_deployments": dict(scope, id=ids["deployment"], status="active"),
            "execution_deployment_revisions": dict(
                scope,
                id=ids["revision"],
                deployment_id=ids["deployment"],
                publication_id=ids["publication"],
                pool_id=values["runtime_pools"]["id"],
                revision=1,
                canonical_configuration=b"{}",
                configuration_sha256="c" * 64,
                status="active",
            ),
            "execution_context_sources": dict(scope, id=ids["source"], access_policy_id="fixture"),
            "execution_context_versions": dict(
                scope,
                id=ids["source_version"],
                source_id=ids["source"],
                version=1,
                classification="internal",
                observed_at_ms=0,
                expires_at_ms=10000,
                access_policy_id="fixture",
            ),
            "execution_artifacts": dict(
                scope,
                id=ids["artifact"],
                version=1,
                sha256="d" * 64,
                ciphertext=b"x" * 17,
                nonce=b"n" * 12,
                classification="internal",
                access_policy_id="fixture",
                key_id="fixture",
                retain_until_ms=10000,
                content_bytes=1,
                source_id=ids["source"],
                source_version=1,
            ),
            "executions": dict(
                scope,
                id=ids["execution"],
                deployment_revision_id=ids["revision"],
                interpreter_id="fixture",
                engine_identity=f"opaque-{n}",
                input_artifact_id=ids["artifact"],
                input_artifact_version=1,
                input_sha256="d" * 64,
                initiating_user_id=values["users"]["id"],
                initiating_api_key_id=ids["key"],
                admitted_at_ms=0,
                admission_sequence=1,
                deadline_ms=10000,
                admission_digest="e" * 64,
            ),
            "execution_step_instances": dict(
                scope,
                id=ids["step"],
                execution_id=ids["execution"],
                occurrence_path="root/step",
                node_id="step",
            ),
            "execution_attempts": dict(
                scope,
                id=ids["attempt"],
                execution_id=ids["execution"],
                step_instance_id=ids["step"],
                ordinal=1,
                input_artifact_id=ids["artifact"],
                input_artifact_version=1,
                input_sha256="d" * 64,
            ),
            "execution_waits": dict(
                scope,
                id=ids["wait"],
                execution_id=ids["execution"],
                step_instance_id=ids["step"],
                wait_key="wait",
                deadline_ms=10000,
            ),
            "execution_inbox": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                wait_id=ids["wait"],
                signal_id="signal",
                digest="f" * 64,
                payload_artifact_id=ids["artifact"],
                payload_artifact_version=1,
                payload_sha256="d" * 64,
                expires_at_ms=10000,
            ),
            "execution_events": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                sequence=1,
                source_id="engine",
                source_sequence=1,
                event_id="event",
                digest="d" * 64,
                family="execution_control",
                format_version=1,
                installation_id=values["runtime_instances"]["installation_id"],
                recovery_epoch=n,
                source_generation=n + 1,
                event_artifact_id=ids["artifact"],
                event_artifact_version=1,
                application_state="applied",
                gap_slot=None,
                code="admitted",
                occurred_at_ms=0,
            ),
        }
    )
    values.update(
        {
            "executable_dependencies": dict(
                scope,
                id=ids["dependency"],
                publication_id=ids["publication"],
                dependency_publication_id=ids["publication"],
                dependency_sha256="a" * 64,
                canonical_dependency=b"{}",
            ),
            "deployment_bindings": dict(
                scope,
                id=new_id(),
                deployment_revision_id=ids["revision"],
                publication_id=ids["publication"],
                dependency_id=ids["dependency"],
                connection_id=values["connections"]["id"],
                connection_version=n + 1,
                source_id=ids["source"],
                source_version=1,
            ),
            "policies": dict(id=ids["policy"], workspace_id=scope["workspace_id"], name="fixture"),
            "runs": dict(
                scope, id=ids["run"], name="fixture", revision="legacy", scenario_snapshot=[]
            ),
            "releases": dict(
                id=ids["release"],
                workspace_id=scope["workspace_id"],
                project_id=scope["project_id"],
                name="fixture",
                revision="legacy",
                candidate_run_id=ids["run"],
                policy_id=ids["policy"],
            ),
            "deployment_assurance_bindings": dict(
                scope,
                id=new_id(),
                deployment_revision_id=ids["revision"],
                release_id=ids["release"],
                candidate_run_id=ids["run"],
            ),
            "execution_requests": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                request_key="request",
                digest="e" * 64,
            ),
            "execution_outbox": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                message_id="start",
                kind="engine_start",
                attempt_id=ids["attempt"],
                digest="e" * 64,
            ),
            "execution_control_requests": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                request_id="control",
                action="pause",
                digest="f" * 64,
                initiating_user_id=values["users"]["id"],
                initiating_api_key_id=ids["key"],
            ),
            "execution_usage_reservations": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                step_instance_id=ids["step"],
                attempt_id=ids["attempt"],
                input_tokens=10,
                output_tokens=10,
                cost_microusd=100,
                active_ms=1000,
            ),
            "execution_capacities": dict(scope, id=new_id()),
            "execution_activity_deliveries": dict(
                scope,
                id=ids["delivery"],
                execution_id=ids["execution"],
                attempt_id=ids["attempt"],
                pool_id=values["runtime_pools"]["id"],
                instance_id=values["runtime_instances"]["id"],
                installation_id=values["runtime_instances"]["installation_id"],
                recovery_epoch=n,
                session_id=values["runtime_sessions"]["id"],
                generation=n + 1,
                replay_id=new_id(),
                canonical_envelope=b"{}",
                canonical_signature=b"{}",
                digest="a" * 64,
                issued_at_ms=0,
                expires_at_ms=300000,
                retain_until_ms=330000,
            ),
            "execution_leases": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                attempt_id=ids["attempt"],
                delivery_id=ids["delivery"],
                token_digest="b" * 64,
                claimed_at_ms=0,
                renewed_at_ms=0,
                expires_at_ms=60000,
            ),
            "execution_reconciliation_observations": dict(
                scope,
                id=new_id(),
                execution_id=ids["execution"],
                attempt_id=ids["attempt"],
                source_id="runtime",
                observation_id="observation",
                digest="c" * 64,
                code="outcome_unknown",
                observed_at_ms=0,
            ),
        }
    )
    return values


def with_cases(values):
    """Schema v17 (E06): a case keyed by the fixture's artifact, and its execution. Kept
    out of `execution_rows`, which older-schema migration tests also load."""
    scope = {k: values["executions"][k] for k in ("workspace_id", "project_id", "environment_id")}
    case_id = new_id()
    values["execution_cases"] = dict(
        scope,
        id=case_id,
        key_artifact_id=values["execution_artifacts"]["id"],
        key_artifact_version=1,
        key_sha256=values["execution_artifacts"]["sha256"],
        created_by_user_id=values["users"]["id"],
    )
    values["execution_case_links"] = dict(
        scope,
        id=new_id(),
        case_id=case_id,
        execution_id=values["executions"]["id"],
        linked_by_user_id=values["users"]["id"],
        linked_at_ms=0,
    )
    return values


@pytest.mark.parametrize("boundary", ["workspace", "project", "environment"])
def test_full_scope_fk_matrix(engine, boundary):
    assert "executions" in Base.metadata.tables
    left, right = with_cases(execution_rows()), with_cases(execution_rows(1))
    if boundary in {"project", "environment"}:
        for row in right.values():
            if "workspace_id" in row:
                row["workspace_id"] = left["workspaces"]["id"]
        del right["workspaces"]
    if boundary == "environment":
        for row in right.values():
            if "project_id" in row:
                row["project_id"] = left["projects"]["id"]
        del right["projects"]
    with engine.begin() as connection:
        load(connection, left)
        load(connection, right)
        checked = set()
        # v16 records have their own matrix in tests/execution/actions/test_schema.py.
        assert set(left) & EXECUTION_TABLES == EXECUTION_TABLES - V16_TABLES - {
            "execution_scheduler_cursors"
        }
        for name in (name for name in left if name in EXECUTION_TABLES):
            table = Base.metadata.tables[name]
            for fk in table.foreign_key_constraints:
                if len(fk.columns) < 2:
                    continue
                # Legacy API keys are workspace-scoped, never project/environment grants.
                if fk.referred_table.name == "api_keys" and boundary != "workspace":
                    continue
                for element in fk.elements:
                    column = element.parent.name
                    if (
                        column not in right[name]
                        or left[name].get(column) == right[name][column]
                        or (name, column) in checked
                    ):
                        continue
                    checked.add((name, column))
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            table.update()
                            .where(table.c.id == left[name]["id"])
                            .values(**{column: right[name][column]})
                        )
        assert {
            "deployment_revision_id",
            "input_artifact_id",
            "step_instance_id",
            "wait_id",
            "environment_id",
        }.issubset({field for _, field in checked})


def test_attempt_and_event_uniqueness(engine):
    assert "execution_attempts" in Base.metadata.tables
    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        for name in ("execution_attempts", "execution_events", "execution_inbox"):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    Base.metadata.tables[name].insert().values(**dict(values[name], id=new_id()))
                )


def test_same_environment_different_execution_cannot_link_attempt_wait_or_signal(engine):
    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        execution = dict(
            values["executions"],
            id=new_id(),
            engine_identity="another-engine",
            admission_sequence=2,
        )
        connection.execute(models.Execution.__table__.insert().values(**execution))
        for name in (
            "execution_attempts",
            "execution_waits",
            "execution_inbox",
            "execution_usage_reservations",
            "execution_activity_deliveries",
            "execution_leases",
        ):
            table = Base.metadata.tables[name]
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(table.update().values(execution_id=execution["id"]))
        child = dict(
            values["execution_attempts"],
            id=new_id(),
            ordinal=2,
            parent_attempt_id=values["execution_attempts"]["id"],
            child_request_id="child",
            child_request_digest="b" * 64,
        )
        connection.execute(models.ExecutionAttempt.__table__.insert().values(**child))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                models.ExecutionAttempt.__table__.insert().values(
                    **dict(child, id=new_id(), ordinal=3)
                )
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                models.ExecutionAttempt.__table__.update()
                .where(models.ExecutionAttempt.id == child["id"])
                .values(child_request_digest=None)
            )


def test_distinct_event_identities_and_source_sequence_uniqueness(engine):
    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        event = values["execution_events"]
        for changes in (
            {"sequence": 2, "event_id": "different"},
            {"sequence": 2, "source_sequence": 2},
            {"event_id": "different", "source_sequence": 2},
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    models.ExecutionEvent.__table__.insert().values(
                        **dict(event, id=new_id(), **changes)
                    )
                )
        connection.execute(
            models.ExecutionEvent.__table__.insert().values(
                **dict(event, id=new_id(), sequence=2, source_sequence=3, event_id="gap")
            )
        )
        for name in (
            "execution_requests",
            "execution_control_requests",
            "execution_outbox",
            "execution_usage_reservations",
            "execution_activity_deliveries",
            "execution_leases",
            "execution_reconciliation_observations",
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    Base.metadata.tables[name].insert().values(**dict(values[name], id=new_id()))
                )


def test_assurance_requires_exact_release_candidate_and_environment(engine):
    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        binding = models.DeploymentAssuranceBinding.__table__
        another = dict(values["runs"], id=new_id())
        connection.execute(models.Run.__table__.insert().values(**another))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(binding.update().values(candidate_run_id=another["id"]))
        # Legacy null-environment candidates remain legal; new binding must refuse them.
        legacy = dict(values["runs"], id=new_id(), environment_id=None)
        connection.execute(models.Run.__table__.insert().values(**legacy))
        release = dict(values["releases"], id=new_id(), candidate_run_id=legacy["id"])
        connection.execute(models.Release.__table__.insert().values(**release))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                binding.update().values(release_id=release["id"], candidate_run_id=legacy["id"])
            )
        # A matching release/candidate in another environment is still invalid.
        environment = dict(values["environments"], id=new_id())
        connection.execute(models.Environment.__table__.insert().values(**environment))
        other = dict(values["runs"], id=new_id(), environment_id=environment["id"])
        connection.execute(models.Run.__table__.insert().values(**other))
        release = dict(values["releases"], id=new_id(), candidate_run_id=other["id"])
        connection.execute(models.Release.__table__.insert().values(**release))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                binding.update().values(release_id=release["id"], candidate_run_id=other["id"])
            )


def test_bounds_states_ciphertext_and_unknown_usage(engine):
    values = execution_rows()
    invalid = [
        ("executions", {"deadline_ms": 0}),
        ("executions", {"admitted_at_ms": -1}),
        ("executions", {"deadline_ms": 9007199254740992}),
        ("executions", {"state": "unknown"}),
        ("executions", {"requested_state": "succeeded"}),
        ("executions", {"reason": "private provider failure"}),
        ("execution_attempts", {"state": "expired"}),
        ("execution_attempts", {"ordinal": 0}),
        ("execution_waits", {"state": "succeeded"}),
        ("execution_waits", {"deadline_ms": -1}),
        ("execution_inbox", {"status": "forgotten"}),
        ("execution_outbox", {"status": "leased"}),
        ("execution_events", {"code": "SENTINEL_PRIVATE_PROVIDER_ERROR"}),
        ("execution_events", {"source_sequence": 9007199254740992}),
        ("execution_artifacts", {"nonce": b"short"}),
        ("execution_artifacts", {"content_bytes": 1048577}),
        ("execution_artifacts", {"ciphertext": b"plaintext"}),
        ("execution_artifacts", {"source_version": None}),
        ("deployment_bindings", {"connection_version": None}),
        ("execution_usage_reservations", {"state": "settled"}),
        ("execution_usage_reservations", {"state": "released"}),
        ("execution_usage_reservations", {"observed_cost_microusd": -1}),
        ("execution_capacities", {"active_limit": 6}),
        ("execution_capacities", {"pending_limit": 1001}),
        ("execution_capacities", {"wait_limit": 10001}),
        ("execution_activity_deliveries", {"expires_at_ms": 300001}),
        ("execution_activity_deliveries", {"canonical_envelope": b"x" * 65536}),
        ("execution_activity_deliveries", {"retain_until_ms": 329999}),
        ("execution_leases", {"expires_at_ms": 60001}),
        ("execution_leases", {"renewed_at_ms": -1}),
    ]
    with engine.begin() as connection:
        load(connection, values)
        for name, changes in invalid:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(Base.metadata.tables[name].update().values(**changes))
        reservation = models.ExecutionUsageReservation.__table__
        assert (
            connection.execute(reservation.select()).mappings().one()["observed_cost_microusd"]
            is None
        )
        connection.execute(
            reservation.update().values(state="uncertain", compute_stopped_at_ms=100)
        )
        assert (
            connection.execute(reservation.select()).mappings().one()["observed_cost_microusd"]
            is None
        )
        # Lease expiry is not reclaim permission: uniqueness remains after expiry.
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                models.ExecutionLease.__table__.insert().values(
                    **dict(
                        values["execution_leases"],
                        id=new_id(),
                        claimed_at_ms=60001,
                        renewed_at_ms=60001,
                        expires_at_ms=120000,
                    )
                )
            )
        cursor = models.ExecutionSchedulerCursor.__table__
        connection.execute(cursor.insert().values(installation_id="fixture"))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(cursor.update().values(active_limit=26))


def test_artifact_identity_and_active_revision_pins(engine):
    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        for name, changes in (
            ("executions", {"input_artifact_version": 2}),
            ("executions", {"input_sha256": "e" * 64}),
            ("execution_inbox", {"payload_sha256": "e" * 64}),
            ("execution_attempts", {"result_artifact_id": values["execution_artifacts"]["id"]}),
            (
                "execution_context_versions",
                {"content_artifact_id": values["execution_artifacts"]["id"]},
            ),
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(Base.metadata.tables[name].update().values(**changes))
        revision = dict(values["execution_deployment_revisions"], id=new_id(), revision=2)
        connection.execute(models.ExecutionDeploymentRevision.__table__.insert().values(**revision))
        connection.execute(
            models.ExecutionDeployment.__table__.update().values(active_revision_id=revision["id"])
        )
        assert (
            connection.execute(models.Execution.__table__.select())
            .mappings()
            .one()["deployment_revision_id"]
            == values["execution_deployment_revisions"]["id"]
        )
        another = dict(values["execution_deployments"], id=new_id())
        connection.execute(models.ExecutionDeployment.__table__.insert().values(**another))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                models.ExecutionDeployment.__table__.update()
                .where(models.ExecutionDeployment.id == another["id"])
                .values(active_revision_id=revision["id"])
            )


def test_publication_catalog_kinds_match_sdk_artifact_references(engine):
    from typing import get_args

    from lotuspond_sdk.executable.package import ArtifactKind

    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        publication = models.ExecutablePublication.__table__
        for kind in get_args(ArtifactKind):
            row = dict(values[publication.name], id=new_id(), name=f"catalog-{kind}", kind=kind)
            if kind != "workflow":
                row.update(
                    canonical_plan=None,
                    canonical_signature=None,
                    plan_sha256=None,
                    compiler_id=None,
                )
            connection.execute(publication.insert().values(**row))
        for kind in ("tool", "context", "unknown"):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    publication.insert().values(
                        **dict(
                            values[publication.name], id=new_id(), name=f"invalid-{kind}", kind=kind
                        )
                    )
                )


def test_classification_uses_bounded_sdk_identifier_domain(engine):
    from lotuspond_sdk.contract import Identifier
    from pydantic import TypeAdapter

    classification = TypeAdapter(Identifier).validate_python("customer-confidential")
    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        for name in ("execution_artifacts", "execution_context_versions"):
            table = Base.metadata.tables[name]
            connection.execute(table.update().values(classification=classification))
            for invalid in ("", "x" * 65):
                with pytest.raises((IntegrityError, DataError)), connection.begin_nested():
                    connection.execute(table.update().values(classification=invalid))


def test_events_accept_fixed_sdk_event_vocabulary_without_free_text(engine):
    from typing import get_args

    from lotuspond_sdk.executable.wire import EventKind

    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        event = models.ExecutionEvent.__table__
        for sequence, kind in enumerate(get_args(EventKind), start=2):
            connection.execute(
                event.insert().values(
                    **dict(
                        values[event.name],
                        id=new_id(),
                        family="sdk_runtime_event",
                        code=kind,
                        sequence=sequence,
                        source_sequence=sequence,
                        event_id=f"event-{sequence}",
                    )
                )
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(event.update().values(code="private provider failure"))


def test_event_gap_storage_survives_reconnect_and_bounds_pending(execution_database):
    from copy import deepcopy

    from sqlalchemy import func, select

    engine = execution_database()
    deepcopy(Base.metadata).create_all(engine)
    values = execution_rows()
    event = models.ExecutionEvent.__table__
    with engine.begin() as connection:
        load(connection, values)
        for slot in range(1, 101):
            connection.execute(
                event.insert().values(
                    **dict(
                        values[event.name],
                        id=new_id(),
                        event_id=f"gap-{slot}",
                        sequence=slot + 1,
                        source_sequence=slot + 1,
                        application_state="pending",
                        gap_slot=slot,
                    )
                )
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                event.insert().values(
                    **dict(
                        values[event.name],
                        id=new_id(),
                        event_id="gap-101",
                        sequence=102,
                        source_sequence=102,
                        application_state="pending",
                        gap_slot=101,
                    )
                )
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                event.insert().values(
                    **dict(
                        values[event.name],
                        id=new_id(),
                        event_id="gap-no-slot",
                        sequence=102,
                        source_sequence=102,
                        application_state="pending",
                        gap_slot=None,
                    )
                )
            )
    with engine.connect() as connection:
        restored = (
            connection.execute(event.select().where(event.c.event_id == "gap-1")).mappings().one()
        )
        assert restored["family"] == "execution_control" and restored["format_version"] == 1
        assert restored["source_generation"] == 1 and restored["application_state"] == "pending"
        artifact = (
            connection.execute(
                models.ExecutionArtifact.__table__.select().where(
                    models.ExecutionArtifact.id == restored["event_artifact_id"]
                )
            )
            .mappings()
            .one()
        )
        assert artifact["version"] == restored["event_artifact_version"]
        assert artifact["sha256"] == restored["digest"]
        assert artifact["ciphertext"] == values["execution_artifacts"]["ciphertext"]
    with engine.begin() as connection:
        for changes in (
            {"digest": "e" * 64},
            {"event_artifact_version": 2},
            {"source_generation": -1},
            {"recovery_epoch": 9007199254740992},
            {"format_version": 2},
            {"family": "untyped"},
            {"application_state": "applied"},
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    event.update().where(event.c.event_id == "gap-1").values(**changes)
                )
        connection.execute(event.update().values(application_state="applied", gap_slot=None))
        for sequence in range(102, 203):
            connection.execute(
                event.insert().values(
                    **dict(
                        values[event.name],
                        id=new_id(),
                        event_id=f"applied-{sequence}",
                        sequence=sequence,
                        source_sequence=sequence,
                    )
                )
            )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(event)
                .where(event.c.application_state == "applied")
            )
            == 202
        )


def test_event_generation_sequences_keep_global_source_event_replay_identity(engine):
    values = execution_rows()
    event = models.ExecutionEvent.__table__
    with engine.begin() as connection:
        load(connection, values)
        new_generation = dict(values[event.name], id=new_id(), sequence=2, source_generation=2)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(event.insert().values(**new_generation))
        connection.execute(
            event.insert().values(**dict(new_generation, event_id="new-generation-event"))
        )


def test_agent_catalog_publication_preserves_real_document_without_plan(engine):
    import hashlib
    import json
    from pathlib import Path

    from lotuspond_sdk.executable import canonical_catalog_document, validate_catalog

    source = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/examples/executable-v2/catalog.json"
        ).read_text()
    )
    catalog = validate_catalog(source)
    agent = next(document for document in catalog.documents if document.kind == "agent")
    model = next(
        document
        for document in catalog.documents
        if document.kind == "model" and document.name == agent.definition.model_ref.name
    )
    values = execution_rows()
    publication = models.ExecutablePublication.__table__
    dependency = models.ExecutableDependency.__table__
    with engine.begin() as connection:
        load(connection, values)
        stored = {}
        for document in (model, agent):
            canonical = canonical_catalog_document(document)
            if isinstance(canonical, str):
                canonical = canonical.encode()
            row = dict(
                values[publication.name],
                id=new_id(),
                name=document.name,
                kind=document.kind,
                version=document.version,
                canonical_package=canonical,
                package_sha256=hashlib.sha256(canonical).hexdigest(),
                canonical_plan=None,
                canonical_signature=None,
                plan_sha256=None,
                compiler_id=None,
            )
            connection.execute(publication.insert().values(**row))
            stored[document.kind] = row
        assert stored["model"]["package_sha256"] == agent.definition.model_ref.sha256
        connection.execute(
            dependency.insert().values(
                **dict(
                    values[dependency.name],
                    id=new_id(),
                    publication_id=stored["agent"]["id"],
                    dependency_publication_id=stored["model"]["id"],
                    dependency_sha256=stored["model"]["package_sha256"],
                    canonical_dependency=agent.definition.model_ref.model_dump_json().encode(),
                )
            )
        )


def test_admission_sequence_is_required_scoped_unique_and_safe(engine):
    values = execution_rows()
    with engine.begin() as connection:
        load(connection, values)
        table = models.Execution.__table__
        for value in (None, 0, -1, 9007199254740992):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(table.update().values(admission_sequence=value))
        duplicate = dict(values["executions"], id=new_id(), engine_identity=new_id())
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(table.insert().values(**duplicate))
        connection.execute(table.insert().values(**dict(duplicate, admission_sequence=2)))
        counter = models.ExecutionCapacity.__table__
        for value in (0, -1, 9007199254740992):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(counter.update().values(next_admission_sequence=value))
