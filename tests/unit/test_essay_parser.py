from pathlib import Path

from app.parsing.essays.parser import EssayParser, PageSpan, _page_for_offset
from app.schemas.pdf import DocumentExtraction, PageExtraction


def _extraction(text: str) -> DocumentExtraction:
    return DocumentExtraction(
        source_path=Path("synthetic.pdf"),
        sha256="0" * 64,
        page_count=1,
        parser_version="test",
        pages=[
            PageExtraction(
                page_number=1,
                raw_text=text,
                normalized_text=text,
                extraction_method="synthetic",
                extraction_quality_score=1.0,
            )
        ],
    )


def test_parser_detects_multiple_questions_and_selected_answers() -> None:
    text = """
    GENERAL INSTRUCTIONS

    QUESTION 1
    Alice created a trust. The facts continue for enough words to clear the parser threshold.
    Discuss all rights and remedies. This paragraph adds enough legally relevant text for parsing.

    Essay Question No. 2
    Bob is a trustee. The facts continue for enough words to clear the parser threshold.
    Discuss all claims. This paragraph adds enough legally relevant text for parsing.

    Question 1
    Selected Answer A
    The answer analyzes duties and beneficiaries in detail with enough words for confidence.

    Selected Answer B
    The answer also analyzes the trust issues in detail with enough words for confidence.

    Question 2
    ANSWER A
    The answer analyzes trustee liability in detail with enough words for confidence.
    """
    result = EssayParser(min_question_chars=40).parse(_extraction(text))

    assert [question.question_number for question in result.questions] == [1, 2]
    assert [(answer.question_number, answer.answer_label) for answer in result.selected_answers] == [
        (1, "A"),
        (1, "B"),
        (2, "A"),
    ]
    assert not [issue for issue in result.issues if issue.code == "answer_text_in_question"]


def test_parser_reports_missing_questions() -> None:
    result = EssayParser(min_question_chars=40).parse(_extraction("Selected Answer A\nOnly an answer appears."))

    assert any(issue.code == "no_questions" for issue in result.issues)


def test_parser_ignores_question_headings_inside_selected_answers() -> None:
    text = """
    QUESTION 1
    Alice created a trust. The facts continue for enough words to clear the parser threshold.
    Discuss all rights and remedies. This paragraph adds enough legally relevant text for parsing.

    QUESTION 2
    Bob is a trustee. The facts continue for enough words to clear the parser threshold.
    Discuss all claims. This paragraph adds enough legally relevant text for parsing.

    Selected Answer A
    Question 1 -- Trust Formation
    The answer analyzes duties and beneficiaries in detail with enough words for confidence.

    Selected Answer B
    Question 1
    The answer also analyzes the trust issues in detail with enough words for confidence.
    """

    result = EssayParser(min_question_chars=40).parse(_extraction(text))

    assert [question.question_number for question in result.questions] == [1, 2]
    assert [answer.answer_label for answer in result.selected_answers] == ["A", "B"]
    assert not [issue for issue in result.issues if issue.code == "duplicate_question"]


def test_parser_detects_answer_to_question_heading_style() -> None:
    text = """
    QUESTION 1
    Alice created a trust. The facts continue for enough words to clear the parser threshold.
    Discuss all rights and remedies. This paragraph adds enough legally relevant text for parsing.

    ANSWER A TO QUESTION 1
    The answer analyzes duties and beneficiaries in detail with enough words for confidence.

    Answer B to Question 1
    The answer also analyzes the trust issues in detail with enough words for confidence.
    """

    result = EssayParser(min_question_chars=40).parse(_extraction(text))

    assert [question.question_number for question in result.questions] == [1]
    assert [(answer.question_number, answer.answer_label) for answer in result.selected_answers] == [
        (1, "A"),
        (1, "B"),
    ]


def test_parser_detects_question_answer_heading_style() -> None:
    text = """
    QUESTION 1
    Alice created a trust. The facts continue for enough words to clear the parser threshold.
    Discuss all rights and remedies. This paragraph adds enough legally relevant text for parsing.

    Question 1 Answer A
    The answer analyzes duties and beneficiaries in detail with enough words for confidence.
    """

    result = EssayParser(min_question_chars=40).parse(_extraction(text))

    assert [question.question_number for question in result.questions] == [1]
    assert [(answer.question_number, answer.answer_label) for answer in result.selected_answers] == [(1, "A")]


def test_page_for_offset_uses_previous_page_for_separator_gap() -> None:
    spans = [
        PageSpan(page_number=1, start=0, end=10),
        PageSpan(page_number=2, start=12, end=25),
    ]

    assert _page_for_offset(spans, 11) == 1
