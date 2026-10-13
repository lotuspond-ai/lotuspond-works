from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import Settings
from .database import engine_for
from .models import SchemaVersion
from .offline_identity import (
    ProvisioningRequest,
    ReissueRequest,
    RevocationRequest,
    provision_identity,
    reissue_enrollment,
    revoke_identity,
)
from .schema_version import SCHEMA_VERSION

CONFIRMATION = "PROVISION NEW OFFLINE IDENTITY"
REISSUE_CONFIRMATION = "REISSUE OFFLINE ENROLLMENT"
REVOCATION_CONFIRMATION = "REVOKE OFFLINE IDENTITY"


def _write_token_file(path: Path, enrollment_url: str) -> None:
    parent = path.parent.resolve(strict=True)
    if path.parent.is_symlink() or not parent.is_dir():
        raise ValueError("Token-file parent must be a real existing directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(enrollment_url + "\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision a new offline Lotuspond identity")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision")
    provision.add_argument("--email", required=True)
    provision.add_argument("--name", required=True)
    provision.add_argument(
        "--attestation-source",
        required=True,
        choices=["in_person", "government_credential", "enterprise_record"],
    )
    provision.add_argument("--source-reference", required=True)
    provision.add_argument("--operator-reference", required=True)
    provision.add_argument("--expires-hours", type=int, default=24)
    provision.add_argument("--organization-name")
    provision.add_argument("--project", action="append", default=[])
    provision.add_argument("--platform-admin", action="store_true")
    provision.add_argument("--token-file", required=True, type=Path)
    provision.add_argument("--confirm", required=True)
    reissue = commands.add_parser("reissue-enrollment")
    reissue.add_argument("--user-id", required=True)
    reissue.add_argument("--email", required=True)
    reissue.add_argument("--operator-reference", required=True)
    reissue.add_argument("--expires-hours", type=int, default=24)
    reissue.add_argument("--token-file", required=True, type=Path)
    reissue.add_argument("--confirm", required=True)
    revoke = commands.add_parser("revoke-identity")
    revoke.add_argument("--user-id", required=True)
    revoke.add_argument("--email", required=True)
    revoke.add_argument("--operator-reference", required=True)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--confirm", required=True)
    args = parser.parse_args()
    settings = Settings()
    if settings.network_mode != "airgap" or settings.mail_enabled:
        raise ValueError("Offline provisioning requires airgap mode with mail disabled")
    expected_confirmation = {
        "provision": CONFIRMATION,
        "reissue-enrollment": REISSUE_CONFIRMATION,
        "revoke-identity": REVOCATION_CONFIRMATION,
    }[args.command]
    if args.confirm != expected_confirmation:
        raise ValueError(f"Confirmation must exactly match: {expected_confirmation}")

    engine = engine_for(settings)
    wrote_token = False
    try:
        with Session(engine) as db, db.begin():
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(736184)"))
            versions = list(db.scalars(select(SchemaVersion.version)))
            if versions != [SCHEMA_VERSION]:
                raise ValueError(
                    "Offline identity requires the exact current database schema version"
                )
            if args.command == "provision":
                result = provision_identity(
                    db,
                    ProvisioningRequest(
                        email=args.email,
                        name=args.name,
                        attestation_source=args.attestation_source,
                        source_reference=args.source_reference,
                        operator_reference=args.operator_reference,
                        expires_hours=args.expires_hours,
                        organization_name=args.organization_name,
                        project_names=tuple(args.project),
                        platform_admin=args.platform_admin,
                    ),
                )
            elif args.command == "reissue-enrollment":
                result = reissue_enrollment(
                    db,
                    ReissueRequest(
                        user_id=args.user_id,
                        email=args.email,
                        operator_reference=args.operator_reference,
                        expires_hours=args.expires_hours,
                    ),
                )
            else:
                summary = revoke_identity(
                    db,
                    RevocationRequest(
                        user_id=args.user_id,
                        email=args.email,
                        operator_reference=args.operator_reference,
                        reason=args.reason,
                    ),
                )
                result = None
            if result:
                enrollment_url = f"{settings.origin}/#enroll?token={result.token}"
                _write_token_file(args.token_file, enrollment_url)
                wrote_token = True
        if result:
            print(
                f"Prepared pending user {result.user_id}. Enrollment URL written to "
                f"{args.token_file}. No password or token was printed."
            )
        else:
            print(
                f"Revoked offline identity for {summary['user_id']}. Sessions and attributed "
                "API keys were revoked; account data was retained."
            )
    except BaseException:
        if wrote_token and hasattr(args, "token_file"):
            args.token_file.unlink(missing_ok=True)
        raise
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, SQLAlchemyError, ValueError):
        print(
            "Offline identity operation failed. No credential or token was printed; "
            "verify the schema version, air-gap configuration, and operator inputs.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
