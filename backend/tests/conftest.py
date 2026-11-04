import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from lotuspond.app import create_app
from lotuspond.config import Settings
from lotuspond.database import engine_for, sessions_for
from lotuspond.models import (
    Base,
    EvaluationJob,
    SchemaVersion,
    VoiceSegment,
    VoiceSession,
    new_id,
    now,
)
from lotuspond.schema_version import SCHEMA_VERSION


@pytest.fixture
def settings():
    return Settings(
        env="test",
        database_url="sqlite://",
        origin="http://testserver",
        redis_url=None,
        registration_enabled=True,
    )


@pytest.fixture
def engine(settings):
    database = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    administration = None
    schema = None
    if database:
        url = make_url(database)
        if (
            url.host not in {"127.0.0.1", "localhost", "::1"}
            or url.database != "lotuspond_platform_test"
        ):
            raise ValueError(
                "Integration tests require the isolated localhost lotuspond_platform_test database"
            )
        schema = f"test_{uuid4().hex}"
        administration = create_engine(database, hide_parameters=True)
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            database, connect_args={"options": f"-c search_path={schema}"}, hide_parameters=True
        )
    else:
        engine = engine_for(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(SchemaVersion.__table__.insert().values(version=SCHEMA_VERSION))
    yield engine
    engine.dispose()
    if administration is not None:
        with administration.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()


@pytest.fixture
def db_factory(engine):
    return sessions_for(engine)


@pytest.fixture
def fleet_dependencies():
    return None


@pytest.fixture
def retired_voice_job(db_factory):
    """Store a closed voice session with a queued assessment, as an older installation holds it.

    Voice ingestion is retired, so no route can create these rows any more; the worker and
    incidents must still treat the stored ones correctly.
    """

    def queue(workspace_id: str, project_id: str, criteria: str) -> tuple[str, str]:
        with db_factory() as db:
            session = VoiceSession(
                id=new_id(),
                workspace_id=workspace_id,
                project_id=project_id,
                name="Support call",
                provider="test",
                status="closed",
                closed_at=now(),
            )
            db.add(session)
            db.flush()
            db.add(
                VoiceSegment(
                    id=new_id(),
                    workspace_id=workspace_id,
                    session_id=session.id,
                    sequence=0,
                    revision=0,
                    speaker="agent",
                    text="Hello",
                    start_ms=0,
                    end_ms=100,
                    is_final=True,
                )
            )
            job = EvaluationJob(
                id=new_id(),
                workspace_id=workspace_id,
                project_id=project_id,
                voice_session_id=session.id,
                criteria=criteria,
            )
            db.add(job)
            db.commit()
            return session.id, job.id

    return queue


@pytest.fixture
def app(settings, engine, fleet_dependencies):
    return create_app(settings, engine=engine, fleet_dependencies=fleet_dependencies)


@pytest.fixture
def client(app):
    with TestClient(app, headers={"origin": "http://testserver"}) as client:
        yield client


@pytest.fixture
def account(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "name": "Owner",
            "password": "a-valid-test-password",
            "account_type": "organization",
            "workspace": "Test organization",
        },
    )
    assert response.status_code == 201, response.text
    workspace = response.json()["workspaceId"]
    client.headers["x-lotuspond-workspace"] = workspace
    return workspace
