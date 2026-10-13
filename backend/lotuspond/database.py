from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings


def engine_for(settings: Settings):
    url = settings.database_url.get_secret_value()
    if url.startswith("sqlite"):
        if settings.env != "test":
            raise ValueError("SQLite is allowed only for isolated unit tests")
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            hide_parameters=True,
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(
        url, pool_pre_ping=True, pool_size=10, max_overflow=10, hide_parameters=True
    )


def sessions_for(engine):
    return sessionmaker(engine, expire_on_commit=False)


def session_scope(factory) -> Iterator[Session]:
    with factory() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
