from __future__ import annotations

import dataclasses
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.agents.profiles import (
    NEWS_AGENT_PROFILE,
    AgentTrigger,
    SourceProfile,
    normalize_agent_triggers,
    validate_triggers_for_profile,
)
from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import AgentRun, AgentRunOutcome, AgentRunReviewItem
from tcg_pipeline.news.costs import (
    record_llm_cost,
    release_llm_cost_reservation,
    reserve_llm_cost,
)
from tcg_pipeline.news.llm import LLMUsage, calculate_llm_cost_usd, pricing_for_model
from tcg_pipeline.settings import Settings, get_settings


@dataclass(frozen=True, slots=True)
class IntakeRecord:
    source_type: str
    intake_record_id: str
    extraction_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    source_run_id: uuid.UUID | None = None
    scrape_job_id: uuid.UUID | None = None
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    intake: IntakeRecord
    matcher_results: tuple[dict[str, Any], ...]
    trigger_reasons: tuple[str, ...]
    profile: SourceProfile


@dataclass(frozen=True, slots=True)
class AgentClientResult:
    outcome: str
    usage: LLMUsage
    latency_ms: int
    reasoning_trace: str | None = None
    evidence_consulted: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    tool_calls_summary: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    agent_revised_verdict: dict[str, Any] | None = None
    error_text: str | None = None


class AgentClient(Protocol):
    provider: str
    model: str
    prompt_version: str

    def run(self, request: AgentRunRequest) -> AgentClientResult:
        ...


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    agent_run_id: uuid.UUID
    outcome: str
    error_text: str | None
    cost_usd: Decimal
    review_item_ids: tuple[uuid.UUID, ...] = ()


def run_agent_for_intake(
    intake: IntakeRecord,
    *,
    matcher_results: list[dict[str, Any]],
    trigger_reasons: list[AgentTrigger | str],
    profile: SourceProfile = NEWS_AGENT_PROFILE,
    client: AgentClient | None = None,
    produced_review_item_ids: list[uuid.UUID] | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    now: datetime | None = None,
) -> AgentRunResult:
    resolved_settings = settings or get_settings()
    current = _as_aware(now or datetime.now(UTC))
    normalized_triggers = normalize_agent_triggers(trigger_reasons)
    validate_triggers_for_profile(profile=profile, trigger_reasons=normalized_triggers)
    _validate_intake_profile(intake=intake, profile=profile)
    resolved_session_factory = session_factory or get_session_factory()
    review_item_ids = tuple(produced_review_item_ids or ())
    if not _agent_enabled(settings=resolved_settings, profile=profile):
        with resolved_session_factory() as session:
            agent_run = _persist_agent_run(
                session,
                intake=intake,
                profile=profile,
                trigger_reasons=normalized_triggers,
                provider=profile.default_provider,
                model=profile.default_model,
                prompt_version=profile.prompt_version,
                outcome=AgentRunOutcome.KILLED_BY_SWITCH.value,
                error_text=f"{profile.kill_switch_setting}=false",
                started_at=current,
                completed_at=current,
                matcher_results=matcher_results,
                review_item_ids=review_item_ids,
            )
            session.commit()
            return AgentRunResult(
                agent_run_id=agent_run.id,
                outcome=AgentRunOutcome.KILLED_BY_SWITCH.value,
                error_text=agent_run.error_text,
                cost_usd=Decimal("0"),
                review_item_ids=review_item_ids,
            )

    if client is None:
        raise RuntimeError("An AgentClient is required until the AGENT.2 LLM client lands.")

    pricing_for_model(client.model)
    reservation = _reserve_agent_cost(
        session_factory=resolved_session_factory,
        profile=profile,
        provider=client.provider,
        model=client.model,
        now=current,
    )
    if reservation is None:
        with resolved_session_factory() as session:
            agent_run = _persist_agent_run(
                session,
                intake=intake,
                profile=profile,
                trigger_reasons=normalized_triggers,
                provider=client.provider,
                model=client.model,
                prompt_version=client.prompt_version,
                outcome=AgentRunOutcome.FAILED_BUDGET.value,
                error_text="Daily cost cap rejected the agent run reservation.",
                started_at=current,
                completed_at=current,
                matcher_results=matcher_results,
                review_item_ids=review_item_ids,
            )
            session.commit()
            return AgentRunResult(
                agent_run_id=agent_run.id,
                outcome=AgentRunOutcome.FAILED_BUDGET.value,
                error_text=agent_run.error_text,
                cost_usd=Decimal("0"),
                review_item_ids=review_item_ids,
            )

    request = AgentRunRequest(
        intake=intake,
        matcher_results=tuple(_json_safe(result) for result in matcher_results),
        trigger_reasons=normalized_triggers,
        profile=profile,
    )
    started_counter = time.perf_counter()
    try:
        client_result = client.run(request)
    except Exception as exc:
        completed = datetime.now(UTC)
        wallclock_seconds = max(0, int(time.perf_counter() - started_counter))
        with resolved_session_factory() as session:
            release_llm_cost_reservation(
                session,
                reserved_cost_usd=profile.max_cost_usd,
                bucket=profile.cost_cap_bucket,
                now=current,
            )
            agent_run = _persist_agent_run(
                session,
                intake=intake,
                profile=profile,
                trigger_reasons=normalized_triggers,
                provider=client.provider,
                model=client.model,
                prompt_version=client.prompt_version,
                outcome=AgentRunOutcome.FAILED_ERROR.value,
                error_text=str(exc),
                started_at=current,
                completed_at=completed,
                matcher_results=matcher_results,
                latency_ms=wallclock_seconds * 1000,
                wallclock_seconds=wallclock_seconds,
                review_item_ids=review_item_ids,
            )
            session.commit()
            return AgentRunResult(
                agent_run_id=agent_run.id,
                outcome=AgentRunOutcome.FAILED_ERROR.value,
                error_text=agent_run.error_text,
                cost_usd=Decimal("0"),
                review_item_ids=review_item_ids,
            )

    completed = datetime.now(UTC)
    wallclock_seconds = max(0, int(time.perf_counter() - started_counter))
    cost_usd = calculate_llm_cost_usd(
        client.model,
        input_tokens_uncached=client_result.usage.input_tokens_uncached,
        input_tokens_cache_creation=client_result.usage.input_tokens_cache_creation,
        input_tokens_cached=client_result.usage.input_tokens_cached,
        output_tokens=client_result.usage.output_tokens,
    )
    with resolved_session_factory() as session:
        record_llm_cost(
            session,
            pass_name=profile.capability_key,
            model=client.model,
            provider=client.provider,
            input_tokens_uncached=client_result.usage.input_tokens_uncached,
            input_tokens_cache_creation=client_result.usage.input_tokens_cache_creation,
            input_tokens_cached=client_result.usage.input_tokens_cached,
            output_tokens=client_result.usage.output_tokens,
            cost_usd=cost_usd,
            reserved_cost_usd=profile.max_cost_usd,
            bucket=profile.cost_cap_bucket,
            now=current,
        )
        agent_run = _persist_agent_run(
            session,
            intake=intake,
            profile=profile,
            trigger_reasons=normalized_triggers,
            provider=client.provider,
            model=client.model,
            prompt_version=client.prompt_version,
            outcome=client_result.outcome,
            error_text=client_result.error_text,
            started_at=current,
            completed_at=completed,
            matcher_results=matcher_results,
            usage=client_result.usage,
            cost_usd=cost_usd,
            latency_ms=client_result.latency_ms,
            reasoning_trace=client_result.reasoning_trace,
            evidence_consulted=client_result.evidence_consulted,
            tool_calls_summary=client_result.tool_calls_summary,
            agent_revised_verdict=client_result.agent_revised_verdict,
            wallclock_seconds=wallclock_seconds,
            review_item_ids=review_item_ids,
        )
        session.commit()
        return AgentRunResult(
            agent_run_id=agent_run.id,
            outcome=client_result.outcome,
            error_text=client_result.error_text,
            cost_usd=cost_usd,
            review_item_ids=review_item_ids,
        )


def _reserve_agent_cost(
    *,
    session_factory: sessionmaker[Session],
    profile: SourceProfile,
    provider: str,
    model: str,
    now: datetime,
) -> object | None:
    with session_factory() as session:
        reservation = reserve_llm_cost(
            session,
            pass_name=profile.capability_key,
            model=model,
            provider=provider,
            estimated_cost_usd=profile.max_cost_usd,
            bucket=profile.cost_cap_bucket,
            now=now,
        )
        session.commit()
        return reservation


def _persist_agent_run(
    session: Session,
    *,
    intake: IntakeRecord,
    profile: SourceProfile,
    trigger_reasons: tuple[str, ...],
    provider: str,
    model: str,
    prompt_version: str,
    outcome: str,
    error_text: str | None,
    started_at: datetime,
    completed_at: datetime,
    matcher_results: list[dict[str, Any]],
    usage: LLMUsage | None = None,
    cost_usd: Decimal = Decimal("0"),
    latency_ms: int = 0,
    reasoning_trace: str | None = None,
    evidence_consulted: list[dict[str, Any]] | None = None,
    tool_calls_summary: list[dict[str, Any]] | None = None,
    agent_revised_verdict: dict[str, Any] | None = None,
    wallclock_seconds: int = 0,
    review_item_ids: tuple[uuid.UUID, ...] = (),
) -> AgentRun:
    usage = usage or LLMUsage(
        input_tokens_uncached=0,
        input_tokens_cache_creation=0,
        input_tokens_cached=0,
        output_tokens=0,
    )
    tool_calls = tool_calls_summary or []
    agent_run = AgentRun(
        intake_source_type=intake.source_type,
        intake_record_id=intake.intake_record_id,
        intake_extraction_id=intake.extraction_id,
        project_id=intake.project_id,
        source_run_id=intake.source_run_id,
        scrape_job_id=intake.scrape_job_id,
        profile_name=profile.name,
        profile_version=profile.profile_version,
        triggered_by=list(trigger_reasons),
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        input_tokens_uncached=usage.input_tokens_uncached,
        input_tokens_cache_creation=usage.input_tokens_cache_creation,
        input_tokens_cached=usage.input_tokens_cached,
        output_tokens=usage.output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        reasoning_trace=reasoning_trace,
        evidence_consulted=evidence_consulted or [],
        tool_calls_summary=tool_calls,
        matcher_original_verdict={"matcher_results": _json_safe(matcher_results)},
        agent_revised_verdict=_json_safe(agent_revised_verdict),
        outcome=outcome,
        error_text=error_text,
        budget_consumed_usd=cost_usd,
        tool_calls_count=len(tool_calls),
        wallclock_seconds=wallclock_seconds,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(agent_run)
    session.flush()
    for review_item_id in review_item_ids:
        session.add(AgentRunReviewItem(agent_run_id=agent_run.id, review_item_id=review_item_id))
    session.flush()
    return agent_run


def _validate_intake_profile(*, intake: IntakeRecord, profile: SourceProfile) -> None:
    if intake.source_type != profile.intake_source_type:
        raise ValueError(
            f"Profile {profile.name} expects source_type={profile.intake_source_type}; "
            f"got {intake.source_type}."
        )


def _agent_enabled(*, settings: Settings, profile: SourceProfile) -> bool:
    if not hasattr(settings, profile.kill_switch_setting):
        raise RuntimeError(
            f"Settings is missing kill switch {profile.kill_switch_setting} "
            f"for profile {profile.name}."
        )
    return bool(getattr(settings, profile.kill_switch_setting))


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (uuid.UUID, datetime, Decimal)):
        return str(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump())
    return str(value)
