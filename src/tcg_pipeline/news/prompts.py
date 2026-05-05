from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    DeveloperAlias,
    DeveloperRegistry,
    NewsArticle,
    NewsSignalFlag,
    PipelineStatus,
    Project,
)

PROMPT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "news_prompts.yaml"
PROMPT_ROOT = Path(__file__).with_name("prompts")
PROMPT_ID_RE = re.compile(r".+_v\d+")
GLOSSARY_EXCLUDED_PROJECT_STATUSES = (
    PipelineStatus.INACTIVE,
    PipelineStatus.DELETE_DUPLICATE,
    PipelineStatus.DELETE_OUTSIDE_MARKET_AREA,
    PipelineStatus.DELETE_NOT_RESIDENTIAL,
)


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
    system_blocks: tuple[str, ...] = ()


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


def render_extraction_prompt(session: Session, article: NewsArticle) -> RenderedPrompt:
    template = load_active_prompt("extract")
    return _render_project_extraction_prompt(
        session,
        article,
        template=template,
        include_glossary=False,
    )


def render_reextraction_prompt(
    session: Session,
    article: NewsArticle,
    *,
    prior_extraction: Any,
    trigger_context: dict[str, Any],
) -> RenderedPrompt:
    template = load_active_prompt("reextract")
    previous_output_json = json.dumps(
        prior_extraction.output_json,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    trigger_context_json = json.dumps(
        trigger_context,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    previous_parse_status = prior_extraction.parse_status or ""
    previous_parse_error_text = prior_extraction.parse_error_text or ""
    return _render_project_extraction_prompt(
        session,
        article,
        template=template,
        include_glossary=True,
        extra_user_values={
            "previous_output_json": previous_output_json,
            "previous_parse_status": previous_parse_status,
            "previous_parse_error_text": previous_parse_error_text,
            "trigger_context_json": trigger_context_json,
        },
    )


def _render_project_extraction_prompt(
    session: Session,
    article: NewsArticle,
    *,
    template: PromptTemplate,
    include_glossary: bool,
    extra_user_values: dict[str, Any] | None = None,
) -> RenderedPrompt:
    system_blocks = [template.system_template]
    if include_glossary:
        system_blocks.append("Glossary:\n" + render_news_glossary(session, article))
    system_blocks.append("Signal flag registry:\n" + render_signal_flag_registry(session))
    system_text = "\n\n".join(system_blocks)
    metadata_json = json.dumps(
        {
            "title": article.title,
            "published_at": article.published_at.isoformat()
            if article.published_at
            else None,
            "source_name": article.source.slug if article.source else None,
            "publication_section": article.publication_section,
            "byline_author": article.byline_author,
            "url_canonical": article.url_canonical,
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    structural_signals_json = json.dumps(
        article.structural_signals or {},
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    user_values: dict[str, Any] = {
        "article_metadata_json": metadata_json,
        "structural_signals_json": structural_signals_json,
        "body_text_with_offsets": _body_text_with_offsets(article.body_text or ""),
    }
    if extra_user_values:
        user_values.update(extra_user_values)
    user_text = template.user_template.format(
        **user_values,
    )
    prompt_hash = hashlib.sha256(
        (
            system_text
            + "\n\n"
            + user_text
            + "\n\n"
            + json.dumps(template.schema, sort_keys=True)
        ).encode("utf-8")
    ).hexdigest()
    return RenderedPrompt(
        prompt_id=template.prompt_id,
        prompt_version=template.prompt_version,
        prompt_hash=prompt_hash,
        system_text=system_text,
        user_text=user_text,
        schema=template.schema,
        system_blocks=tuple(system_blocks),
    )


def render_news_glossary(session: Session, article: NewsArticle) -> str:
    market_id = article.source.market_id if article.source else None
    developer_lines = ["Developers:"]
    developers = session.execute(
        select(DeveloperRegistry).order_by(DeveloperRegistry.canonical_name.asc())
    ).scalars()
    developer_count = 0
    for developer in developers:
        aliases = session.execute(
            select(DeveloperAlias.alias_name)
            .where(DeveloperAlias.developer_id == developer.id)
            .order_by(DeveloperAlias.alias_name.asc())
        ).scalars()
        alias_text = ", ".join(aliases)
        developer_lines.append(
            f"- id={developer.id} canonical_name={developer.canonical_name}"
            + (f" aliases={alias_text}" if alias_text else "")
        )
        developer_count += 1
    if developer_count == 0:
        developer_lines.append("- none")

    project_query = select(Project).where(
        ~Project.pipeline_status.in_(GLOSSARY_EXCLUDED_PROJECT_STATUSES)
    )
    if market_id is not None:
        project_query = project_query.where(Project.market_id == market_id)
    project_query = project_query.order_by(Project.project_name.asc().nulls_last())
    project_lines = ["Projects:"]
    project_count = 0
    for project in session.execute(project_query).scalars():
        names = [project.project_name] if project.project_name else []
        names.extend(project.previous_names or [])
        name_text = ", ".join(name for name in names if name)
        project_lines.append(
            f"- id={project.id} name={name_text or '(unnamed)'} "
            f"address={project.canonical_address}"
        )
        project_count += 1
    if project_count == 0:
        project_lines.append("- none")
    return "\n".join([*developer_lines, "", *project_lines])


def render_signal_flag_registry(session: Session) -> str:
    flags = session.execute(
        select(NewsSignalFlag)
        .where(NewsSignalFlag.active.is_(True), NewsSignalFlag.retired_at.is_(None))
        .order_by(NewsSignalFlag.category.asc(), NewsSignalFlag.flag_key.asc())
    ).scalars()
    lines = ["Signal flags:"]
    count = 0
    for flag in flags:
        examples = "; ".join(flag.example_phrases or [])
        lines.append(
            f"- {flag.flag_key} ({flag.category}): {flag.description}"
            + (f" Examples: {examples}" if examples else "")
        )
        count += 1
    if count == 0:
        lines.append("- none")
    return "\n".join(lines)


def _body_text_with_offsets(body_text: str) -> str:
    lines: list[str] = []
    for offset in range(0, len(body_text), 100):
        chunk = body_text[offset : offset + 100]
        lines.append(f"{offset:06d}: {chunk}")
    return "\n".join(lines)
