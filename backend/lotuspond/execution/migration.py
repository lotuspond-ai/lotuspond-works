"""Additive exact-v13 migration. The caller owns its transaction and advisory lock.

No engine persistence, authority backfill, reset or downgrade belongs here.
"""

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import AddConstraint

from ..fleet.migration import ERROR, V12_CHECKS, _normalize_check
from ..models import EXECUTION_TABLES, Base, SchemaVersion

LEGACY_ADDITIONS = frozenset(
    {
        "api_keys_execution_scope_unique",
        "runs_execution_scope_unique",
        "releases_execution_candidate_unique",
    }
)


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


def _preflight(connection: Connection) -> None:
    inspector = inspect(connection)
    old_tables = {
        name: table for name, table in Base.metadata.tables.items() if name not in EXECUTION_TABLES
    }
    if set(inspector.get_table_names()) != set(old_tables):
        raise ValueError(ERROR)
    # Serialize against writes/DDL as well as other cooperating migrations. Static
    # model identifiers only; no caller-controlled table/column interpolation.
    quote = connection.dialect.identifier_preparer.quote
    tables = ", ".join(quote(name) for name in sorted(old_tables))
    connection.execute(text(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE"))
    inspector = inspect(connection)
    if list(connection.execute(select(SchemaVersion.version)).scalars()) != [13]:
        raise ValueError(ERROR)
    invalid = connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=current_schema() AND NOT c.convalidated)"
        )
    )
    if invalid:
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
    for name, table in old_tables.items():
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
            for c in table.columns
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
            if isinstance(constraint, UniqueConstraint) and constraint.name not in LEGACY_ADDITIONS
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
            _normalize_check(c) for c in V13_CHECKS[name]
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
        # Frozen v13 indexes use plain columns, default btree and only the
        # declared fleet status predicates. Refuse all other index options.
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


def migrate_execution_v14(connection: Connection) -> None:
    """Validate exact v13, append durable records, then advance the ledger last.

    Never commits; any later exception or explicit caller rollback also removes
    every new table/constraint and restores the original v13 ledger and rows.
    """
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise ValueError(ERROR)
    _preflight(connection)
    for name in ("api_keys", "runs", "releases"):
        for constraint in Base.metadata.tables[name].constraints:
            if constraint.name in LEGACY_ADDITIONS:
                connection.execute(AddConstraint(constraint, isolate_from_table=False))
    for table in Base.metadata.sorted_tables:
        if table.name in EXECUTION_TABLES:
            table.create(connection)
    for table in Base.metadata.sorted_tables:
        if table.name in EXECUTION_TABLES:
            for constraint in table.foreign_key_constraints:
                if constraint.use_alter:
                    connection.execute(AddConstraint(constraint, isolate_from_table=False))
    connection.execute(SchemaVersion.__table__.update().values(version=14))


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
