import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.fleet.command_fixtures import command_env as command_env
from tests.fleet.identity_fixtures import authority_store as authority_store
from tests.fleet.identity_fixtures import identity_env as identity_env
from tests.fleet.test_management import catalog as catalog
from tests.fleet.test_management import fleet as fleet
from tests.fleet.test_management import fleet_dependencies as fleet_dependencies


@pytest.fixture
def schema_database():
    url = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Verified localhost PostgreSQL required")
    parsed = make_url(url)
    if (
        parsed.host not in {"localhost", "127.0.0.1", "::1"}
        or parsed.database != "lotuspond_platform_test"
    ):
        raise ValueError("Isolated localhost lotuspond_platform_test required")
    admin = create_engine(url, hide_parameters=True)
    owned = []

    def allocate(v12=False):
        schema = f"test_{uuid4().hex}"
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            url, connect_args={"options": f"-c search_path={schema}"}, hide_parameters=True
        )
        owned.append((schema, engine))
        if v12:
            sql = (Path(__file__).parent / "fixtures/schema-v12.sql").read_text()
            with engine.connect() as connection:
                connection.exec_driver_sql(sql)
        return engine

    yield allocate
    for schema, engine in owned:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()
