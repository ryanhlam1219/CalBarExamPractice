from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    SourceDocument,
    SourceSpan,
)
from app.db.models.enums import IngestionStatus, ReviewStatus
from app.db.models.rules import LegalSubject
from app.db.models.templates import (
    CanonicalIssueCandidate,
    DocumentAbbreviation,
    EssayTemplate,
    TemplateCrossReference,
    TemplateNode,
    TemplateRuleCandidate,
)
from app.db.repositories.documents import add_source_span, page_map
from app.parsing.schimmel.models import (
    SchimmelDocumentCandidate,
    SchimmelSubjectSection,
    SchimmelTemplateNodeCandidate,
)


def replace_essay_template_parse(
    session: Session,
    source_document: SourceDocument,
    document: SchimmelDocumentCandidate,
    parser_version: str,
) -> dict[str, int]:
    """Store parsed Schimmel document into database tables.

    Replaces any existing template data for the source document.
    """
    # Clean existing template data for this document
    existing_template_ids = session.scalars(
        select(EssayTemplate.id).where(EssayTemplate.source_document_id == source_document.id)
    ).all()

    for tid in existing_template_ids:
        node_ids = session.scalars(
            select(TemplateNode.id).where(TemplateNode.essay_template_id == tid)
        ).all()
        if node_ids:
            session.execute(delete(TemplateCrossReference).where(
                TemplateCrossReference.source_template_node_id.in_(node_ids)
            ))
            session.execute(delete(TemplateRuleCandidate).where(
                TemplateRuleCandidate.template_node_id.in_(node_ids)
            ))
        session.execute(delete(TemplateNode).where(TemplateNode.essay_template_id == tid))

    if existing_template_ids:
        session.execute(delete(EssayTemplate).where(
            EssayTemplate.id.in_(existing_template_ids)
        ))

    session.execute(delete(DocumentAbbreviation).where(
        DocumentAbbreviation.source_document_id == source_document.id
    ))

    session.execute(
        delete(SourceSpan).where(
            SourceSpan.source_document_id == source_document.id,
            SourceSpan.entity_type.in_([
                "essay_template", "template_node", "template_rule_candidate",
                "template_cross_reference", "document_abbreviation",
                "canonical_issue_candidate",
            ]),
        )
    )
    session.flush()

    pages = page_map(session, source_document.id)
    templates_created = 0
    abbreviations_created = 0

    counters = {"nodes": 0, "rules": 0, "cross_refs": 0}

    for section in document.subjects:
        if not section.candidates:
            continue

        # Get or create legal subject
        subject = _get_or_create_subject(session, section.normalized_name)
        template_name = f"{section.normalized_name} Essay Template"

        template = EssayTemplate(
            legal_subject_id=subject.id,
            source_document_id=source_document.id,
            name=template_name,
            jurisdiction_scope="GENERAL",
            description=None,
            version="1",
            parse_confidence=0.90,
            review_status=ReviewStatus.UNREVIEWED.value,
            parser_version=parser_version,
            metadata_json={"source": "schimmel_template_parser"},
        )
        session.add(template)
        session.flush()
        templates_created += 1

        # Add source span for template
        template_page = pages.get(section.start_page)
        add_source_span(
            session,
            source_document_id=source_document.id,
            document_page_id=template_page.id if template_page else None,
            entity_type="essay_template",
            entity_id=template.id,
            quoted_text=section.raw_heading,
            extraction_method="schimmel-subject-detector",
        )

        # Store nodes recursively
        for root_node in section.candidates:
            _store_node_recursive(
                session, template, subject, source_document, pages,
                root_node, None, 0, parser_version,
                counters,
            )

    # Store abbreviations
    for abbr in document.abbreviations:
        abbr_subject_id = None
        if abbr.legal_subject_id:
            abbr_subject_id = abbr.legal_subject_id

        db_abbr = DocumentAbbreviation(
            source_document_id=source_document.id,
            legal_subject_id=abbr_subject_id,
            abbreviation=abbr.abbreviation,
            normalized_term=abbr.normalized_term,
            context_notes=abbr.context_notes,
            confidence=abbr.confidence,
            review_status=abbr.review_status,
        )
        session.add(db_abbr)
        session.flush()
        abbreviations_created += 1

    source_document.ingestion_status = IngestionStatus.PARSED.value
    session.flush()

    return {
        "templates": templates_created,
        "nodes": counters["nodes"],
        "rule_candidates": counters["rules"],
        "cross_references": counters["cross_refs"],
        "abbreviations": abbreviations_created,
    }


def _get_or_create_subject(session: Session, name: str) -> LegalSubject:
    """Get or create a legal subject."""
    canonical = name.lower().replace(" ", "_")
    subject = session.scalar(select(LegalSubject).where(LegalSubject.canonical_name == canonical))
    if subject is None:
        subject = LegalSubject(canonical_name=canonical, display_name=name)
        session.add(subject)
        session.flush()
    return subject


def _store_node_recursive(
    session: Session,
    template: EssayTemplate,
    subject: LegalSubject,
    source_document: SourceDocument,
    pages: dict,
    node_candidate: SchimmelTemplateNodeCandidate,
    parent_db_node: TemplateNode | None,
    order: int,
    parser_version: str,
    counters: dict[str, int],
) -> TemplateNode:
    """Recursively store a template node and its children."""
    db_node = TemplateNode(
        essay_template_id=template.id,
        parent_node_id=parent_db_node.id if parent_db_node else None,
        node_type=node_candidate.node_type,
        title=node_candidate.title,
        raw_text=node_candidate.raw_text,
        normalized_text=node_candidate.normalized_text,
        display_order=order,
        depth=node_candidate.depth,
        jurisdiction_scope=node_candidate.jurisdiction_scope,
        parse_confidence=node_candidate.parse_confidence,
        review_status=ReviewStatus.UNREVIEWED.value,
        parser_version=parser_version,
        metadata_json=node_candidate.evidence,
    )
    session.add(db_node)
    session.flush()
    counters["nodes"] += 1

    # Source span
    node_page = pages.get(node_candidate.page_number)
    add_source_span(
        session,
        source_document_id=source_document.id,
        document_page_id=node_page.id if node_page else None,
        entity_type="template_node",
        entity_id=db_node.id,
        quoted_text=node_candidate.raw_text or node_candidate.title,
        extraction_method="schimmel-heading-classifier",
    )

    # Store rule candidates
    for rule in node_candidate.rule_candidates:
        db_rule = TemplateRuleCandidate(
            template_node_id=db_node.id,
            legal_subject_id=subject.id,
            raw_rule_text=rule.raw_rule_text,
            normalized_rule_text=rule.normalized_rule_text,
            jurisdiction_scope=rule.jurisdiction_scope,
            rule_variant=rule.rule_variant,
            source_document_id=source_document.id,
            start_page=rule.start_page,
            end_page=rule.end_page,
            parse_confidence=rule.parse_confidence,
            review_status=ReviewStatus.UNREVIEWED.value,
            parser_version=parser_version,
        )
        session.add(db_rule)
        session.flush()
        counters["rules"] += 1

        # Source span for rule
        rule_page = pages.get(rule.start_page)
        add_source_span(
            session,
            source_document_id=source_document.id,
            document_page_id=rule_page.id if rule_page else None,
            entity_type="template_rule_candidate",
            entity_id=db_rule.id,
            quoted_text=rule.raw_rule_text[:500],
            extraction_method="schimmel-rule-extractor",
        )

    # Store cross-references
    for cr in node_candidate.cross_references:
        db_cr = TemplateCrossReference(
            source_template_node_id=db_node.id,
            target_text=cr.target_text,
            resolution_status=cr.resolution_status,
            parse_confidence=cr.parse_confidence,
            metadata_json=cr.metadata,
        )
        session.add(db_cr)
        session.flush()
        counters["cross_refs"] += 1

    # Store children recursively
    for child_idx, child in enumerate(node_candidate.children):
        _store_node_recursive(
            session, template, subject, source_document, pages,
            child, db_node, child_idx, parser_version,
            counters,
        )

    return db_node


def get_template_counts(session: Session, source_document_id: int) -> dict[str, int]:
    """Get template counts for a source document."""
    templates = session.scalars(
        select(EssayTemplate).where(EssayTemplate.source_document_id == source_document_id)
    ).all()
    template_ids = [t.id for t in templates]
    node_count = len(session.scalars(
        select(TemplateNode.id).where(TemplateNode.essay_template_id.in_(template_ids))
    ).all()) if template_ids else 0
    return {
        "templates": len(templates),
        "nodes": node_count,
    }


def get_subject_templates(session: Session, subject_name: str) -> list[EssayTemplate]:
    """Get templates for a subject."""
    canonical = subject_name.lower().replace(" ", "_")
    subject = session.scalar(select(LegalSubject).where(LegalSubject.canonical_name == canonical))
    if subject is None:
        return []
    return list(session.scalars(
        select(EssayTemplate).where(EssayTemplate.legal_subject_id == subject.id)
    ).all())