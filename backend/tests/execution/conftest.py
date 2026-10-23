"""Owned PostgreSQL schemas, never a shared reset or ambient database."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def execution_test_url():
    """CI's optional unit suite supplies its independently verified service only here.

    The required PostgreSQL qualification supplies the shared platform test URL.
    Neither path permits missing configuration, conflicts, or a nonlocal database.
    """
    dedicated = os.environ.get("LOTUSPOND_EXECUTION_TEST_DATABASE_URL")
    platform = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if dedicated and platform and dedicated != platform:
        raise ValueError("Conflicting execution and platform test database URLs")
    url = dedicated or platform
    if not url:
        pytest.fail("Verified localhost PostgreSQL required for execution qualification")
    parsed = make_url(url)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.query
        or parsed.host not in {"localhost", "127.0.0.1", "::1"}
        or parsed.database != "lotuspond_platform_test"
    ):
        raise ValueError("Isolated localhost lotuspond_platform_test required")
    return url


@pytest.fixture
def execution_database():
    url = execution_test_url()
    admin = create_engine(url, hide_parameters=True)
    owned = []

    def allocate(v13=False):
        schema = f"test_execution_{uuid4().hex}"
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            assert connection.scalar(
                text("SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname=:schema)"),
                {"schema": schema},
            )
        engine = create_engine(
            url, connect_args={"options": f"-c search_path={schema}"}, hide_parameters=True
        )
        owned.append((schema, engine))
        if v13:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    (Path(__file__).parent / "fixtures/schema-v13.sql").read_text()
                )
        return engine

    yield allocate
    for schema, engine in owned:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            assert not connection.scalar(
                text("SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname=:schema)"),
                {"schema": schema},
            )
        print(f"Owned schema cleanup observed: {schema}")
    admin.dispose()
