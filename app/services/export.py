from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentPage,
    EssayQuestion,
    LegalRule,
    RuleComponent,
    SelectedAnswer,
    SourceDocument,
    SourceSpan,
)
from app.services.files import write_json


def build_document_review_payload(session: Session, source_document_id: int) -> dict[str, object]:
    document = session.get(SourceDocument, source_document_id)
    if document is None:
        raise ValueError(f"Source document {source_document_id} was not found")
    pages = session.scalars(select(DocumentPage).where(DocumentPage.source_document_id == source_document_id)).all()
    questions = session.scalars(
        select(EssayQuestion).where(EssayQuestion.source_document_id == source_document_id)
    ).all()
    answers = session.scalars(
        select(SelectedAnswer).where(SelectedAnswer.source_document_id == source_document_id)
    ).all()
    rules = session.scalars(select(LegalRule).where(LegalRule.source_document_id == source_document_id)).all()
    components_by_rule: dict[int, list[RuleComponent]] = {}
    if rules:
        components = session.scalars(
            select(RuleComponent).where(RuleComponent.legal_rule_id.in_([rule.id for rule in rules]))
        ).all()
        for component in components:
            components_by_rule.setdefault(component.legal_rule_id, []).append(component)
    spans = session.scalars(select(SourceSpan).where(SourceSpan.source_document_id == source_document_id)).all()

    payload: dict[str, object] = {
        "source_document": _model_dict(document),
        "pages": [_model_dict(page) for page in pages],
        "essay_questions": [_model_dict(question) for question in questions],
        "selected_answers": [_model_dict(answer) for answer in answers],
        "legal_rules": [
            _model_dict(rule) | {"components": [_model_dict(item) for item in components_by_rule.get(rule.id, [])]}
            for rule in rules
        ],
        "source_spans": [_model_dict(span) for span in spans],
    }
    return payload


def export_document_review(session: Session, source_document_id: int, output_path: Path) -> dict[str, object]:
    payload = build_document_review_payload(session, source_document_id)
    write_json(output_path, payload)
    return payload


def _model_dict(model: object) -> dict[str, object]:
    return {
        column.name: getattr(model, column.name)
        for column in model.__table__.columns  # type: ignore[attr-defined]
    }
