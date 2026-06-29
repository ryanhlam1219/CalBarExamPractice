from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.enums import LicenseStatus, SourceType
from app.db.repositories.documents import register_source_document, replace_document_pages
from app.db.repositories.essays import replace_essay_parse
from app.db.repositories.rules import replace_rule_parse
from app.schemas.essays import EssayParseResult, ParsedEssayQuestion, ParsedSelectedAnswer
from app.schemas.pdf import DocumentExtraction, PageExtraction
from app.schemas.rules import ParsedRule, ParsedRuleComponent, RuleParseResult
from app.services.html_export import export_data_browser_html, export_document_review_html


def test_html_exports_render_questions_answers_rules_and_spans(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.7\nsynthetic\n%%EOF")

    extraction = DocumentExtraction(
        source_path=pdf,
        sha256="3" * 64,
        page_count=1,
        parser_version="test",
        pages=[
            PageExtraction(
                page_number=1,
                raw_text=(
                    "QUESTION 1\n\nTrust prompt facts.\n\n"
                    "Bob called Ann and arranged to meet. When they met, the following conversation ensued:\n\n"
                    "Bob: Hello.\n\nDid the court properly admit:\n\n"
                    "1. The trust document? Discuss.\n\n2. The trustee's statement? Discuss.\n\n"
                    "Answer according to California law."
                ),
                normalized_text=(
                    "QUESTION 1\n\nTrust prompt facts.\n\n"
                    "Bob called Ann and arranged to meet. When they met, the following conversation ensued:\n\n"
                    "Bob: Hello.\n\nDid the court properly admit:\n\n"
                    "1. The trust document? Discuss.\n\n2. The trustee's statement? Discuss.\n\n"
                    "Answer according to California law."
                ),
                extraction_method="synthetic",
                extraction_quality_score=1.0,
            )
        ],
    )
    essay_result = EssayParseResult(
        parser_version="test",
        questions=[
            ParsedEssayQuestion(
                question_number=1,
                raw_text=(
                    "QUESTION 1\n\nTrust prompt facts.\n\n"
                    "Bob called Ann and arranged to meet. When they met, the following conversation ensued:\n\n"
                    "Bob: Hello.\n\nDid the court properly admit:\n\n"
                    "1. The trust document? Discuss.\n\n2. The trustee's statement? Discuss.\n\n"
                    "Answer according to California law."
                ),
                normalized_text=(
                    "QUESTION 1\n\nTrust prompt facts.\n\n"
                    "Bob called Ann and arranged to meet. When they met, the following conversation ensued:\n\n"
                    "Bob: Hello.\n\nDid the court properly admit:\n\n"
                    "1. The trust document? Discuss.\n\n2. The trustee's statement? Discuss.\n\n"
                    "Answer according to California law."
                ),
                start_page=1,
                end_page=1,
                parse_confidence=0.95,
                review_status="AUTO_ACCEPTED",
            )
        ],
        selected_answers=[
            ParsedSelectedAnswer(
                question_number=1,
                answer_label="A",
                raw_text="Selected answer text",
                normalized_text="Selected answer text",
                start_page=1,
                end_page=1,
                parse_confidence=0.9,
                review_status="AUTO_ACCEPTED",
            )
        ],
    )
    rule_result = RuleParseResult(
        subject_canonical_name="trusts",
        subject_display_name="Trusts",
        subject_source_page=1,
        subject_source_text="TRUSTS",
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
            source_type=SourceType.OFFICIAL_SELECTED_ANSWERS.value,
            publisher="State Bar of California",
            title="Synthetic February 2017",
            license_status=LicenseStatus.OFFICIAL_PUBLIC.value,
        )
        replace_document_pages(session, document, extraction)
        replace_essay_parse(session, document, essay_result)
        replace_rule_parse(session, document, rule_result)
        session.commit()

        review_path = tmp_path / "review.html"
        browser_path = tmp_path / "browser.html"
        export_document_review_html(session, document.id, review_path)
        export_data_browser_html(session, browser_path)

    review_html = review_path.read_text(encoding="utf-8")
    browser_html = browser_path.read_text(encoding="utf-8")
    assert "Synthetic February 2017" in review_html
    assert "Exam Prompt" in review_html
    assert "class='call-intro'" not in review_html
    assert "<p class='call-item'>Did the court properly admit:</p>" in review_html
    assert "<li>2.</li>" not in review_html
    assert "<ol>" not in review_html
    assert "<p class='call-item'>2. The trustee's statement? Discuss.</p>" in review_html
    assert "Call of the Question" not in review_html
    assert "Instructions" not in review_html
    assert "Facts</h4>" not in review_html
    assert "Answer according to California law." in review_html
    assert "Trust prompt facts." in review_html
    assert "<pre>QUESTION 1" not in review_html
    assert "Selected Answer A" in review_html
    assert "Source page 1" in review_html
    assert "Valid trust" in browser_html
    assert "A valid trust requires intent and property." in browser_html
