from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    LegalRule,
    LegalSubject,
    LegalTopic,
    RuleComponent,
    SourceDocument,
    SourceSpan,
)
from app.db.models.enums import IngestionStatus
from app.db.repositories.documents import add_source_span, page_map
from app.parsing.text import normalized_key
from app.schemas.rules import RuleParseResult


def replace_rule_parse(
    session: Session,
    source_document: SourceDocument,
    parse_result: RuleParseResult,
) -> dict[str, int]:
    existing_rule_ids = session.scalars(
        select(LegalRule.id).where(
            LegalRule.source_document_id == source_document.id,
            LegalRule.parser_version == parse_result.parser_version,
        )
    ).all()
    if existing_rule_ids:
        session.execute(delete(RuleComponent).where(RuleComponent.legal_rule_id.in_(existing_rule_ids)))
        session.execute(delete(LegalRule).where(LegalRule.id.in_(existing_rule_ids)))
    session.execute(
        delete(SourceSpan).where(
            SourceSpan.source_document_id == source_document.id,
            SourceSpan.entity_type.in_(["legal_subject", "legal_topic", "legal_rule", "rule_component"]),
        )
    )
    session.flush()

    subject = _get_or_create_subject(
        session, parse_result.subject_canonical_name, parse_result.subject_display_name
    )
    topic_cache: dict[str, LegalTopic] = {}
    for topic_path in parse_result.topics:
        _get_or_create_topic_path(session, subject, topic_path, topic_cache)
    pages = page_map(session, source_document.id)

    if parse_result.subject_source_text:
        subject_page = pages.get(parse_result.subject_source_page or 1)
        add_source_span(
            session,
            source_document_id=source_document.id,
            document_page_id=subject_page.id if subject_page else None,
            entity_type="legal_subject",
            entity_id=subject.id,
            quoted_text=parse_result.subject_source_text,
            extraction_method="pymupdf-layout",
        )
    for topic_source in parse_result.topic_sources:
        topic = _get_or_create_topic_path(session, subject, topic_source.topic_path, topic_cache)
        topic_page = pages.get(topic_source.source_page)
        add_source_span(
            session,
            source_document_id=source_document.id,
            document_page_id=topic_page.id if topic_page else None,
            entity_type="legal_topic",
            entity_id=topic.id,
            quoted_text=topic_source.source_text,
            extraction_method="pymupdf-layout",
        )

    rules_created = 0
    components_created = 0
    seen_keys: dict[tuple[int, str], int] = {}
    for parsed in parse_result.rules:
        topic = _get_or_create_topic_path(session, subject, parsed.topic_path, topic_cache)
        canon = parsed.canonical_name
        key = (topic.id, canon)
        if key in seen_keys:
            seen_keys[key] += 1
            canon = f"{canon} ({seen_keys[key]})"
        else:
            seen_keys[key] = 1
        db_rule = LegalRule(
            source_document_id=source_document.id,
            legal_subject_id=subject.id,
            legal_topic_id=topic.id,
            canonical_name=canon,
            rule_statement=parsed.rule_statement,
            short_rule_statement=parsed.short_rule_statement,
            jurisdiction_scope=parsed.jurisdiction_scope,
            rule_status=parsed.rule_status,
            parse_confidence=parsed.parse_confidence,
            review_status=parsed.review_status,
            parser_version=parse_result.parser_version,
            metadata_json=parsed.metadata,
        )
        session.add(db_rule)
        session.flush()
        rules_created += 1
        start_page = pages.get(parsed.start_page)
        add_source_span(
            session,
            source_document_id=source_document.id,
            document_page_id=start_page.id if start_page else None,
            entity_type="legal_rule",
            entity_id=db_rule.id,
            quoted_text=parsed.source_text,
            extraction_method="pymupdf-layout",
        )

        for component in parsed.components:
            db_component = RuleComponent(
                legal_rule_id=db_rule.id,
                component_type=component.component_type,
                label=component.label,
                content=component.content,
                display_order=component.display_order,
                metadata_json=component.metadata,
            )
            session.add(db_component)
            session.flush()
            components_created += 1
            component_page = pages.get(component.source_page)
            add_source_span(
                session,
                source_document_id=source_document.id,
                document_page_id=component_page.id if component_page else None,
                entity_type="rule_component",
                entity_id=db_component.id,
                quoted_text=component.source_text,
                extraction_method="pymupdf-layout",
            )

    source_document.ingestion_status = IngestionStatus.PARSED.value
    session.flush()
    return {"topics": len(topic_cache), "rules": rules_created, "rule_components": components_created}


def _get_or_create_subject(session: Session, canonical_name: str, display_name: str) -> LegalSubject:
    subject = session.scalar(select(LegalSubject).where(LegalSubject.canonical_name == canonical_name))
    if subject:
        return subject
    subject = LegalSubject(canonical_name=canonical_name, display_name=display_name)
    session.add(subject)
    session.flush()
    return subject


def _get_or_create_topic_path(
    session: Session,
    subject: LegalSubject,
    topic_path: list[str],
    cache: dict[str, LegalTopic],
) -> LegalTopic:
    path_parts: list[str] = []
    parent: LegalTopic | None = None
    for index, name in enumerate(topic_path or [subject.display_name]):
        normalized = normalized_key(name) or f"topic-{index}"
        path_parts.append(normalized)
        hierarchy_path = "/".join(path_parts)
        if hierarchy_path in cache:
            parent = cache[hierarchy_path]
            continue
        topic = session.scalar(
            select(LegalTopic).where(
                LegalTopic.legal_subject_id == subject.id,
                LegalTopic.hierarchy_path == hierarchy_path,
            )
        )
        if topic is None:
            topic = LegalTopic(
                legal_subject_id=subject.id,
                parent_topic_id=parent.id if parent else None,
                name=name,
                normalized_name=normalized,
                hierarchy_path=hierarchy_path,
                display_order=len(cache),
            )
            session.add(topic)
            session.flush()
        cache[hierarchy_path] = topic
        parent = topic
    if parent is None:
        raise ValueError("Could not create legal topic path")
    return parent
