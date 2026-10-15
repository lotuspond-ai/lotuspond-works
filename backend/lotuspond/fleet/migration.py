"""Additive fleet v13 migration, inside the caller's transaction/advisory lock.

Recovery is forward repair. Older applications intentionally refuse schema v13;
there is no destructive downgrade and no automatic customer authority backfill.
"""

import re

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import AddConstraint

from ..models import Base, SchemaVersion

FLEET_TABLES = frozenset(
    {
        "connections",
        "connection_versions",
        "secret_references",
        "runtime_pools",
        "runtime_pool_policies",
        "connection_version_secrets",
        "runtime_pool_policy_connections",
        "runtime_instances",
        "runtime_enrollments",
        "runtime_credentials",
        "runtime_sessions",
        "runtime_command_admissions",
        "fleet_mutation_requests",
    }
)
ERROR = "Unexpected schema state; no changes made"


def _normalize_check(expression: str) -> str:
    # Normalize accepted PostgreSQL cast rendering only between quoted regions.
    # Literal bytes, doubled quotes, quoted identifiers and E-string escapes
    # retain their exact meaning; this is not a general SQL equivalence parser.
    cast = re.compile(r"::(?:character varying|text|numeric)(?:\[\])?(?![\w$])")
    parts = []
    outside_start = cursor = 0
    while cursor < len(expression):
        quote = expression[cursor]
        if quote not in {"'", '"'}:
            cursor += 1
            continue
        parts.append(cast.sub("", expression[outside_start:cursor]))
        quoted_start = cursor
        escapes = (
            quote == "'"
            and cursor > 0
            and expression[cursor - 1] in "eE"
            and (
                cursor < 2
                or not (expression[cursor - 2].isalnum() or expression[cursor - 2] in "_$")
            )
        )
        cursor += 1
        while cursor < len(expression):
            if escapes and expression[cursor] == "\\":
                cursor += 2
            elif expression[cursor] == quote:
                cursor += 1
                if cursor < len(expression) and expression[cursor] == quote:
                    cursor += 1
                else:
                    break
            else:
                cursor += 1
        parts.append(expression[quoted_start:cursor])
        outside_start = cursor
    parts.append(cast.sub("", expression[outside_start:]))
    return "".join(parts)


def _preflight(connection: Connection) -> None:
    inspector = inspect(connection)
    old_tables = {name: table for name, table in Base.metadata.tables.items() if name in V12_CHECKS}
    if set(inspector.get_table_names()) != set(old_tables):
        raise ValueError(ERROR)
    # Serialize against writes/DDL as well as other cooperating migrations. Static
    # model identifiers only; no caller-controlled table/column interpolation.
    quote = connection.dialect.identifier_preparer.quote
    tables = ", ".join(quote(name) for name in sorted(old_tables))
    connection.execute(text(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE"))
    inspector = inspect(connection)
    if list(connection.execute(select(SchemaVersion.version)).scalars()) != [12]:
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
            if isinstance(constraint, UniqueConstraint)
            and constraint.name
            not in {
                "environments_fleet_scope_unique",
                "api_keys_execution_scope_unique", "runs_execution_scope_unique",
                "releases_execution_candidate_unique",
            }
        }
        actual_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
        if actual_unique != expected_unique:
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
        if actual_fks != expected_fks:
            raise ValueError(ERROR)
        checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
        actual_checks = inspector.get_check_constraints(name)
        if sorted(_normalize_check(c["sqltext"]) for c in actual_checks) != sorted(
            _normalize_check(c) for c in V12_CHECKS[name]
        ) or not {c.name for c in checks if c.name}.issubset({c["name"] for c in actual_checks}):
            raise ValueError(ERROR)
        expected_indexes = {
            (i.name, tuple(c.name for c in i.columns), bool(i.unique)) for i in table.indexes
        }
        indexes = [i for i in inspector.get_indexes(name) if not i.get("duplicates_constraint")]
        # Every frozen v12 index is an unconditional plain-column default btree.
        # Reflection omits default options (except an empty INCLUDE list). Refuse
        # predicates, methods, opclasses, INCLUDE, storage or sort changes rather
        # than accepting or rebuilding a customer's altered index.
        if any(
            i.get("expressions")
            or i.get("column_sorting")
            or any(
                value not in (None, False, {}, [])
                for value in i.get("dialect_options", {}).values()
            )
            for i in indexes
        ):
            raise ValueError(ERROR)
        actual_indexes = {(i["name"], tuple(i["column_names"]), bool(i["unique"])) for i in indexes}
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


def migrate_fleet_v13(connection: Connection) -> None:
    """Refuse partial/corrupt v12 before DDL, preserving all existing rows.

    The caller owns rollback, the advisory lock, and updating the version ledger
    last. This helper never commits or creates any runtime/connection records.
    """
    if connection.dialect.name != "postgresql" or not connection.in_transaction():
        raise ValueError(ERROR)
    _preflight(connection)
    connection.execute(
        text(
            "ALTER TABLE environments ADD CONSTRAINT environments_fleet_scope_unique UNIQUE (workspace_id, project_id, id)"
        )
    )
    for table in Base.metadata.sorted_tables:
        if table.name in FLEET_TABLES:
            table.create(connection)
    for table in Base.metadata.sorted_tables:
        if table.name in FLEET_TABLES:
            for constraint in table.foreign_key_constraints:
                if constraint.use_alter:
                    connection.execute(AddConstraint(constraint, isolate_from_table=False))


# Frozen PostgreSQL expressions from the committed schema-v12 fixture.
V12_CHECKS = {
    "alert_deliveries": [
        "attempts >= 0 AND attempts <= 5",
        "event::text = ANY (ARRAY['incident.opened'::character varying, "
        "'incident.resolved'::character varying, 'incident.reopened'::character "
        "varying]::text[])",
        "status::text = ANY (ARRAY['queued'::character varying, 'sending'::character "
        "varying, 'delivered'::character varying, 'failed'::character varying, "
        "'cancelled'::character varying]::text[])",
    ],
    "api_keys": [],
    "approvals": [
        "decided_by IS NULL OR decided_by::text <> requested_by::text",
        "status::text = 'pending'::text AND decided_by IS NULL AND decided_at IS NULL OR "
        "(status::text = ANY (ARRAY['approved'::character varying, 'rejected'::character "
        "varying]::text[])) AND decided_by IS NOT NULL AND decided_at IS NOT NULL",
    ],
    "assessments": [
        "status::text = ANY (ARRAY['passed'::character varying, 'failed'::character "
        "varying, 'unknown'::character varying]::text[])"
    ],
    "audit_events": [],
    "environments": [
        "kind::text = ANY (ARRAY['local'::character varying, 'sandbox'::character "
        "varying, 'production'::character varying]::text[])"
    ],
    "evaluation_jobs": [],
    "gateway_usage": [
        "actual_usd IS NULL OR actual_usd >= 0::numeric",
        "completion_tokens IS NULL OR completion_tokens >= 0",
        "latency_ms IS NULL OR latency_ms >= 0",
        "prompt_tokens IS NULL OR prompt_tokens >= 0",
        "reserved_usd >= 0::numeric AND charged_usd >= 0::numeric",
        "status::text = ANY (ARRAY['reserved'::character varying, 'settled'::character "
        "varying, 'uncertain'::character varying]::text[])",
    ],
    "identity_challenges": [
        "kind::text = ANY (ARRAY['email_verification'::character varying, "
        "'password_reset'::character varying]::text[])"
    ],
    "incident_events": [
        "action::text = ANY (ARRAY['created'::character varying, "
        "'resolved'::character varying, 'reopened'::character varying]::text[])"
    ],
    "incidents": [
        "category::text = ANY (ARRAY['manual'::character varying, "
        "'assessment_failed'::character varying, 'assessment_unavailable'::character "
        "varying]::text[])",
        "severity::text = ANY (ARRAY['low'::character varying, 'medium'::character varying, "
        "'high'::character varying, 'critical'::character varying]::text[])",
        "source::text = ANY (ARRAY['manual'::character varying, 'monitor'::character "
        "varying, 'trace_assessment'::character varying, 'voice_assessment'::character "
        "varying]::text[])",
        "status::text = 'open'::text AND resolved_at IS NULL OR status::text = "
        "'resolved'::text AND resolved_at IS NOT NULL",
        "status::text = ANY (ARRAY['open'::character varying, 'resolved'::character "
        "varying]::text[])",
    ],
    "integrations": [],
    "invitations": [
        "role::text = ANY (ARRAY['admin'::character varying, 'editor'::character varying, "
        "'viewer'::character varying]::text[])"
    ],
    "mail_outbox": [
        "attempts >= 0 AND attempts <= 5",
        "kind::text = 'security_notice'::text AND identity_challenge_id IS NULL AND "
        "invitation_id IS NULL OR kind::text <> 'security_notice'::text AND "
        "(identity_challenge_id IS NOT NULL AND invitation_id IS NULL OR "
        "identity_challenge_id IS NULL AND invitation_id IS NOT NULL)",
        "kind::text = ANY (ARRAY['email_verification'::character varying, "
        "'password_reset'::character varying, 'invitation'::character varying, "
        "'security_notice'::character varying]::text[])",
        "status::text = ANY (ARRAY['queued'::character varying, 'sending'::character "
        "varying, 'sent'::character varying, 'failed'::character varying, "
        "'cancelled'::character varying]::text[])",
    ],
    "memberships": [
        "role::text = ANY (ARRAY['owner'::character varying, 'admin'::character varying, "
        "'editor'::character varying, 'viewer'::character varying]::text[])"
    ],
    "mfa_enrollments": [
        "expires_at > created_at",
        "factor_version >= 1",
        "length(secret_ciphertext) > 0",
    ],
    "mfa_factors": [
        "last_totp_counter IS NULL OR last_totp_counter >= 0",
        "length(secret_ciphertext) > 0",
        "version >= 1",
    ],
    "mfa_login_challenges": [
        "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
        "attempts >= 0 AND attempts <= 8",
        "consumed_at IS NULL OR consumed_at >= created_at",
        "expires_at > created_at",
        "factor_version >= 1",
        "length(token_hash::text) = 64",
        "revoked_at IS NULL OR revoked_at >= created_at",
    ],
    "mfa_recovery_codes": [
        "consumed_at IS NULL OR consumed_at >= created_at",
        "factor_version >= 1",
        "length(code_hash::text) = 64",
    ],
    "monitors": ["sample_basis_points >= 0 AND sample_basis_points <= 10000"],
    "offline_enrollments": [
        "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
        "consumed_at IS NULL OR consumed_at >= created_at",
        "revoked_at IS NULL OR revoked_at >= created_at",
    ],
    "offline_identity_attestations": [
        "expires_at IS NULL OR expires_at > attested_at",
        "revoked_at IS NULL OR revoked_at >= attested_at",
        "source::text = ANY (ARRAY['in_person'::character varying, "
        "'government_credential'::character varying, "
        "'enterprise_record'::character varying]::text[])",
    ],
    "package_publication_requests": [
        "length(request_hash::text) = 64",
        "length(request_key::text) >= 1 AND length(request_key::text) <= 128",
    ],
    "policies": [
        "minimum_pass_rate >= 0::numeric AND minimum_pass_rate <= 1::numeric AND "
        "maximum_regression >= 0::numeric AND maximum_regression <= 1::numeric"
    ],
    "projects": [],
    "receipts": [
        "outcome::text = ANY (ARRAY['success'::character varying, 'failure'::character "
        "varying, 'unknown'::character varying]::text[])"
    ],
    "release_decisions": [
        "verdict::text = ANY (ARRAY['pass'::character varying, 'block'::character "
        "varying, 'approval_required'::character varying]::text[])"
    ],
    "releases": [],
    "run_results": ["latency_ms >= 0 AND cost_usd >= 0::numeric"],
    "runners": [],
    "runs": [
        "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, "
        "'completed'::character varying, 'failed'::character varying, 'cancelled'::character "
        "varying]::text[])"
    ],
    "scenarios": [
        "modality::text = ANY (ARRAY['text'::character varying, 'voice'::character varying, "
        "'tool'::character varying, 'multimodal'::character varying]::text[])"
    ],
    "schema_version": [],
    "sessions": [
        "mfa_factor_version IS NULL OR mfa_factor_version >= 1",
        "mfa_verified_at IS NULL AND mfa_factor_id IS NULL AND mfa_factor_version IS NULL OR "
        "mfa_verified_at IS NOT NULL AND mfa_factor_id IS NOT NULL AND mfa_factor_version IS "
        "NOT NULL",
        "mfa_verified_at IS NULL OR mfa_verified_at <= created_at",
    ],
    "traces": [
        "(status::text = ANY (ARRAY['ok'::character varying, 'error'::character "
        "varying]::text[])) AND latency_ms >= 0 AND cost_usd >= 0::numeric",
        "cost_measurement::text = 'reported'::text AND cost_usd IS NOT NULL OR "
        "cost_measurement::text = 'unavailable'::text AND cost_usd IS NULL OR "
        "cost_measurement::text = 'unknown'::text",
    ],
    "users": ["email::text = lower(email::text)"],
    "voice_segments": ["sequence >= 0 AND revision >= 0 AND start_ms >= 0 AND end_ms >= start_ms"],
    "voice_sessions": [
        "status::text = ANY (ARRAY['open'::character varying, 'closed'::character varying]::text[])"
    ],
    "workflow_package_versions": [
        "content_bytes > 0 AND content_bytes = length(canonical_content)",
        "length(sha256::text) = 64",
        "publisher_kind::text = ANY (ARRAY['user'::character varying, "
        "'api_key'::character varying]::text[])",
    ],
    "workspaces": [
        "kind::text = 'organization'::text OR require_mfa = false",
        "kind::text = ANY (ARRAY['personal'::character varying, 'organization'::character "
        "varying]::text[])",
        "monthly_budget_usd IS NULL OR monthly_budget_usd >= 0::numeric",
        "plan::text = ANY (ARRAY['developer'::character varying, 'team'::character "
        "varying, 'enterprise'::character varying]::text[])",
        "status::text = ANY (ARRAY['active'::character varying, 'suspended'::character "
        "varying]::text[])",
    ],
}
