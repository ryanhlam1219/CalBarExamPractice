from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    EssayQuestion,
    LegalRule,
    RuleComponent,
    SelectedAnswer,
    SourceDocument,
    SourceSpan,
)


def document_validation_summary(session: Session, source_document_id: int) -> dict[str, object]:
    document = session.get(SourceDocument, source_document_id)
    if document is None:
        raise ValueError(f"Source document {source_document_id} was not found")
    question_count = len(
        session.scalars(select(EssayQuestion.id).where(EssayQuestion.source_document_id == source_document_id)).all()
    )
    answer_count = len(
        session.scalars(select(SelectedAnswer.id).where(SelectedAnswer.source_document_id == source_document_id)).all()
    )
    rule_ids = session.scalars(select(LegalRule.id).where(LegalRule.source_document_id == source_document_id)).all()
    component_count = (
        len(session.scalars(select(RuleComponent.id).where(RuleComponent.legal_rule_id.in_(rule_ids))).all())
        if rule_ids
        else 0
    )
    span_count = len(
        session.scalars(select(SourceSpan.id).where(SourceSpan.source_document_id == source_document_id)).all()
    )
    low_confidence_questions = len(
        session.scalars(
            select(EssayQuestion.id).where(
                EssayQuestion.source_document_id == source_document_id,
                EssayQuestion.parse_confidence < 0.8,
            )
        ).all()
    )
    low_confidence_rules = len(
        session.scalars(
            select(LegalRule.id).where(
                LegalRule.source_document_id == source_document_id,
                LegalRule.parse_confidence < 0.75,
            )
        ).all()
    )
    return {
        "source_document_id": document.id,
        "title": document.title,
        "ingestion_status": document.ingestion_status,
        "review_status": document.review_status,
        "page_count": document.page_count,
        "essay_questions": question_count,
        "selected_answers": answer_count,
        "legal_rules": len(rule_ids),
        "rule_components": component_count,
        "source_spans": span_count,
        "low_confidence_records": low_confidence_questions + low_confidence_rules,
    }

