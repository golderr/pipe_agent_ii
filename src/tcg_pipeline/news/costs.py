from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import CostCap, CostCapOverride, LLMCostUsage, SystemAlert

LLM_COST_CAP_LOCK_KEY = 0x4E455753434150
NEWS_COST_TIMEZONE = ZoneInfo("America/Los_Angeles")
NEWS_COST_BUCKET = "news"
RESERVATION_PASS_NAME = "reserved"
RESERVATION_PROVIDER = "_reservation_"
RESERVATION_MODEL = "_reservation_"
DEFAULT_DAILY_WARN_USD = Decimal("25.00")
DEFAULT_DAILY_HARD_USD = Decimal("35.00")
DEFAULT_BUCKET_CAPS = {
    NEWS_COST_BUCKET: (DEFAULT_DAILY_WARN_USD, DEFAULT_DAILY_HARD_USD),
    "permits": (Decimal("50.00"), Decimal("75.00")),
}


@dataclass(frozen=True, slots=True)
class ActiveCostCap:
    cost_date: date
    daily_warn_usd: Decimal
    daily_hard_usd: Decimal
    spent_usd: Decimal


def cost_date_for(now: datetime | None = None) -> date:
    current = _as_aware(now or datetime.now(UTC))
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(NEWS_COST_TIMEZONE).date()


def reserve_llm_cost(
    session: Session,
    *,
    pass_name: str,
    model: str,
    estimated_cost_usd: Decimal,
    bucket: str = NEWS_COST_BUCKET,
    provider: str = "anthropic",
    now: datetime | None = None,
) -> ActiveCostCap | None:
    current = _as_aware(now or datetime.now(UTC))
    cost_date = cost_date_for(current)
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": LLM_COST_CAP_LOCK_KEY},
    )
    cap = active_cost_cap(session, cost_date=cost_date, bucket=bucket, now=current)
    projected_spend = cap.spent_usd + estimated_cost_usd
    if projected_spend >= cap.daily_warn_usd:
        _raise_cost_alert(
            session,
            alert_key=_alert_key(bucket, "warn"),
            severity="warning",
            message=f"{bucket} LLM daily warn cost cap reached.",
            bucket=bucket,
            cost_date=cost_date,
            spent_usd=projected_spend,
            cap_usd=cap.daily_warn_usd,
            pass_name=pass_name,
            provider=provider,
            model=model,
            now=current,
        )
    if projected_spend > cap.daily_hard_usd:
        _raise_cost_alert(
            session,
            alert_key=_alert_key(bucket, "hard"),
            severity="high",
            message=f"{bucket} LLM daily hard cost cap reached.",
            bucket=bucket,
            cost_date=cost_date,
            spent_usd=projected_spend,
            cap_usd=cap.daily_hard_usd,
            pass_name=pass_name,
            provider=provider,
            model=model,
            now=current,
        )
        return None
    _increment_cost_rollup(
        session,
        bucket=bucket,
        cost_date=cost_date,
        capability=RESERVATION_PASS_NAME,
        provider=RESERVATION_PROVIDER,
        model=RESERVATION_MODEL,
        spent_usd=estimated_cost_usd,
    )
    return ActiveCostCap(
        cost_date=cost_date,
        daily_warn_usd=cap.daily_warn_usd,
        daily_hard_usd=cap.daily_hard_usd,
        spent_usd=projected_spend,
    )


def release_llm_cost_reservation(
    session: Session,
    *,
    reserved_cost_usd: Decimal,
    bucket: str = NEWS_COST_BUCKET,
    now: datetime | None = None,
) -> None:
    if reserved_cost_usd == 0:
        return
    _increment_cost_rollup(
        session,
        bucket=bucket,
        cost_date=cost_date_for(now),
        capability=RESERVATION_PASS_NAME,
        provider=RESERVATION_PROVIDER,
        model=RESERVATION_MODEL,
        spent_usd=-reserved_cost_usd,
    )


def record_llm_cost(
    session: Session,
    *,
    pass_name: str,
    model: str,
    input_tokens_uncached: int,
    input_tokens_cache_creation: int,
    input_tokens_cached: int,
    output_tokens: int,
    cost_usd: Decimal,
    reserved_cost_usd: Decimal = Decimal("0"),
    bucket: str = NEWS_COST_BUCKET,
    provider: str = "anthropic",
    now: datetime | None = None,
) -> None:
    cost_date = cost_date_for(now)
    if reserved_cost_usd:
        _increment_cost_rollup(
            session,
            bucket=bucket,
            cost_date=cost_date,
            capability=RESERVATION_PASS_NAME,
            provider=RESERVATION_PROVIDER,
            model=RESERVATION_MODEL,
            spent_usd=-reserved_cost_usd,
        )
    _increment_cost_rollup(
        session,
        bucket=bucket,
        cost_date=cost_date,
        capability=pass_name,
        provider=provider,
        model=model,
        call_count=1,
        input_tokens_uncached=input_tokens_uncached,
        input_tokens_cache_creation=input_tokens_cache_creation,
        input_tokens_cached=input_tokens_cached,
        output_tokens=output_tokens,
        spent_usd=cost_usd,
    )


def active_cost_cap(
    session: Session,
    *,
    cost_date: date,
    bucket: str = NEWS_COST_BUCKET,
    now: datetime | None = None,
) -> ActiveCostCap:
    cap = session.execute(
        select(CostCap)
        .where(
            CostCap.bucket == bucket,
            CostCap.effective_from <= cost_date,
            (CostCap.effective_to.is_(None)) | (CostCap.effective_to >= cost_date),
        )
        .order_by(CostCap.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    current = _as_aware(now or datetime.now(UTC))
    if cap is None:
        warn, hard = DEFAULT_BUCKET_CAPS.get(
            bucket,
            (DEFAULT_DAILY_WARN_USD, DEFAULT_DAILY_HARD_USD),
        )
    else:
        warn = Decimal(cap.daily_warn_usd)
        hard = Decimal(cap.daily_hard_usd)
    override = session.execute(
        select(CostCapOverride)
        .where(
            CostCapOverride.bucket == bucket,
            CostCapOverride.effective_from <= current,
            CostCapOverride.effective_until > current,
        )
        .order_by(
            CostCapOverride.override_hard_usd.desc(),
            CostCapOverride.effective_until.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if override is not None:
        hard = Decimal(override.override_hard_usd)
        if override.override_warn_usd is not None:
            warn = Decimal(override.override_warn_usd)
    return ActiveCostCap(
        cost_date=cost_date,
        daily_warn_usd=warn,
        daily_hard_usd=hard,
        spent_usd=current_day_spend(session, cost_date=cost_date, bucket=bucket),
    )


def current_day_spend(
    session: Session,
    *,
    cost_date: date,
    bucket: str = NEWS_COST_BUCKET,
) -> Decimal:
    # Reservation rows can briefly go negative while a completed call true-ups its
    # estimate to actual cost. The daily spend check intentionally sums all rows.
    spent = session.execute(
        select(func.coalesce(func.sum(LLMCostUsage.spent_usd), 0)).where(
            LLMCostUsage.bucket == bucket,
            LLMCostUsage.cost_date == cost_date,
        )
    ).scalar_one()
    return Decimal(spent)


def _increment_cost_rollup(
    session: Session,
    *,
    bucket: str,
    cost_date: date,
    capability: str,
    provider: str,
    model: str,
    call_count: int = 0,
    input_tokens_uncached: int = 0,
    input_tokens_cache_creation: int = 0,
    input_tokens_cached: int = 0,
    output_tokens: int = 0,
    spent_usd: Decimal = Decimal("0"),
) -> None:
    statement = (
        insert(LLMCostUsage)
        .values(
            bucket=bucket,
            cost_date=cost_date,
            capability=capability,
            provider=provider,
            model=model,
            call_count=call_count,
            input_tokens_uncached=input_tokens_uncached,
            input_tokens_cache_creation=input_tokens_cache_creation,
            input_tokens_cached=input_tokens_cached,
            output_tokens=output_tokens,
            spent_usd=spent_usd,
        )
        .on_conflict_do_update(
            index_elements=[
                LLMCostUsage.bucket,
                LLMCostUsage.cost_date,
                LLMCostUsage.capability,
                LLMCostUsage.provider,
                LLMCostUsage.model,
            ],
            set_={
                "call_count": LLMCostUsage.call_count + call_count,
                "input_tokens_uncached": (
                    LLMCostUsage.input_tokens_uncached + input_tokens_uncached
                ),
                "input_tokens_cache_creation": (
                    LLMCostUsage.input_tokens_cache_creation + input_tokens_cache_creation
                ),
                "input_tokens_cached": (
                    LLMCostUsage.input_tokens_cached + input_tokens_cached
                ),
                "output_tokens": LLMCostUsage.output_tokens + output_tokens,
                "spent_usd": LLMCostUsage.spent_usd + spent_usd,
            },
        )
    )
    session.execute(statement)


def _raise_cost_alert(
    session: Session,
    *,
    alert_key: str,
    severity: str,
    message: str,
    bucket: str,
    cost_date: date,
    spent_usd: Decimal,
    cap_usd: Decimal,
    pass_name: str,
    provider: str,
    model: str,
    now: datetime,
) -> None:
    statement = (
        insert(SystemAlert)
        .values(
            alert_key=alert_key,
            severity=severity,
            scope=_alert_scope(bucket=bucket, cost_date=cost_date),
            message=message,
            detail={
                "bucket": bucket,
                "spent_usd": str(spent_usd),
                "cap_usd": str(cap_usd),
                "pass": pass_name,
                "provider": provider,
                "model": model,
            },
            raised_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                SystemAlert.alert_key,
                text("COALESCE(scope::text, '{}')"),
            ],
            index_where=text("cleared_at IS NULL"),
            set_={
                "severity": severity,
                "message": message,
                "detail": {
                    "bucket": bucket,
                    "spent_usd": str(spent_usd),
                    "cap_usd": str(cap_usd),
                    "pass": pass_name,
                    "provider": provider,
                    "model": model,
                },
                "last_seen_at": now,
            },
        )
    )
    session.execute(statement)


def _alert_key(bucket: str, cap_kind: str) -> str:
    if bucket == NEWS_COST_BUCKET:
        return f"news_daily_cost_{cap_kind}_cap_reached"
    return f"{bucket}_daily_cost_{cap_kind}_cap_reached"


def _alert_scope(*, bucket: str, cost_date: date) -> dict[str, str]:
    if bucket == NEWS_COST_BUCKET:
        return {"cost_date": cost_date.isoformat()}
    return {"bucket": bucket, "cost_date": cost_date.isoformat()}


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
