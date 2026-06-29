from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import EssayQuestion, EssaySubmission, SelectedAnswer, SourceDocument, SourceSpan
from app.db.models.enums import IngestionStatus
from app.db.repositories.documents import add_source_span, page_map
from app.schemas.essays import EssayParseResult


def replace_essay_parse(
    session: Session,
    source_document: SourceDocument,
    parse_result: EssayParseResult,
) -> dict[str, int]:
    session.execute(
        delete(SourceSpan).where(
            SourceSpan.source_document_id == source_document.id,
            SourceSpan.entity_type.in_(["essay_question", "selected_answer"]),
        )
    )
    session.execute(
        delete(SelectedAnswer).where(
            SelectedAnswer.source_document_id == source_document.id,
            SelectedAnswer.parser_version == parse_result.parser_version,
        )
    )
    session.execute(
        delete(EssayQuestion).where(
            EssayQuestion.source_document_id == source_document.id,
            EssayQuestion.parser_version == parse_result.parser_version,
        )
    )
    session.flush()

    pages = page_map(session, source_document.id)
    questions_by_number: dict[int, EssayQuestion] = {}
    for parsed in parse_result.questions:
        db_question = EssayQuestion(
            source_document_id=source_document.id,
            jurisdiction=source_document.jurisdiction or "California",
            exam_name="California Bar Examination",
            exam_year=source_document.exam_year,
            exam_month=source_document.exam_month,
            question_number=parsed.question_number,
            title=parsed.title,
            raw_text=parsed.raw_text,
            normalized_text=parsed.normalized_text,
            instructions_text=parsed.instructions_text,
            start_page=parsed.start_page,
            end_page=parsed.end_page,
            start_character_offset=parsed.start_character_offset,
            end_character_offset=parsed.end_character_offset,
            parse_confidence=parsed.parse_confidence,
            review_status=parsed.review_status,
            parser_version=parse_result.parser_version,
            metadata_json=parsed.metadata,
        )
        session.add(db_question)
        session.flush()
        questions_by_number[parsed.question_number] = db_question
        start_page = pages.get(parsed.start_page)
        add_source_span(
            session,
            source_document_id=source_document.id,
            document_page_id=start_page.id if start_page else None,
            entity_type="essay_question",
            entity_id=db_question.id,
            quoted_text=parsed.raw_text,
            extraction_method="pymupdf-text",
            start_offset=parsed.start_character_offset,
            end_offset=parsed.end_character_offset,
        )

    for parsed_answer in parse_result.selected_answers:
        question = questions_by_number.get(parsed_answer.question_number or -1)
        db_answer = SelectedAnswer(
            source_document_id=source_document.id,
            essay_question_id=question.id if question else None,
            answer_label=parsed_answer.answer_label,
            raw_text=parsed_answer.raw_text,
            normalized_text=parsed_answer.normalized_text,
            start_page=parsed_answer.start_page,
            end_page=parsed_answer.end_page,
            start_character_offset=parsed_answer.start_character_offset,
            end_character_offset=parsed_answer.end_character_offset,
            parse_confidence=parsed_answer.parse_confidence,
            review_status=parsed_answer.review_status,
            parser_version=parse_result.parser_version,
            metadata_json=parsed_answer.metadata | {"question_number": parsed_answer.question_number},
        )
        session.add(db_answer)
        session.flush()
        answer_start_page = pages.get(parsed_answer.start_page)
        add_source_span(
            session,
            source_document_id=source_document.id,
            document_page_id=answer_start_page.id if answer_start_page else None,
            entity_type="selected_answer",
            entity_id=db_answer.id,
            quoted_text=parsed_answer.raw_text,
            extraction_method="pymupdf-text",
            start_offset=parsed_answer.start_character_offset,
            end_offset=parsed_answer.end_character_offset,
        )

    source_document.ingestion_status = IngestionStatus.PARSED.value
    session.flush()
    return {"questions": len(parse_result.questions), "selected_answers": len(parse_result.selected_answers)}


def get_essay_parse_counts(session: Session, source_document_id: int) -> dict[str, int]:
    question_count = len(
        session.scalars(select(EssayQuestion.id).where(EssayQuestion.source_document_id == source_document_id)).all()
    )
    answer_count = len(
        session.scalars(select(SelectedAnswer.id).where(SelectedAnswer.source_document_id == source_document_id)).all()
    )
    return {"questions": question_count, "selected_answers": answer_count}


def dedupe_essay_questions(session: Session) -> dict[str, int]:
    """Remove duplicate prompt rows across source PDFs.

    The CalBar site often has both a question-only PDF and a selected-answer PDF
    for the same exam administration. Keep the row with submissions/answers first,
    then prefer selected-answer source documents. Rows with selected answers or user
    submissions are never deleted.
    """
    duplicate_groups = session.execute(
        select(
            EssayQuestion.exam_year,
            EssayQuestion.exam_month,
            EssayQuestion.question_number,
            func.count(EssayQuestion.id),
        )
        .group_by(EssayQuestion.exam_year, EssayQuestion.exam_month, EssayQuestion.question_number)
        .having(func.count(EssayQuestion.id) > 1)
    ).all()

    delete_ids: list[int] = []
    skipped = 0

    for year, month, question_number, _count in duplicate_groups:
        rows = session.execute(
            select(
                EssayQuestion.id,
                SourceDocument.document_category,
                func.count(func.distinct(SelectedAnswer.id)).label("answer_count"),
                func.count(func.distinct(EssaySubmission.id)).label("submission_count"),
                EssayQuestion.parse_confidence,
            )
            .join(SourceDocument, SourceDocument.id == EssayQuestion.source_document_id)
            .outerjoin(SelectedAnswer, SelectedAnswer.essay_question_id == EssayQuestion.id)
            .outerjoin(EssaySubmission, EssaySubmission.essay_question_id == EssayQuestion.id)
            .where(
                EssayQuestion.exam_year == year,
                EssayQuestion.exam_month == month,
                EssayQuestion.question_number == question_number,
            )
            .group_by(EssayQuestion.id, SourceDocument.document_category)
        ).all()

        def rank(row: tuple[int, str | None, int, int, float]) -> tuple[int, int, int, float, int]:
            question_id, category, answer_count, submission_count, confidence = row
            category_bonus = 1 if category == "ESSAY_QUESTIONS_AND_SELECTED_ANSWERS" else 0
            return (submission_count, answer_count, category_bonus, confidence or 0.0, -question_id)

        for row in sorted(rows, key=rank, reverse=True)[1:]:
            question_id, _category, answer_count, submission_count, _confidence = row
            if answer_count or submission_count:
                skipped += 1
                continue
            delete_ids.append(question_id)

    if delete_ids:
        session.execute(
            delete(SourceSpan).where(
                SourceSpan.entity_type == "essay_question",
                SourceSpan.entity_id.in_(delete_ids),
            )
        )
        session.execute(delete(EssayQuestion).where(EssayQuestion.id.in_(delete_ids)))
        session.flush()

    return {
        "duplicate_groups": len(duplicate_groups),
        "deleted_questions": len(delete_ids),
        "skipped_questions": skipped,
    }
