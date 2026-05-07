from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PERMIT_DATA_QUALITIES = frozenset({"low", "high"})
NEWS_STATUS_PROMOTION_POLICIES = frozenset(
    {"wait_for_permit_corroboration", "auto_promote_unverified"}
)
DEFAULT_PERMIT_DATA_QUALITY = "low"
DEFAULT_NEWS_STATUS_PROMOTION_POLICY = "auto_promote_unverified"


@dataclass(frozen=True, slots=True)
class JurisdictionPolicy:
    slug: str | None
    permit_data_quality: str = DEFAULT_PERMIT_DATA_QUALITY
    news_status_promotion_policy: str = DEFAULT_NEWS_STATUS_PROMOTION_POLICY
    permit_data_quality_validated_at: str | None = None
    permit_data_quality_notes: str | None = None
    path: Path | None = None

    @property
    def is_default(self) -> bool:
        return self.path is None

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "permit_data_quality": self.permit_data_quality,
            "news_status_promotion_policy": self.news_status_promotion_policy,
            "permit_data_quality_validated_at": self.permit_data_quality_validated_at,
            "permit_data_quality_notes": self.permit_data_quality_notes,
            "policy_source": "default" if self.is_default else "config",
        }


def default_jurisdiction_policy(slug: str | None = None) -> JurisdictionPolicy:
    return JurisdictionPolicy(slug=slug)


def default_jurisdiction_config_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "jurisdictions"


def load_jurisdiction_policy(
    jurisdiction_slug: str,
    *,
    config_dir: Path | None = None,
) -> JurisdictionPolicy:
    base_dir = config_dir or default_jurisdiction_config_dir()
    path = base_dir / f"{jurisdiction_slug}.yaml"
    if not path.exists():
        return default_jurisdiction_policy(jurisdiction_slug)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"Jurisdiction policy must be a mapping: {path}")
    return parse_jurisdiction_policy(data, jurisdiction_slug=jurisdiction_slug, path=path)


def parse_jurisdiction_policy(
    data: Mapping[str, Any],
    *,
    jurisdiction_slug: str,
    path: Path | None = None,
) -> JurisdictionPolicy:
    declared_slug = data.get("slug")
    if declared_slug is not None and declared_slug != jurisdiction_slug:
        raise ValueError(
            f"Jurisdiction policy declared slug '{declared_slug}', "
            f"expected '{jurisdiction_slug}'."
        )
    permit_data_quality = _required_choice(
        data.get("permit_data_quality"),
        field_name="permit_data_quality",
        allowed=PERMIT_DATA_QUALITIES,
    )
    promotion_policy = _required_choice(
        data.get("news_status_promotion_policy"),
        field_name="news_status_promotion_policy",
        allowed=NEWS_STATUS_PROMOTION_POLICIES,
    )
    return JurisdictionPolicy(
        slug=jurisdiction_slug,
        permit_data_quality=permit_data_quality,
        news_status_promotion_policy=promotion_policy,
        permit_data_quality_validated_at=_optional_string(
            data.get("permit_data_quality_validated_at")
        ),
        permit_data_quality_notes=_optional_string(data.get("permit_data_quality_notes")),
        path=path,
    )


def _required_choice(value: Any, *, field_name: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None
