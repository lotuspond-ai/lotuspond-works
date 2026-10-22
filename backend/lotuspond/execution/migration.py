"""Additive exact-v13 → v14 and exact-v14 → v15 migrations.

The caller owns its transaction and advisory lock. Engine state is product schema
(ADR-0011): v15 adds only the orchestration claim marker and sweep indexes. No
authority backfill, reset or downgrade belongs here.
"""

from copy import deepcopy

from sqlalchemy import CheckConstraint, UniqueConstraint, bindparam, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import AddConstraint, CreateColumn, CreateIndex, CreateTable

from ..fleet.migration import ERROR, V12_CHECKS, _normalize_check
from ..models import EXECUTION_TABLES, Base, SchemaVersion

LEGACY_ADDITIONS = frozenset(
    {
        "api_keys_execution_scope_unique",
        "runs_execution_scope_unique",
        "releases_execution_candidate_unique",
    }
)
# Schema v15 objects. Exact v14, and the direct v14 helper, contain none of them.
V15_COLUMNS = {"executions": ("runnable_at_ms",)}
V15_CHECKS = frozenset({"executions_runnable_at_ms_check", "executions_runnable_terminal_check"})
V15_INDEXES = {
    "executions": ("execution_runnable_idx",),
    "execution_waits": ("execution_wait_due_idx",),
    "execution_leases": ("execution_lease_due_idx",),
    "execution_activity_deliveries": ("execution_delivery_due_idx",),
}


def _v14_metadata():
    """A private copy of the authoritative model without any v15 object."""
    metadata = deepcopy(Base.metadata)
    for name, names in V15_INDEXES.items():
        table = metadata.tables[name]
        for index in [i for i in table.indexes if i.name in names]:
            table.indexes.discard(index)
    executions = metadata.tables["executions"]
    for constraint in [c for c in executions.constraints if c.name in V15_CHECKS]:
        executions.constraints.discard(constraint)
    return metadata


def _columns(table):
    return [c for c in table.columns if c.name not in V15_COLUMNS.get(table.name, ())]


def _create(connection, table):
    """Equivalent to Table.create, restricted to the table's v14 columns."""
    statement = CreateTable(table)
    statement.columns = [
        c for c in statement.columns if c.element.name not in V15_COLUMNS.get(table.name, ())
    ]
    connection.execute(statement)
    for index in sorted(table.indexes, key=lambda index: index.name):
        connection.execute(CreateIndex(index))


def _predicate(value):
    if value is None:
        return ""
    value = _normalize_check(str(value))
    # PostgreSQL wraps the only frozen v13 predicates: status = '<literal>'.
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    if value in {"(status) = 'active'", "(status) = 'issued'", "(status) = 'pending'"}:
        value = value.replace("(status)", "status", 1)
    return value


def _lock(connection, tables):
    # Serialize against writes/DDL as well as other cooperating migrations. Static
    # model identifiers only; no caller-controlled table/column interpolation.
    quote = connection.dialect.identifier_preparer.quote
    names = ", ".join(quote(name) for name in sorted(tables))
    connection.execute(text(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE"))


def _version(connection, expected):
    if list(connection.execute(select(SchemaVersion.version)).scalars()) != [expected]:
        raise ValueError(ERROR)


def _verify_catalog(connection: Connection) -> None:
    invalid = connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=current_schema() AND NOT c.convalidated)"
        )
    )
    if invalid:
        raise ValueError(ERROR)
    # Frozen schemas have no user triggers or table rewrite rules. These can
    # suppress the ledger write or mutate unrelated rows even when all
    # constraints match. Refuse before DDL; never repair operator-owned objects.
    unexpected_hooks = connection.scalar(
        text(
            "SELECT EXISTS (SELECT FROM pg_trigger h JOIN pg_class t ON t.oid=h.tgrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=current_schema() AND t.relkind IN ('r','p') "
            "AND NOT h.tgisinternal) OR EXISTS "
            "(SELECT FROM pg_rewrite r JOIN pg_class t ON t.oid=r.ev_class "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=current_schema() AND t.relkind IN ('r','p'))"
        )
    )
    if unexpected_hooks:
        raise ValueError(ERROR)
    # A validated FK is insufficient when its internal insert/update or
    # referenced-table delete/update triggers do not enforce normal writes.
    # Refuse the operator's state unchanged; never silently enable triggers.
    unenforced = connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c "
            "JOIN pg_class child ON child.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=child.relnamespace "
            "JOIN pg_trigger t ON t.tgconstraint=c.oid "
            "WHERE n.nspname=current_schema() AND c.contype='f' "
            "AND t.tgenabled NOT IN ('O','A'))"
        )
    )
    if unenforced:
        raise ValueError(ERROR)
    invalid_catalog = connection.scalar(
        text(
            "SELECT EXISTS (SELECT FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=current_schema() "
            "AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indislive)) OR EXISTS "
            "(SELECT FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=current_schema() "
            "AND c.contype IN ('p','u') AND (c.condeferrable OR c.condeferred))"
        )
    )
    if invalid_catalog:
        raise ValueError(ERROR)


def _verify_tables(connection: Connection, tables, frozen_checks, excluded_unique=frozenset()):
    """Compare every table with its frozen definition and recheck stored values."""
    inspector = inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    for name, table in tables.items():
        columns = inspector.get_columns(name)
        expected = [
            (
                c.name,
                str(c.type.compile(dialect=connection.dialect)),
                c.nullable,
                (
                    "nextval('schema_version_version_seq'::regclass)"
                    if name == "schema_version" and c.name == "version"
                    else None
                ),
            )
            for c in _columns(table)
        ]
        actual = [
            (
                c["name"],
                str(c["type"].compile(dialect=connection.dialect)),
                c["nullable"],
                c["default"],
            )
            for c in columns
        ]
        if sorted(actual) != sorted(expected):
            raise ValueError(ERROR)
        if inspector.get_pk_constraint(name)["constrained_columns"] != [
            c.name for c in table.primary_key.columns
        ]:
            raise ValueError(ERROR)
        expected_unique = {
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint) and constraint.name not in excluded_unique
        }
        actual_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
        if actual_unique != expected_unique or len(inspector.get_unique_constraints(name)) != len(
            expected_unique
        ):
            raise ValueError(ERROR)
        option_names = ("onupdate", "ondelete", "deferrable", "initially", "match")
        expected_fks = {
            (
                tuple(element.parent.name for element in fk.elements),
                tuple(element.column.name for element in fk.elements),
                fk.elements[0].column.table.name,
                tuple(getattr(fk, option) for option in option_names),
            )
            for fk in table.foreign_key_constraints
        }
        actual_fks = {
            (
                tuple(fk["constrained_columns"]),
                tuple(fk["referred_columns"]),
                fk["referred_table"],
                tuple(fk["options"].get(option) for option in option_names),
            )
            for fk in inspector.get_foreign_keys(name)
        }
        if any(
            fk.get("referred_schema")
            not in {None, connection.scalar(text("SELECT current_schema()"))}
            for fk in inspector.get_foreign_keys(name)
        ):
            raise ValueError(ERROR)
        if actual_fks != expected_fks or len(inspector.get_foreign_keys(name)) != len(expected_fks):
            raise ValueError(ERROR)
        checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
        actual_checks = inspector.get_check_constraints(name)
        if sorted(_normalize_check(c["sqltext"]) for c in actual_checks) != sorted(
            _normalize_check(c) for c in frozen_checks[name]
        ) or not {c.name for c in checks if c.name}.issubset({c["name"] for c in actual_checks}):
            raise ValueError(ERROR)
        expected_indexes = {
            (
                i.name,
                tuple(c.name for c in i.columns),
                bool(i.unique),
                _predicate(i.dialect_options["postgresql"].get("where")),
            )
            for i in table.indexes
        }
        indexes = [i for i in inspector.get_indexes(name) if not i.get("duplicates_constraint")]
        # Frozen indexes use plain columns, default btree and only the declared
        # fleet status predicates. Refuse all other index options.
        if any(
            i.get("expressions")
            or i.get("column_sorting")
            or any(
                value not in (None, False, {}, [])
                for key, value in i.get("dialect_options", {}).items()
                if key != "postgresql_where"
            )
            for i in indexes
        ):
            raise ValueError(ERROR)
        actual_indexes = {
            (
                i["name"],
                tuple(i["column_names"]),
                bool(i["unique"]),
                _predicate(i.get("dialect_options", {}).get("postgresql_where")),
            )
            for i in indexes
        }
        if actual_indexes != expected_indexes:
            raise ValueError(ERROR)
        # A disabled trigger could have allowed existing orphan rows even while
        # the catalog says the FK is validated. Check real values independently.
        for fields, targets, parent, _options in expected_fks:
            present = " AND ".join(f"child.{quote(c)} IS NOT NULL" for c in fields)
            joined = " AND ".join(
                f"parent.{quote(target)} = child.{quote(field)}"
                for field, target in zip(fields, targets, strict=True)
            )
            if connection.scalar(
                text(
                    f"SELECT EXISTS (SELECT 1 FROM {quote(name)} child WHERE {present} AND NOT EXISTS (SELECT 1 FROM {quote(parent)} parent WHERE {joined}))"
                )
            ):
                raise ValueError(ERROR)
        for check in checks:
            if connection.scalar(
                text(f"SELECT EXISTS (SELECT 1 FROM {quote(name)} WHERE NOT ({check.sqltext}))")
            ):
                raise ValueError(ERROR)


def _preflight(connection: Connection) -> None:
    inspector = inspect(connection)
    old_tables = {
        name: table for name, table in Base.metadata.tables.items() if name not in EXECUTION_TABLES
    }
    if set(inspector.get_table_names()) != set(old_tables):
        raise ValueError(ERROR)
    _lock(connection, old_tables)
    _version(connection, 13)
    _verify_catalog(connection)
    _verify_tables(connection, old_tables, V13_CHECKS, LEGACY_ADDITIONS)


def migrate_execution_v14(connection: Connection) -> None:
    """Validate exact v13, append durable records, then advance the ledger last.

    Never commits; any later exception or explicit caller rollback also removes
    every new table/constraint and restores the original v13 ledger and rows.
    Creates exactly the v14 tables; the v15 step alone adds its objects.
    """
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise ValueError(ERROR)
    _preflight(connection)
    for name in ("api_keys", "runs", "releases"):
        for constraint in Base.metadata.tables[name].constraints:
            if constraint.name in LEGACY_ADDITIONS:
                connection.execute(AddConstraint(constraint, isolate_from_table=False))
    metadata = _v14_metadata()
    for table in metadata.sorted_tables:
        if table.name in EXECUTION_TABLES:
            _create(connection, table)
    for table in metadata.sorted_tables:
        if table.name in EXECUTION_TABLES:
            for constraint in table.foreign_key_constraints:
                if constraint.use_alter:
                    connection.execute(AddConstraint(constraint, isolate_from_table=False))
    result = connection.execute(
        SchemaVersion.__table__.update().where(SchemaVersion.version == 13).values(version=14)
    )
    if result.rowcount != 1 or list(
        connection.execute(select(SchemaVersion.version)).scalars()
    ) != [14]:
        raise ValueError(ERROR)


# Frozen PostgreSQL expressions captured from the committed BASE v13 fixture.
# V12 expressions are unchanged; fleet expressions below complete that baseline.
V13_CHECKS = V12_CHECKS | {
    "connection_version_secrets": [
        "connection_version >= 1 AND connection_version <= 2147483647",
        "secret_version >= 1 AND secret_version <= 2147483647",
    ],
    "connection_versions": [
        "length(canonical_config) >= 1 AND length(canonical_config) <= 32768",
        "length(config_digest::text) = 64",
        "version >= 1 AND version <= 2147483647",
    ],
    "connections": [
        "status::text = ANY (ARRAY['active'::character varying, "
        "'disabled'::character varying, 'revoked'::character varying]::text[])"
    ],
    "fleet_mutation_requests": [
        "length(request_digest::text) = 64",
        "length(request_key::text) >= 1 AND length(request_key::text) <= 128",
    ],
    "runtime_command_admissions": [
        "admitted_at_ms >= 0 AND admitted_at_ms <= '9007199254740991'::bigint",
        "status::text <> 'terminal'::text OR terminal_at_ms IS NOT "
        "NULL AND retain_until_ms IS NOT NULL AND retain_until_ms >= "
        "(terminal_at_ms + 86400000) AND retain_until_ms >= "
        "(command_expires_at_ms + 30000)",
        "claimed_at_ms IS NULL OR claimed_at_ms >= 0 AND "
        "claimed_at_ms <= '9007199254740991'::bigint",
        "length(command_bytes) >= 1 AND length(command_bytes) <= 65536",
        "length(command_digest::text) = 64",
        "command_expires_at_ms >= 0 AND command_expires_at_ms <= '9007199254740991'::bigint",
        "generation >= 1 AND generation <= '9007199254740991'::bigint",
        "length(installation_id::text) >= 1 AND length(installation_id::text) <= 128",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "retain_until_ms IS NULL OR retain_until_ms >= 0 AND "
        "retain_until_ms <= '9007199254740991'::bigint",
        "length(signature_bytes) >= 1 AND length(signature_bytes) <= 65536",
        "status::text = ANY (ARRAY['admitted'::character varying, "
        "'claimed'::character varying, 'terminal'::character varying, "
        "'uncertain'::character varying]::text[])",
        "terminal_at_ms IS NULL OR terminal_at_ms >= 0 AND "
        "terminal_at_ms <= '9007199254740991'::bigint",
        "trust_revision >= 1 AND trust_revision <= '9007199254740991'::bigint",
    ],
    "runtime_credentials": [
        "activation_deadline_ms IS NULL OR activation_deadline_ms >= 0 AND "
        "activation_deadline_ms <= '9007199254740991'::bigint",
        "status::text <> 'pending'::text OR predecessor_id IS NOT NULL AND "
        "session_id IS NOT NULL AND generation IS NOT NULL AND "
        "prepared_policy_version IS NOT NULL AND activation_deadline_ms IS "
        "NOT NULL",
        "session_id IS NULL AND generation IS NULL OR session_id IS NOT NULL "
        "AND generation IS NOT NULL",
        "predecessor_id IS NULL OR predecessor_id::text <> id::text",
        "successor_id IS NULL OR successor_id::text <> id::text",
        "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
        "generation IS NULL OR generation >= 1 AND generation <= '9007199254740991'::bigint",
        "length(installation_id::text) >= 1 AND length(installation_id::text) <= 128",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "status::text = ANY (ARRAY['pending'::character varying, "
        "'active'::character varying, 'revoked'::character varying]::text[])",
        "length(token_digest::text) = 64",
        "version >= 1 AND version <= 2147483647",
    ],
    "runtime_enrollments": [
        "consumed_at_ms IS NULL OR consumed_at_ms >= 0 AND consumed_at_ms <= "
        "'9007199254740991'::bigint",
        "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
        "length(installation_id::text) >= 1 AND length(installation_id::text) <= 128",
        "length(observation_ceiling) >= 1 AND length(observation_ceiling) <= 32768",
        "policy_version >= 1 AND policy_version <= 2147483647",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "status::text = ANY (ARRAY['issued'::character varying, "
        "'consumed'::character varying, 'revoked'::character "
        "varying]::text[])",
        "length(token_digest::text) = 64",
    ],
    "runtime_instances": [
        "generation >= 0 AND generation <= '9007199254740991'::bigint",
        "length(installation_id::text) >= 1 AND length(installation_id::text) <= 128",
        "last_seen_at_ms IS NULL OR last_seen_at_ms >= 0 AND last_seen_at_ms "
        "<= '9007199254740991'::bigint",
        "length(observation_ceiling) >= 1 AND length(observation_ceiling) <= 32768",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "status::text = ANY (ARRAY['active'::character varying, "
        "'revoked'::character varying]::text[])",
    ],
    "runtime_pool_policies": [
        "length(canonical_policy) >= 1 AND length(canonical_policy) <= 32768",
        "length(policy_digest::text) = 64",
        "version >= 1 AND version <= 2147483647",
    ],
    "runtime_pool_policy_connections": ["policy_version >= 1 AND policy_version <= 2147483647"],
    "runtime_pools": [
        "current_policy_version IS NULL OR current_policy_version >= 1 AND "
        "current_policy_version <= 2147483647",
        "status::text = ANY (ARRAY['active'::character varying, "
        "'disabled'::character varying, 'revoked'::character varying]::text[])",
    ],
    "runtime_sessions": [
        "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
        "generation >= 1 AND generation <= '9007199254740991'::bigint",
        "length(installation_id::text) >= 1 AND length(installation_id::text) <= 128",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "status::text = ANY (ARRAY['active'::character varying, "
        "'revoked'::character varying]::text[])",
        "length(token_digest::text) = 64",
    ],
    "secret_references": [
        "status::text = ANY (ARRAY['active'::character varying, "
        "'revoked'::character varying]::text[])",
        "version >= 1 AND version <= 2147483647",
    ],
}


# Frozen PostgreSQL expressions captured from the committed v14 fixture
# (tests/execution/fixtures/schema-v14.sql). Legacy and fleet tables are unchanged.
V14_CHECKS = V13_CHECKS | {
    "deployment_assurance_bindings": [],
    "deployment_bindings": [
        "(connection_id IS NULL) = (connection_version IS NULL)",
        "(source_id IS NULL) = (source_version IS NULL)",
    ],
    "executable_dependencies": [
        "length(canonical_dependency) >= 1 AND length(canonical_dependency) <= 65536",
        "length(dependency_sha256::text) = 64",
    ],
    "executable_publications": [
        "(canonical_plan IS NULL) = (compiler_id IS NULL)",
        "(canonical_plan IS NULL) = (plan_sha256 IS NULL)",
        "(kind::text = 'workflow'::text) = (canonical_plan IS NOT NULL)",
        "(kind::text = 'workflow'::text) = (canonical_signature IS NOT NULL)",
        "kind::text = ANY (ARRAY['workflow'::character varying, 'connector'::character "
        "varying, 'model'::character varying, 'agent'::character varying, "
        "'evaluation'::character varying, 'policy'::character varying, "
        "'knowledge'::character varying, 'artifact'::character varying]::text[])",
        "length(canonical_package) >= 1 AND length(canonical_package) <= 1048576",
        "length(canonical_plan) >= 1 AND length(canonical_plan) <= 1048576",
        "length(canonical_signature) >= 1 AND length(canonical_signature) <= 65536",
        "length(package_sha256::text) = 64",
        "length(plan_sha256::text) = 64",
        "status::text = ANY (ARRAY['active'::character varying, 'revoked'::character "
        "varying]::text[])",
    ],
    "execution_activity_deliveries": [
        "expires_at_ms > issued_at_ms AND expires_at_ms <= (issued_at_ms + 300000)",
        "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
        "generation >= 1 AND generation <= '9007199254740991'::bigint",
        "issued_at_ms >= 0 AND issued_at_ms <= '9007199254740991'::bigint",
        "length(canonical_envelope) > 0 AND length(canonical_signature) > 0 AND "
        "(length(canonical_envelope) + length(canonical_signature)) <= 65536",
        "length(digest::text) = 64",
        "length(installation_id::text) >= 1 AND length(installation_id::text) <= 128",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "retain_until_ms >= (expires_at_ms + 30000)",
        "retain_until_ms >= 0 AND retain_until_ms <= '9007199254740991'::bigint",
        "status::text = ANY (ARRAY['pending'::character varying, 'leased'::character "
        "varying, 'delivered'::character varying, 'conflict'::character "
        "varying]::text[])",
    ],
    "execution_artifacts": [
        "(source_id IS NULL) = (source_version IS NULL)",
        "content_bytes >= 1 AND content_bytes <= 1048576",
        "length(ciphertext) = (content_bytes + 16)",
        "length(classification::text) >= 1 AND length(classification::text) <= 64",
        "length(nonce) = 12",
        "length(sha256::text) = 64",
        "retain_until_ms >= 0 AND retain_until_ms <= '9007199254740991'::bigint",
        "version >= 1 AND version <= 2147483647",
    ],
    "execution_attempts": [
        "(parent_attempt_id IS NULL) = (child_request_digest IS NULL)",
        "(parent_attempt_id IS NULL) = (child_request_id IS NULL)",
        "(result_artifact_id IS NULL) = (result_artifact_version IS NULL)",
        "(result_artifact_id IS NULL) = (result_sha256 IS NULL)",
        "length(child_request_digest::text) = 64",
        "ordinal >= 1 AND ordinal <= 2147483647",
        "parent_attempt_id IS NULL OR parent_attempt_id::text <> id::text",
        "state::text = ANY (ARRAY['reserved'::character varying, "
        "'dispatched'::character varying, 'claimed'::character varying, "
        "'succeeded'::character varying, 'failed'::character varying, "
        "'uncertain'::character varying, 'cancelled'::character varying]::text[])",
    ],
    "execution_capacities": [
        "active_limit >= 0 AND active_limit <= 5 AND active >= 0 AND active <= active_limit",
        "next_admission_sequence >= 1 AND next_admission_sequence <= '9007199254740991'::bigint",
        "pending_limit >= 0 AND pending_limit <= 1000 AND pending >= 0 AND pending <= "
        "pending_limit",
        "wait_limit >= 0 AND wait_limit <= 10000 AND retained_waits >= 0 AND "
        "retained_waits <= wait_limit",
    ],
    "execution_context_sources": [
        "status::text = ANY (ARRAY['active'::character varying, 'revoked'::character "
        "varying, 'deleted'::character varying]::text[])",
    ],
    "execution_context_versions": [
        "(citation_artifact_id IS NULL) = (citation_artifact_version IS NULL)",
        "(citation_artifact_id IS NULL) = (citation_sha256 IS NULL)",
        "(content_artifact_id IS NULL) = (content_artifact_version IS NULL)",
        "(content_artifact_id IS NULL) = (content_sha256 IS NULL)",
        "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
        "expires_at_ms >= observed_at_ms",
        "length(classification::text) >= 1 AND length(classification::text) <= 64",
        "observed_at_ms >= 0 AND observed_at_ms <= '9007199254740991'::bigint",
        "version >= 1 AND version <= 2147483647",
    ],
    "execution_control_requests": [
        "action::text = ANY (ARRAY['pause'::character varying, 'resume'::character "
        "varying, 'cancel'::character varying]::text[])",
        "length(digest::text) = 64",
    ],
    "execution_deployment_revisions": [
        "length(canonical_configuration) >= 1 AND length(canonical_configuration) <= 1048576",
        "length(configuration_sha256::text) = 64",
        "revision >= 1 AND revision <= 2147483647",
        "status::text = ANY (ARRAY['active'::character varying, 'disabled'::character "
        "varying, 'revoked'::character varying]::text[])",
    ],
    "execution_deployments": [
        "status::text = ANY (ARRAY['active'::character varying, 'disabled'::character "
        "varying, 'revoked'::character varying]::text[])",
    ],
    "execution_events": [
        "(application_state::text = 'pending'::text) = (gap_slot IS NOT NULL)",
        "application_state::text = ANY (ARRAY['pending'::character varying, "
        "'applied'::character varying, 'conflict'::character varying]::text[])",
        "event_artifact_version > 0",
        "family::text = 'sdk_runtime_event'::text AND (code::text = ANY "
        "(ARRAY['admission'::character varying, 'step_started'::character varying, "
        "'step_result'::character varying, 'step_failed'::character varying, "
        "'wait_started'::character varying, 'signal_received'::character varying, "
        "'timer_elapsed'::character varying, 'intent_created'::character varying, "
        "'decision_recorded'::character varying, 'action_dispatched'::character "
        "varying, 'action_receipt'::character varying, 'dispatch_timeout'::character "
        "varying, 'reconciliation_observed'::character varying, "
        "'outcome_recorded'::character varying, 'execution_completed'::character "
        "varying, 'cancellation_requested'::character varying, "
        "'execution_failed'::character varying]::text[])) OR family::text = "
        "'execution_control'::text AND (code::text = ANY (ARRAY['admitted'::character "
        "varying, 'started'::character varying, 'attempt_reserved'::character varying, "
        "'attempt_dispatched'::character varying, 'attempt_claimed'::character varying, "
        "'attempt_succeeded'::character varying, 'attempt_failed'::character varying, "
        "'attempt_uncertain'::character varying, 'waiting'::character varying, "
        "'signal_received'::character varying, 'signal_conflict'::character varying, "
        "'paused'::character varying, 'resumed'::character varying, "
        "'cancelled'::character varying, 'succeeded'::character varying, "
        "'failed'::character varying, 'reconciliation_required'::character varying, "
        "'capacity_refused'::character varying, 'budget_refused'::character varying, "
        "'authority_revoked'::character varying, 'deadline_exceeded'::character "
        "varying, 'reconciled'::character varying]::text[]))",
        "format_version = 1",
        "gap_slot >= 1 AND gap_slot <= 100",
        "length(digest::text) = 64",
        "length(event_id::text) >= 1 AND length(event_id::text) <= 128",
        "length(installation_id::text) >= 1 AND length(installation_id::text) <= 128",
        "length(source_id::text) >= 1 AND length(source_id::text) <= 128",
        "occurred_at_ms >= 0 AND occurred_at_ms <= '9007199254740991'::bigint",
        "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
        "sequence >= 1 AND sequence <= '9007199254740991'::bigint",
        "source_generation >= 0 AND source_generation <= '9007199254740991'::bigint",
        "source_sequence >= 0 AND source_sequence <= '9007199254740991'::bigint",
    ],
    "execution_inbox": [
        "(leased_at_ms IS NULL) = (lease_expires_at_ms IS NULL)",
        "(leased_at_ms IS NULL) = (lease_token_digest IS NULL)",
        "available_at_ms >= 0 AND available_at_ms <= '9007199254740991'::bigint",
        "delivery_count >= 0 AND delivery_count <= 2147483647",
        "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
        "lease_expires_at_ms > leased_at_ms AND lease_expires_at_ms <= (leased_at_ms + 60000)",
        "lease_expires_at_ms >= 0 AND lease_expires_at_ms <= '9007199254740991'::bigint",
        "leased_at_ms >= 0 AND leased_at_ms <= '9007199254740991'::bigint",
        "length(digest::text) = 64",
        "length(lease_token_digest::text) = 64",
        "status::text <> 'leased'::text OR leased_at_ms IS NOT NULL",
        "status::text = ANY (ARRAY['pending'::character varying, 'leased'::character "
        "varying, 'delivered'::character varying, 'conflict'::character "
        "varying]::text[])",
    ],
    "execution_leases": [
        "claimed_at_ms >= 0 AND claimed_at_ms <= '9007199254740991'::bigint",
        "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
        "length(token_digest::text) = 64",
        "renewed_at_ms >= 0 AND renewed_at_ms <= '9007199254740991'::bigint",
        "renewed_at_ms >= claimed_at_ms AND expires_at_ms > renewed_at_ms AND "
        "expires_at_ms <= (renewed_at_ms + 60000)",
        "status::text = ANY (ARRAY['active'::character varying, 'stopped'::character "
        "varying, 'uncertain'::character varying]::text[])",
    ],
    "execution_outbox": [
        "(leased_at_ms IS NULL) = (lease_expires_at_ms IS NULL)",
        "(leased_at_ms IS NULL) = (lease_token_digest IS NULL)",
        "available_at_ms >= 0 AND available_at_ms <= '9007199254740991'::bigint",
        "delivery_count >= 0 AND delivery_count <= 2147483647",
        "kind::text = ANY (ARRAY['engine_start'::character varying, "
        "'engine_signal'::character varying, 'activity'::character varying, "
        "'control'::character varying]::text[])",
        "lease_expires_at_ms > leased_at_ms AND lease_expires_at_ms <= (leased_at_ms + 60000)",
        "lease_expires_at_ms >= 0 AND lease_expires_at_ms <= '9007199254740991'::bigint",
        "leased_at_ms >= 0 AND leased_at_ms <= '9007199254740991'::bigint",
        "length(digest::text) = 64",
        "length(lease_token_digest::text) = 64",
        "status::text <> 'leased'::text OR leased_at_ms IS NOT NULL",
        "status::text = ANY (ARRAY['pending'::character varying, 'leased'::character "
        "varying, 'delivered'::character varying, 'conflict'::character "
        "varying]::text[])",
    ],
    "execution_reconciliation_observations": [
        "code::text = ANY (ARRAY['compute_stopped'::character varying, "
        "'compute_running'::character varying, 'outcome_unknown'::character varying, "
        "'usage_known'::character varying, 'authority_revoked'::character varying, "
        "'engine_missing'::character varying, 'identity_conflict'::character "
        "varying]::text[])",
        "length(digest::text) = 64",
        "observed_at_ms >= 0 AND observed_at_ms <= '9007199254740991'::bigint",
    ],
    "execution_requests": [
        "length(digest::text) = 64",
        "length(request_key::text) >= 1 AND length(request_key::text) <= 128",
    ],
    "execution_scheduler_cursors": [
        "active_limit >= 0 AND active_limit <= 25 AND active >= 0 AND active <= active_limit",
        "cursor >= 0 AND cursor <= '9007199254740991'::bigint",
    ],
    "execution_step_instances": [
        "length(occurrence_path::text) >= 1 AND length(occurrence_path::text) <= 1024",
    ],
    "execution_usage_reservations": [
        "active_ms >= 0 AND active_ms <= '9007199254740991'::bigint",
        "completed_at_ms >= 0 AND completed_at_ms <= '9007199254740991'::bigint",
        "compute_stopped_at_ms >= 0 AND compute_stopped_at_ms <= '9007199254740991'::bigint",
        "cost_microusd >= 0 AND cost_microusd <= '9007199254740991'::bigint",
        "input_tokens >= 0 AND input_tokens <= '9007199254740991'::bigint",
        "length(settlement_digest::text) = 64",
        "observed_cost_microusd >= 0 AND observed_cost_microusd <= '9007199254740991'::bigint",
        "observed_input_tokens >= 0 AND observed_input_tokens <= '9007199254740991'::bigint",
        "observed_output_tokens >= 0 AND observed_output_tokens <= '9007199254740991'::bigint",
        "output_tokens >= 0 AND output_tokens <= '9007199254740991'::bigint",
        "state::text <> 'released'::text OR compute_stopped_at_ms IS NOT NULL",
        "state::text <> 'settled'::text OR observed_input_tokens IS NOT NULL AND "
        "observed_output_tokens IS NOT NULL AND observed_cost_microusd IS NOT NULL AND "
        "completed_at_ms IS NOT NULL AND settlement_digest IS NOT NULL",
        "state::text = ANY (ARRAY['reserved'::character varying, 'settled'::character "
        "varying, 'uncertain'::character varying, 'released'::character "
        "varying]::text[])",
    ],
    "execution_waits": [
        "deadline_ms >= 0 AND deadline_ms <= '9007199254740991'::bigint",
        "state::text = ANY (ARRAY['open'::character varying, 'satisfied'::character "
        "varying, 'expired'::character varying, 'cancelled'::character "
        "varying]::text[])",
    ],
    "executions": [
        "(result_artifact_id IS NULL) = (result_artifact_version IS NULL)",
        "(result_artifact_id IS NULL) = (result_sha256 IS NULL)",
        "admission_sequence >= 1 AND admission_sequence <= '9007199254740991'::bigint",
        "admitted_at_ms >= 0 AND admitted_at_ms <= '9007199254740991'::bigint",
        "deadline_ms > admitted_at_ms",
        "deadline_ms >= 0 AND deadline_ms <= '9007199254740991'::bigint",
        "length(admission_digest::text) = 64",
        "next_event_sequence >= 1 AND next_event_sequence <= '9007199254740991'::bigint",
        "reason::text = ANY (ARRAY['capacity'::character varying, 'budget'::character "
        "varying, 'entitlement_unavailable'::character varying, "
        "'authority_revoked'::character varying, 'deadline_exceeded'::character "
        "varying, 'signal'::character varying, 'control'::character varying, "
        "'provider_unavailable'::character varying, 'uncertain'::character varying, "
        "'completed'::character varying, 'invalid_result'::character varying]::text[])",
        "requested_state::text = ANY (ARRAY['running'::character varying, "
        "'paused'::character varying, 'cancelled'::character varying]::text[])",
        "state::text = ANY (ARRAY['pending'::character varying, 'running'::character "
        "varying, 'waiting'::character varying, 'paused'::character varying, "
        "'cancelled'::character varying, 'succeeded'::character varying, "
        "'failed'::character varying, 'reconciliation_required'::character "
        "varying]::text[])",
    ],
}


V15_WAKEUPS = ("engine_start", "engine_signal", "control")


def _preflight_v15(connection: Connection) -> None:
    tables = dict(_v14_metadata().tables)
    if set(inspect(connection).get_table_names()) != set(tables):
        raise ValueError(ERROR)
    _lock(connection, tables)
    _version(connection, 14)
    _verify_catalog(connection)
    _verify_tables(connection, tables, V14_CHECKS)


def migrate_execution_v15(connection: Connection) -> None:
    """Validate exact v14, add the runnable marker and sweep indexes, then the ledger.

    Never commits. Existing non-terminal executions that still hold a pending
    start, signal or control outbox row become runnable at that row's time, so
    v14 admissions are not stranded. Rows `leased` through Task 6's external
    consumer protocol stay with that protocol; no v14 code path leases them.
    No other column, constraint or row changes.
    """
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise ValueError(ERROR)
    _preflight_v15(connection)
    executions = Base.metadata.tables["executions"]
    for name in V15_COLUMNS["executions"]:
        column = CreateColumn(executions.c[name]).compile(dialect=connection.dialect)
        connection.execute(text(f"ALTER TABLE executions ADD COLUMN {column}"))
    for constraint in sorted(
        (c for c in executions.constraints if c.name in V15_CHECKS), key=lambda c: c.name
    ):
        connection.execute(AddConstraint(constraint, isolate_from_table=False))
    for name, names in sorted(V15_INDEXES.items()):
        for index in sorted(
            (i for i in Base.metadata.tables[name].indexes if i.name in names),
            key=lambda i: i.name,
        ):
            connection.execute(CreateIndex(index))
    connection.execute(
        text(
            "UPDATE executions AS e SET runnable_at_ms = due.at_ms FROM ("
            "SELECT o.workspace_id, o.project_id, o.environment_id, o.execution_id, "
            "min(o.available_at_ms) AS at_ms FROM execution_outbox o "
            "WHERE o.status = 'pending' AND o.kind IN :kinds "
            "GROUP BY o.workspace_id, o.project_id, o.environment_id, o.execution_id) AS due "
            "WHERE e.workspace_id = due.workspace_id AND e.project_id = due.project_id "
            "AND e.environment_id = due.environment_id AND e.id = due.execution_id "
            "AND e.state NOT IN ('succeeded', 'failed', 'cancelled')"
        ).bindparams(bindparam("kinds", value=V15_WAKEUPS, expanding=True))
    )
    result = connection.execute(
        SchemaVersion.__table__.update().where(SchemaVersion.version == 14).values(version=15)
    )
    if result.rowcount != 1 or list(
        connection.execute(select(SchemaVersion.version)).scalars()
    ) != [15]:
        raise ValueError(ERROR)
