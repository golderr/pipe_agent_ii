from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import NewsCostCap, NewsExtractionCost, SystemAlert

NEWS_COST_CAP_LOCK_KEY = 0x4E455753434150
NEWS_COST_TIMEZONE = ZoneInfo("America/Los_Angeles")
RESERVATION_PASS_NAME = "reserved"
RESERVATION_MODEL = "_reservation_"
DEFAULT_DAILY_WARN_USD = Decimal("25.00")
DEFAULT_DAILY_HARD_USD = Decimal("35.00")


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
    now: datetime | None = None,
) -> ActiveCostCap | None:
    current = _as_aware(now or datetime.now(UTC))
    cost_date = cost_date_for(current)
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": NEWS_COST_CAP_LOCK_KEY},
    )
    cap = active_cost_cap(session, cost_date=cost_date, now=current)
    projected_spend = cap.spent_usd + estimated_cost_usd
    if projected_spend >= cap.daily_warn_usd:
        _raise_cost_alert(
            session,
            alert_key="news_daily_cost_warn_cap_reached",
            severity="warning",
            message="News extraction daily warn cost cap reached.",
            cost_date=cost_date,
            spent_usd=projected_spend,
            cap_usd=cap.daily_warn_usd,
            pass_name=pass_name,
            model=model,
            now=current,
        )
    if projected_spend > cap.daily_hard_usd:
        _raise_cost_alert(
            session,
            alert_key="news_daily_cost_hard_cap_reached",
            severity="high",
            message="News extraction daily hard cost cap reached.",
            cost_date=cost_date,
            spent_usd=projected_spend,
            cap_usd=cap.daily_hard_usd,
            pass_name=pass_name,
            model=model,
            now=current,
        )
        return None
    _increment_cost_rollup(
        session,
        cost_date=cost_date,
        pass_name=RESERVATION_PASS_NAME,
        model=RESERVATION_MODEL,
        cost_usd=estimated_cost_usd,
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
    now: datetime | None = None,
) -> None:
    if reserved_cost_usd == 0:
        return
    _increment_cost_rollup(
        session,
        cost_date=cost_date_for(now),
        pass_name=RESERVATION_PASS_NAME,
        model=RESERVATION_MODEL,
        cost_usd=-reserved_cost_usd,
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
    now: datetime | None = None,
) -> None:
    cost_date = cost_date_for(now)
    if reserved_cost_usd:
        _increment_cost_rollup(
            session,
            cost_date=cost_date,
            pass_name=RESERVATION_PASS_NAME,
            model=RESERVATION_MODEL,
            cost_usd=-reserved_cost_usd,
        )
    _increment_cost_rollup(
        session,
        cost_date=cost_date,
        pass_name=pass_name,
        model=model,
        call_count=1,
        input_tokens_uncached=input_tokens_uncached,
        input_tokens_cache_creation=input_tokens_cache_creation,
        input_tokens_cached=input_tokens_cached,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def active_cost_cap(
    session: Session,
    *,
    cost_date: date,
    now: datetime | None = None,
) -> ActiveCostCap:
    cap = session.execute(
        select(NewsCostCap)
        .where(NewsCostCap.effective_date <= cost_date)
        .order_by(NewsCostCap.effective_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    current = _as_aware(now or datetime.now(UTC))
    if cap is None:
        warn = DEFAULT_DAILY_WARN_USD
        hard = DEFAULT_DAILY_HARD_USD
    else:
        warn = Decimal(cap.daily_warn_usd)
        hard = Decimal(cap.daily_hard_usd)
        if (
            cap.override_until is not None
            and cap.override_hard_usd is not None
            and _as_aware(cap.override_until) > current
        ):
            hard = Decimal(cap.override_hard_usd)
    return ActiveCostCap(
        cost_date=cost_date,
        daily_warn_usd=warn,
        daily_hard_usd=hard,
        spent_usd=current_day_spend(session, cost_date=cost_date),
    )


def current_day_spend(session: Session, *, cost_date: date) -> Decimal:
    # Reservation rows can briefly go negative while a completed call true-ups its
    # estimate to actual cost. The daily spend check intentionally sums all rows.
    spent = session.execute(
        select(func.coalesce(func.sum(NewsExtractionCost.cost_usd), 0)).where(
            NewsExtractionCost.cost_date == cost_date
        )
    ).scalar_one()
    return Decimal(spent)


def _increment_cost_rollup(
    session: Session,
    *,
    cost_date: date,
    pass_name: str,
    model: str,
    call_count: int = 0,
    input_tokens_uncached: int = 0,
    input_tokens_cache_creation: int = 0,
    input_tokens_cached: int = 0,
    output_tokens: int = 0,
    cost_usd: Decimal = Decimal("0"),
) -> None:
    statement = (
        insert(NewsExtractionCost)
        .values(
            cost_date=cost_date,
            **{"pass": pass_name},
            model=model,
            call_count=call_count,
            input_tokens_uncached=input_tokens_uncached,
            input_tokens_cache_creation=input_tokens_cache_creation,
            input_tokens_cached=input_tokens_cached,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        .on_conflict_do_update(
            index_elements=[
                NewsExtractionCost.cost_date,
                NewsExtractionCost.pass_name,
                NewsExtractionCost.model,
            ],
            set_={
                "call_count": NewsExtractionCost.call_count + call_count,
                "input_tokens_uncached": (
                    NewsExtractionCost.input_tokens_uncached + input_tokens_uncached
                ),
                "input_tokens_cache_creation": (
                    NewsExtractionCost.input_tokens_cache_creation
                    + input_tokens_cache_creation
                ),
                "input_tokens_cached": (
                    NewsExtractionCost.input_tokens_cached + input_tokens_cached
                ),
                "output_tokens": NewsExtractionCost.output_tokens + output_tokens,
                "cost_usd": NewsExtractionCost.cost_usd + cost_usd,
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
    cost_date: date,
    spent_usd: Decimal,
    cap_usd: Decimal,
    pass_name: str,
    model: str,
    now: datetime,
) -> None:
    statement = (
        insert(SystemAlert)
        .values(
            alert_key=alert_key,
            severity=severity,
            scope={"cost_date": cost_date.isoformat()},
            message=message,
            detail={
                "spent_usd": str(spent_usd),
                "cap_usd": str(cap_usd),
                "pass": pass_name,
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
                    "spent_usd": str(spent_usd),
                    "cap_usd": str(cap_usd),
                    "pass": pass_name,
                    "model": model,
                },
                "last_seen_at": now,
            },
        )
    )
    session.execute(statement)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
