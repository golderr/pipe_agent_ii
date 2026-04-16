from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_engine
from tcg_pipeline.settings import get_settings


@pytest.fixture()
def postgres_session() -> Session:
    if not get_settings().has_database_url:
        pytest.skip("DATABASE_URL is required for persistence tests.")

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
