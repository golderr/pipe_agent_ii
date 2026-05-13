from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from tcg_pipeline.db import connection as connection_module


def test_get_engine_passes_tcp_keepalive_connect_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long agent integrate calls span many seconds with no DB activity. Supabase
    pooler silently kills the SSL session and the next query surfaces as
    ``ssl/tls alert bad record mac``. TCP keepalives prevent the pooler/firewall
    from declaring the connection dead during idle gaps."""
    captured: dict[str, object] = {}

    def fake_create_engine(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        # Build a real (in-memory) engine so the function returns something usable.
        return create_engine("sqlite:///:memory:")

    monkeypatch.setattr(connection_module, "create_engine", fake_create_engine)
    # Bypass the engine-level event listener that issues PostgreSQL-only SET
    # statements; SQLite would reject those.
    monkeypatch.setattr(
        connection_module,
        "_configure_postgres_session_timeouts",
        lambda _engine: None,
    )
    connection_module.get_engine.cache_clear()
    try:
        connection_module.get_engine()
    finally:
        connection_module.get_engine.cache_clear()

    connect_args = captured["kwargs"].get("connect_args")
    assert isinstance(connect_args, dict), "connect_args must be a dict"
    assert connect_args.get("keepalives") == 1
    assert connect_args.get("keepalives_idle") == 30
    assert connect_args.get("keepalives_interval") == 10
    assert connect_args.get("keepalives_count") == 5
