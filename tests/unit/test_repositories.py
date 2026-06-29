from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import EssayQuestion, LegalRule, RuleComponent, SelectedAnswer, SourceDocument, SourceSpan
from app.db.models.enums import LicenseStatus, SourceType
from app.db.repositories.documents import register_source_document, replace_document_pages
from app.db.repositories.essays import dedupe_essay_questions
from app.db.repositories.rules import replace_rule_parse
from app.schemas.pdf import DocumentExtraction, PageExtraction
from app.schemas.rules import ParsedRule, ParsedRuleComponent, RuleParseResult


def test_rule_repository_creates_source_spans(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    pdf = tmp_path / "trusts.pdf"
    pdf.write_bytes(b"%PDF-1.7\nsynthetic\n%%EOF")

    extraction = DocumentExtraction(
        source_path=pdf,
        sha256="2" * 64,
        page_count=1,
        parser_version="test",
        pages=[
            PageExtraction(
                page_number=1,
                raw_text="Trust rule text",
                normalized_text="Trust rule text",
                extraction_method="synthetic",
                extraction_quality_score=1.0,
            )
        ],
    )
    result = RuleParseResult(
        source_document_id=None,
        subject_canonical_name="trusts",
        subject_display_name="Trusts",
        topics=[["Trusts", "Creation"]],
        parser_version="test",
        rules=[
            ParsedRule(
                topic_path=["Trusts", "Creation"],
                canonical_name="Valid trust",
                rule_statement="A valid trust requires intent and property.",
                parse_confidence=0.9,
                review_status="AUTO_ACCEPTED",
                start_page=1,
                end_page=1,
                source_text="A valid trust requires intent and property.",
                components=[
                    ParsedRuleComponent(
                        component_type="ELEMENT",
                        content="intent",
                        display_order=1,
                        source_page=1,
                        source_text="- intent",
                    )
                ],
            )
        ],
    )

    with Session(engine) as session:
        document = register_source_document(
            session,
            local_path=pdf,
            source_type=SourceType.BAR_REVIEW_OUTLINE.value,
            publisher="Local",
            title="Trusts",
            license_status=LicenseStatus.PRIVATE_USE_ONLY.value,
        )
        replace_document_pages(session, document, extraction)
        counts = replace_rule_parse(session, document, result)
        session.commit()

        assert counts == {"topics": 2, "rules": 1, "rule_components": 1}
        assert len(session.scalars(select(LegalRule)).all()) == 1
        assert len(session.scalars(select(RuleComponent)).all()) == 1
        assert len(session.scalars(select(SourceSpan)).all()) == 2


def test_dedupe_essay_questions_keeps_answered_question() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        answered_doc = SourceDocument(
            source_type="OFFICIAL_SELECTED_ANSWERS",
            publisher="State Bar of California",
            title="Answered",
            original_filename="answered.pdf",
            local_path="/tmp/answered.pdf",
            sha256="a" * 64,
            file_size_bytes=1,
            ingestion_status="PARSED",
            document_category="ESSAY_QUESTIONS_AND_SELECTED_ANSWERS",
            exam_year=2012,
            exam_month="february",
        )
        duplicate_doc = SourceDocument(
            source_type="OFFICIAL_EXAM",
            publisher="State Bar of California",
            title="Question Only",
            original_filename="question.pdf",
            local_path="/tmp/question.pdf",
            sha256="b" * 64,
            file_size_bytes=1,
            ingestion_status="PARSED",
            document_category="EXAM_QUESTIONS",
            exam_year=2012,
            exam_month="february",
        )
        session.add_all([answered_doc, duplicate_doc])
        session.flush()

        keep = EssayQuestion(
            source_document_id=answered_doc.id,
            jurisdiction="California",
            exam_name="California Bar Examination",
            exam_year=2012,
            exam_month="february",
            question_number=1,
            raw_text="Question 1 answered",
            normalized_text="Question 1 answered",
            start_page=1,
            end_page=1,
            parse_confidence=0.9,
            review_status="AUTO_ACCEPTED",
            parser_version="test",
        )
        delete_me = EssayQuestion(
            source_document_id=duplicate_doc.id,
            jurisdiction="California",
            exam_name="California Bar Examination",
            exam_year=2012,
            exam_month="february",
            question_number=1,
            raw_text="Question 1 duplicate",
            normalized_text="Question 1 duplicate",
            start_page=1,
            end_page=1,
            parse_confidence=0.9,
            review_status="AUTO_ACCEPTED",
            parser_version="test",
        )
        session.add_all([keep, delete_me])
        session.flush()

        session.add(
            SelectedAnswer(
                source_document_id=answered_doc.id,
                essay_question_id=keep.id,
                answer_label="A",
                raw_text="Answer A",
                normalized_text="Answer A",
                start_page=2,
                end_page=2,
                parse_confidence=0.9,
                review_status="AUTO_ACCEPTED",
                parser_version="test",
            )
        )
        session.add(
            SourceSpan(
                source_document_id=duplicate_doc.id,
                entity_type="essay_question",
                entity_id=delete_me.id,
                quoted_text="Question 1 duplicate",
                extraction_method="test",
            )
        )
        session.flush()

        counts = dedupe_essay_questions(session)

        remaining = session.scalars(select(EssayQuestion)).all()
        spans = session.scalars(select(SourceSpan)).all()
        assert counts == {"duplicate_groups": 1, "deleted_questions": 1, "skipped_questions": 0}
        assert [question.id for question in remaining] == [keep.id]
        assert spans == []
