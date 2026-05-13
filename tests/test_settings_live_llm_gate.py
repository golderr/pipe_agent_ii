"""Per-profile live-LLM gate tests.

Verify that ``Settings.live_llm_allowed_for(profile_name)`` resolves the gate
per-profile when set, otherwise falls back to the global
``agent_allow_live_llm``. Unknown profile names fall through to the global so
adding a new agent profile that hasn't been wired into the helper still gets
the global posture rather than failing closed silently.
"""

from __future__ import annotations

from tcg_pipeline.settings import Settings


def test_global_true_no_per_profile_overrides_allows_all_profiles() -> None:
    settings = Settings(agent_allow_live_llm=True)
    assert settings.live_llm_allowed_for("news_v1") is True
    assert settings.live_llm_allowed_for("permit_v1") is True
    # Unknown profile still falls through to the global.
    assert settings.live_llm_allowed_for("future_v1") is True


def test_global_false_no_per_profile_overrides_blocks_all_profiles() -> None:
    settings = Settings(agent_allow_live_llm=False)
    assert settings.live_llm_allowed_for("news_v1") is False
    assert settings.live_llm_allowed_for("permit_v1") is False
    assert settings.live_llm_allowed_for("future_v1") is False


def test_news_override_false_isolates_news_without_affecting_permits() -> None:
    """The news-incident kill scenario the gate exists for."""
    settings = Settings(
        agent_allow_live_llm=True,
        agent_allow_live_llm_news=False,
        agent_allow_live_llm_permits=None,
    )
    assert settings.live_llm_allowed_for("news_v1") is False
    assert settings.live_llm_allowed_for("permit_v1") is True


def test_permit_override_false_isolates_permits_without_affecting_news() -> None:
    """The permit-incident kill scenario, symmetric to the news case."""
    settings = Settings(
        agent_allow_live_llm=True,
        agent_allow_live_llm_news=None,
        agent_allow_live_llm_permits=False,
    )
    assert settings.live_llm_allowed_for("news_v1") is True
    assert settings.live_llm_allowed_for("permit_v1") is False


def test_per_profile_true_overrides_global_false() -> None:
    """Per-profile overrides go both directions: a per-profile True with global
    False allows that one profile's live LLM calls while keeping others dark."""
    settings = Settings(
        agent_allow_live_llm=False,
        agent_allow_live_llm_news=True,
    )
    assert settings.live_llm_allowed_for("news_v1") is True
    assert settings.live_llm_allowed_for("permit_v1") is False


def test_per_profile_explicit_false_under_global_false_is_idempotent() -> None:
    """Setting a per-profile flag to False when the global is also False
    keeps the profile blocked (no surprise interaction)."""
    settings = Settings(
        agent_allow_live_llm=False,
        agent_allow_live_llm_news=False,
        agent_allow_live_llm_permits=False,
    )
    assert settings.live_llm_allowed_for("news_v1") is False
    assert settings.live_llm_allowed_for("permit_v1") is False


def test_unknown_profile_name_always_falls_through_to_global() -> None:
    settings = Settings(
        agent_allow_live_llm=True,
        agent_allow_live_llm_news=False,
        agent_allow_live_llm_permits=False,
    )
    # Unknown profile name still uses the global, not False from either
    # per-profile setting.
    assert settings.live_llm_allowed_for("costar_v1") is True
    assert settings.live_llm_allowed_for("leasing_v1") is True
