"""Exercise backup/restore using only newly created, network-disabled local containers."""

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DATABASE = "lotuspond_restore_drill"
NORMALIZED_SCHEMA_DATABASE = "lotuspond_restore_schema_normalized"

# These identifiers, account and credential placeholder are synthetic test fixtures.
# The placeholder is deliberately not a usable password hash.
FIXTURE = """
INSERT INTO users (id,email,name,password_hash,is_platform_admin,email_verified,created_at)
VALUES ('00000000-0000-0000-0000-000000000001','restore@example.invalid','Synthetic restore account',
        'not-a-login-credential',false,false,'2026-09-21T00:00:00Z');
INSERT INTO workspaces (id,name,slug,kind,owner_id,status,plan,monthly_budget_usd,created_at)
VALUES ('00000000-0000-0000-0000-000000000002','Synthetic restore workspace','restore-fixture','personal',
        '00000000-0000-0000-0000-000000000001','active','developer',123.000001,'2026-09-21T00:00:00Z');
INSERT INTO memberships (workspace_id,user_id,role)
VALUES ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','owner');
INSERT INTO projects (id,workspace_id,name,description,created_at)
VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000002',
        'Synthetic restore project','Contains no customer data','2026-09-21T00:00:00Z');
INSERT INTO policies (id,workspace_id,name,minimum_pass_rate,maximum_regression,require_approval,
                      blocked_tools,approval_tools,created_at)
VALUES ('00000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000002',
        'Synthetic precision check',0.9999,0.0001,true,'["remove_all"]','[]','2026-09-21T00:00:00Z');
INSERT INTO gateway_usage (id,workspace_id,model,status,reserved_usd,charged_usd,actual_usd,
                           prompt_tokens,completion_tokens,created_at)
VALUES ('00000000-0000-0000-0000-000000000005','00000000-0000-0000-0000-000000000002',
        'synthetic-model','settled',1.000001,1234567890123456.123456,1234567890123456.123456,
        3000000000,17,'2026-09-21T00:00:00Z');
INSERT INTO audit_events (id,workspace_id,actor_id,action,resource_id,details,created_at)
VALUES ('00000000-0000-0000-0000-000000000006','00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000001','synthetic.restore_check',
        '00000000-0000-0000-0000-000000000003','{"synthetic":true,"unicode":"lotus ∴"}',
        '2026-09-21T00:00:00Z');
INSERT INTO workspaces (id,name,slug,kind,owner_id,status,plan,created_at)
VALUES ('00000000-0000-0000-0000-000000000007','Synthetic organization','restore-organization',
        'organization','00000000-0000-0000-0000-000000000001','active','developer','2026-09-21T00:00:00Z');
INSERT INTO identity_challenges (id,user_id,kind,token_hash,expires_at,revoked_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000008','00000000-0000-0000-0000-000000000001',
        'password_reset',repeat('1',64),'2026-09-21T00:30:00Z','2026-09-21T00:10:00Z','2026-09-21T00:00:00Z');
INSERT INTO invitations (id,workspace_id,email,role,token_hash,created_by,expires_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000009','00000000-0000-0000-0000-000000000007',
        'invited@example.invalid','viewer',repeat('2',64),'00000000-0000-0000-0000-000000000001',
        '2026-09-22T00:00:00Z','2026-09-21T00:00:00Z');
INSERT INTO mail_outbox (id,workspace_id,invitation_id,kind,recipient_hash,body_ciphertext,
                         status,attempts,available_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000010','00000000-0000-0000-0000-000000000007',
        '00000000-0000-0000-0000-000000000009','invitation',repeat('3',64),
        'synthetic-opaque-body-not-a-real-message','queued',0,'2026-09-21T00:00:00Z','2026-09-21T00:00:00Z');
INSERT INTO api_keys (id,workspace_id,name,token_hash,prefix,scopes,expires_at,created_by_user_id,created_at)
VALUES ('00000000-0000-0000-0000-000000000011','00000000-0000-0000-0000-000000000007',
        'Synthetic owner-bound key',repeat('4',64),'lp_synthetic','["read"]','2026-09-22T00:00:00Z',
        '00000000-0000-0000-0000-000000000001','2026-09-21T00:00:00Z');
INSERT INTO incidents (id,workspace_id,project_id,title,severity,category,source,status,created_at)
VALUES ('00000000-0000-0000-0000-000000000012','00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000003','Synthetic restore incident','medium','manual',
        'manual','open','2026-09-21T00:00:00Z');
INSERT INTO incident_events (id,workspace_id,incident_id,action,actor_id,reason,created_at)
VALUES ('00000000-0000-0000-0000-000000000013','00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000012','created','00000000-0000-0000-0000-000000000001',
        'Synthetic history fixture','2026-09-21T00:00:00Z');
INSERT INTO alert_deliveries (id,workspace_id,incident_id,incident_event_id,destination,event,
                              payload,status,attempts,available_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000014','00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000012','00000000-0000-0000-0000-000000000013',
        'synthetic-receiver','incident.opened','{"synthetic":true}',
        'queued',0,'2026-09-21T00:00:00Z','2026-09-21T00:00:00Z');
INSERT INTO offline_identity_attestations (id,user_id,source,source_reference,operator_reference,
                                           attested_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000015','00000000-0000-0000-0000-000000000001',
        'in_person','synthetic-only','restore-drill','2026-09-21T00:00:00Z','2026-09-21T00:00:00Z');
INSERT INTO offline_enrollments (id,user_id,attestation_id,token_hash,expires_at,consumed_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000016','00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000015',repeat('5',64),'2026-09-22T00:00:00Z',
        '2026-09-21T00:10:00Z','2026-09-21T00:00:00Z');
INSERT INTO traces (id,workspace_id,project_id,external_id,name,status,latency_ms,cost_usd,
                    cost_measurement,attributes,occurred_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000017','00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000003','restore-unavailable','Unavailable cost','ok',1,
        NULL,'unavailable','{}','2026-09-21T00:00:00Z','2026-09-21T00:00:00Z'),
       ('00000000-0000-0000-0000-000000000018','00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000003','restore-zero','Reported zero','ok',1,
        0,'reported','{}','2026-09-21T00:00:00Z','2026-09-21T00:00:00Z'),
       ('00000000-0000-0000-0000-000000000019','00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000003','restore-legacy','Legacy measurement','ok',1,
        0.123456,'unknown','{}','2026-09-21T00:00:00Z','2026-09-21T00:00:00Z');
INSERT INTO mfa_factors (id,user_id,secret_ciphertext,version,enabled_at,last_totp_counter,created_at)
VALUES ('00000000-0000-0000-0000-000000000020','00000000-0000-0000-0000-000000000001',
        'synthetic-opaque-secret-not-a-real-factor',1,'2026-09-21T00:00:00Z',59664000,
        '2026-09-21T00:00:00Z');
INSERT INTO mfa_enrollments (id,user_id,secret_ciphertext,factor_version,expires_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000021','00000000-0000-0000-0000-000000000001',
        'synthetic-pending-secret-not-a-real-factor',2,'2026-09-21T00:10:00Z','2026-09-21T00:00:00Z');
INSERT INTO mfa_login_challenges (id,user_id,factor_id,factor_version,token_hash,expires_at,
                                  attempts,consumed_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000022','00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000020',1,repeat('6',64),'2026-09-21T00:05:00Z',1,
        '2026-09-21T00:01:00Z','2026-09-21T00:00:00Z');
INSERT INTO mfa_recovery_codes (id,user_id,factor_id,factor_version,code_hash,consumed_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000023','00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000020',1,repeat('7',64),'2026-09-21T00:01:00Z',
        '2026-09-21T00:00:00Z');
INSERT INTO sessions (id,user_id,token_hash,expires_at,mfa_factor_id,mfa_factor_version,
                       mfa_verified_at,created_at)
VALUES ('00000000-0000-0000-0000-000000000024','00000000-0000-0000-0000-000000000001',
        repeat('8',64),'2026-09-21T01:00:00Z','00000000-0000-0000-0000-000000000020',1,
        '2026-09-21T00:01:00Z','2026-09-21T00:01:00Z');
"""


def command(*args, data=None, timeout=120, check=True):
    result = subprocess.run(
        args, input=data, capture_output=True, check=False, timeout=timeout
    )
    if check and result.returncode:
        # Do not print command bodies, dumps or connection configuration.
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result


def cleanup_command(*args):
    try:
        return command(*args, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def sql(container, query):
    return (
        command(
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            DATABASE,
            data=query.encode(),
        )
        .stdout.decode()
        .strip()
    )


def dump(container, database, *options):
    return command(
        "docker",
        "exec",
        container,
        "pg_dump",
        "-U",
        "postgres",
        "-d",
        database,
        "--no-owner",
        "--no-acl",
        *options,
    ).stdout


def normalized_schema(container, database):
    raw = dump(
        container,
        database,
        "--schema-only",
        "--quote-all-identifiers",
    ).decode()
    # PostgreSQL 17+ emits a fresh random psql safety token on every dump. It
    # has no schema meaning, so retain the commands but normalize only tokens.
    lines = []
    for line in raw.splitlines():
        if line.startswith("\\restrict "):
            line = "\\restrict LOTUSPOND_NORMALIZED_TOKEN"
        elif line.startswith("\\unrestrict "):
            line = "\\unrestrict LOTUSPOND_NORMALIZED_TOKEN"
        lines.append(line)
    return "\n".join(lines) + "\n"


def fingerprint(container, schema_database):
    tables = sql(
        container,
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;",
    ).splitlines()
    rows = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        rows[table] = sql(
            container,
            f"SELECT row_to_json(t)::text FROM {quoted} t ORDER BY row_to_json(t)::text;",
        )
    sequence_metadata = sql(
        container,
        """
        SELECT row_to_json(s)::text
        FROM pg_sequences s
        WHERE schemaname='public'
        ORDER BY sequencename;
        """,
    )
    sequences = {}
    sequence_names = sql(
        container,
        "SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename;",
    ).splitlines()
    for sequence in sequence_names:
        quoted = '"' + sequence.replace('"', '""') + '"'
        sequences[sequence] = sql(
            container,
            f"SELECT last_value::text || '|' || is_called::text FROM {quoted};",
        )
    payload = json.dumps(
        {
            "rows": rows,
            "sequence_metadata": sequence_metadata,
            "sequence_state": sequences,
            "schema": normalized_schema(container, schema_database),
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest(), len(tables)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default="postgres:18.6-alpine3.24",
        help="An already present, operator-reviewed PostgreSQL image",
    )
    args = parser.parse_args()
    if not args.image or args.image.startswith("-"):
        parser.error("Use a local image reference, not a Docker option")
    image = (
        command("docker", "image", "inspect", "--format", "{{.Id}}", args.image)
        .stdout.decode()
        .strip()
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RuntimeError("Docker returned an invalid local image id")
    run_id = uuid4().hex
    names = [f"lotuspond-restore-{run_id}-{part}" for part in ("source", "target")]
    created = []
    cleanup_errors = []
    report = None
    primary_error = None
    started = time.monotonic()
    try:
        for name in names:
            # Record the random name before creation so a timeout cannot orphan it.
            created.append(name)
            command(
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--name",
                name,
                "--label",
                f"lotuspond.restore-drill={run_id}",
                "--network=none",
                "--read-only",
                "--memory=512m",
                "--cpus=1",
                "--pids-limit=128",
                "--security-opt=no-new-privileges:true",
                "--tmpfs",
                "/var/lib/postgresql:rw,size=256m,mode=0755",
                "--tmpfs",
                "/var/run/postgresql:rw,size=8m,mode=0777",
                "--tmpfs",
                "/tmp:rw,size=8m,mode=1777",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                f"POSTGRES_DB={DATABASE}",
                image,
            )
            for _ in range(60):
                if (
                    command(
                        "docker",
                        "exec",
                        name,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "postgres",
                        "-d",
                        DATABASE,
                        check=False,
                        timeout=5,
                    ).returncode
                    == 0
                ):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Disposable PostgreSQL failed to become ready")
        source, target = names
        schema = (ROOT / "backend/schema.sql").read_text()
        sql(source, schema)
        sql(source, "BEGIN;\n" + FIXTURE + "\nCOMMIT;")
        if (
            sql(target, "SELECT count(*) FROM pg_tables WHERE schemaname='public';")
            != "0"
        ):
            raise RuntimeError("Restore target is not empty")
        backup = dump(source, DATABASE, "--format=custom")
        schema_backup = dump(
            source,
            DATABASE,
            "--format=custom",
            "--schema-only",
        )
        command(
            "docker",
            "exec",
            source,
            "createdb",
            "-U",
            "postgres",
            "-T",
            "template0",
            NORMALIZED_SCHEMA_DATABASE,
        )
        command(
            "docker",
            "exec",
            "-i",
            source,
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            NORMALIZED_SCHEMA_DATABASE,
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            data=schema_backup,
        )
        expected, count = fingerprint(source, NORMALIZED_SCHEMA_DATABASE)
        command(
            "docker",
            "exec",
            "-i",
            target,
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            DATABASE,
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            data=backup,
        )
        actual, restored_count = fingerprint(target, DATABASE)
        if actual != expected or count != restored_count:
            raise RuntimeError(
                "Restored data or schema differs from the synthetic source"
            )
        report = {
            "result": "passed",
            "image": image,
            "tables": count,
            "fingerprint_sha256": actual,
            "backup_bytes": len(backup),
            "network": "none",
            "synthetic_only": True,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
    except BaseException as error:
        primary_error = error
        raise
    finally:
        inventory = cleanup_command(
            "docker", "container", "ls", "-a", "--format", "{{.Names}}"
        )
        if inventory is None or inventory.returncode != 0:
            cleanup_errors.extend(created)
        else:
            present = set(inventory.stdout.decode().splitlines())
            for name in reversed(created):
                if name not in present:
                    continue
                inspected = cleanup_command(
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "lotuspond.restore-drill"}}',
                    name,
                )
                if (
                    inspected is None
                    or inspected.returncode
                    or inspected.stdout.decode().strip() != run_id
                ):
                    cleanup_errors.append(name)
                    continue
                removed = cleanup_command("docker", "rm", "-f", "-v", name)
                if removed is None or removed.returncode != 0:
                    cleanup_errors.append(name)
            remaining = cleanup_command(
                "docker", "container", "ls", "-a", "--format", "{{.Names}}"
            )
            if remaining is None or remaining.returncode:
                cleanup_errors.extend(created)
            else:
                cleanup_errors.extend(
                    set(created) & set(remaining.stdout.decode().splitlines())
                )
        if cleanup_errors:
            message = "Disposable container cleanup requires attention: " + ", ".join(
                sorted(set(cleanup_errors))
            )
            if primary_error is not None:
                primary_error.add_note(message)
            else:
                raise RuntimeError(message)
    print(json.dumps({**report, "cleanup": "confirmed"}, sort_keys=True))


if __name__ == "__main__":
    main()
