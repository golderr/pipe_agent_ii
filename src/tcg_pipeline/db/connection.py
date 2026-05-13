from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.settings import get_settings


def redact_database_url(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    prefix, suffix = database_url.split("@", 1)
    if ":" not in prefix:
        return database_url
    scheme_and_user, _password = prefix.rsplit(":", 1)
    return f"{scheme_and_user}:***@{suffix}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(
        settings.require_database_url(),
        echo=settings.sql_echo,
        future=True,
        pool_pre_ping=True,
        # Supabase pooler closes long-idle SSL sessions silently, which surfaces
        # as `consuming input failed: SSL error: ssl/tls alert bad record mac`
        # mid-query on the next operation. TCP keepalives keep stateful
        # intermediaries from declaring the connection dead while the agent
        # loop is waiting on an Anthropic response. libpq parameter names per
        # https://www.postgresql.org/docs/current/libpq-connect.html.
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )
    _configure_postgres_session_timeouts(engine)
    return engine


def _configure_postgres_session_timeouts(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_postgres_timeouts(dbapi_connection, _connection_record) -> None:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '5min'")
            cursor.execute("SET idle_in_transaction_session_timeout = '15min'")


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, class_=Session)
