from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tcg_pipeline.db.models import NewsArticle

PROMPT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "news_prompts.yaml"
PROMPT_ROOT = Path(__file__).with_name("prompts")
PROMPT_ID_RE = re.compile(r".+_v\d+")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    prompt_id: str
    prompt_version: str
    system_template: str
    user_template: str
    schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    system_text: str
    user_text: str
    schema: dict[str, Any]


def load_active_prompt(pass_name: str, *, config_path: Path = PROMPT_CONFIG_PATH) -> PromptTemplate:
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    active = config.get("active") or {}
    prompt_id = active.get(pass_name)
    if not isinstance(prompt_id, str) or not prompt_id:
        raise RuntimeError(f"No active news prompt configured for pass '{pass_name}'.")
    return load_prompt(prompt_id)


def load_prompt(prompt_id: str) -> PromptTemplate:
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise RuntimeError(
            f"Invalid news prompt id '{prompt_id}'. Expected convention '<name>_v<number>'."
        )
    prompt_dir = PROMPT_ROOT / prompt_id
    if not prompt_dir.is_dir():
        raise RuntimeError(f"News prompt directory does not exist: {prompt_id}")
    system_template = (prompt_dir / "system.md").read_text(encoding="utf-8").strip()
    user_template = (prompt_dir / "user.md").read_text(encoding="utf-8").strip()
    schema = json.loads((prompt_dir / "schema.json").read_text(encoding="utf-8"))
    prompt_version = prompt_id.rsplit("_", maxsplit=1)[-1]
    return PromptTemplate(
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        system_template=system_template,
        user_template=user_template,
        schema=schema,
    )


def render_triage_prompt(article: NewsArticle) -> RenderedPrompt:
    template = load_active_prompt("triage")
    structural_signals_json = json.dumps(
        article.structural_signals or {},
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    user_text = template.user_template.format(
        title=article.title or "",
        published_at=article.published_at.isoformat() if article.published_at else "",
        source_name=article.source.slug if article.source else "",
        publication_section=article.publication_section or "",
        byline_author=article.byline_author or "",
        structural_signals_json=structural_signals_json,
        body_text=article.body_text or "",
    )
    prompt_hash = hashlib.sha256(
        (template.system_template + "\n\n" + user_text).encode("utf-8")
    ).hexdigest()
    return RenderedPrompt(
        prompt_id=template.prompt_id,
        prompt_version=template.prompt_version,
        prompt_hash=prompt_hash,
        system_text=template.system_template,
        user_text=user_text,
        schema=template.schema,
    )
