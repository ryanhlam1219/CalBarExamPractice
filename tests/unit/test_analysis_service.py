"""Tests for the mock analysis service."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.schemas.submissions import AnalysisResult
from app.services.analysis import MockAnalysisService, _build_user_prompt, _format_schimmel_template, get_analysis_service


def _make_question(text: str = "Discuss the elements of a valid trust."):
    q = MagicMock()
    q.id = 1
    q.question_number = 1
    q.normalized_text = text
    q.raw_text = text
    q.jurisdiction = "California"
    q.exam_year = 2017
    q.exam_month = "february"
    return q


def test_mock_returns_valid_result() -> None:
    service = MockAnalysisService()
    result = service.analyze("This is my essay about trusts.", _make_question(), None, [])
    assert isinstance(result, AnalysisResult)
    assert 0 <= result.scores.overall <= 100
    assert 0 <= result.scores.issue_spotting <= 35
    assert 0 <= result.scores.rule_statements <= 25
    assert 0 <= result.scores.fact_application <= 30
    assert 0 <= result.scores.organization <= 10
    assert result.model_id == "mock-v1"


def test_mock_deterministic() -> None:
    service = MockAnalysisService()
    q = _make_question()
    essay = "A trust requires a settlor, trustee, beneficiary, and trust property."
    r1 = service.analyze(essay, q, None, [])
    r2 = service.analyze(essay, q, None, [])
    assert r1.scores.overall == r2.scores.overall
    assert len(r1.issues) == len(r2.issues)


def test_longer_essay_scores_higher() -> None:
    service = MockAnalysisService()
    q = _make_question()
    short = "Trusts require elements."
    long = " ".join(["The settlor must have intent to create a trust."] * 30)
    r_short = service.analyze(short, q, None, [])
    r_long = service.analyze(long, q, None, [])
    assert r_long.scores.overall > r_short.scores.overall


def test_factory_returns_service() -> None:
    from app.services.analysis import OllamaAnalysisService
    service = get_analysis_service()
    assert isinstance(service, (MockAnalysisService, OllamaAnalysisService))


def test_issues_generated_without_template() -> None:
    service = MockAnalysisService()
    result = service.analyze("My essay text here.", _make_question(), None, [])
    assert len(result.issues) > 0
    assert all(i.issue_name for i in result.issues)


def test_prompt_uses_schimmel_template_as_controlling_structure() -> None:
    template = SimpleNamespace(
        id=1,
        name="Trusts Essay Template",
        nodes=[
            SimpleNamespace(
                id=10,
                parent_node_id=None,
                node_type="SUBJECT",
                title="TRUSTS",
                raw_text="TRUSTS",
                depth=0,
                display_order=0,
            ),
            SimpleNamespace(
                id=11,
                parent_node_id=10,
                node_type="ISSUE",
                title="Creation of a Trust",
                raw_text="Creation of a Trust",
                depth=1,
                display_order=0,
            ),
        ],
    )
    rule_candidate = SimpleNamespace(
        template_node_id=11,
        normalized_rule_text="Creation of a Trust\nA trust requires intent, trust property, and a beneficiary.",
        raw_rule_text="",
    )

    prompt = _build_user_prompt(
        "Settlor had intent and named a beneficiary.",
        _make_question(),
        template,
        [rule_candidate],
    )

    assert "## SCHIMMEL ESSAY TEMPLATE" in prompt
    assert "Controlling Issue Breakdown" in prompt
    assert "Creation of a Trust" in prompt
    assert "A trust requires intent, trust property, and a beneficiary." in prompt


def test_schimmel_prompt_keeps_short_element_labels() -> None:
    template = SimpleNamespace(
        id=1,
        name="Wills Essay Template",
        nodes=[
            SimpleNamespace(
                id=10,
                parent_node_id=None,
                node_type="SUBJECT",
                title="WILLS",
                raw_text="WILLS",
                depth=0,
                display_order=0,
            ),
            SimpleNamespace(
                id=11,
                parent_node_id=10,
                node_type="ISSUE",
                title="Holographic Will",
                raw_text="Holographic Will",
                depth=1,
                display_order=0,
            ),
            SimpleNamespace(
                id=12,
                parent_node_id=11,
                node_type="ELEMENT",
                title="Lack of Date",
                raw_text="Lack of Date\n•\nNot invalidated for lack of date",
                depth=2,
                display_order=0,
            ),
            SimpleNamespace(
                id=13,
                parent_node_id=11,
                node_type="ELEMENT",
                title="Capacity",
                raw_text="Capacity\n•\nLegal age\n•\nAdequate mental capacity",
                depth=2,
                display_order=1,
            ),
        ],
    )

    formatted = _format_schimmel_template(template, [])

    assert "Holographic Will" in formatted
    assert "Lack of Date" in formatted
    assert "Not invalidated for lack of date" in formatted
    assert "Capacity" in formatted
    assert "Adequate mental capacity" in formatted


def test_prompt_formats_supplemental_rules_readably() -> None:
    supplemental_rule = SimpleNamespace(
        canonical_name="Valid Trust",
        jurisdiction_scope="GENERAL",
        rule_status="GENERAL",
        rule_statement="A valid trust requires intent, trust property, a trustee, and a beneficiary.",
        legal_topic=SimpleNamespace(hierarchy_path="trusts/creation", name="Creation"),
        components=[
            SimpleNamespace(
                id=1,
                display_order=1,
                component_type="ELEMENT",
                label="Intent",
                content="The settlor must manifest present intent to create a trust.",
            ),
            SimpleNamespace(
                id=2,
                display_order=2,
                component_type="ELEMENT",
                label="Property",
                content="The trust must have identifiable property.",
            ),
        ],
    )

    prompt = _build_user_prompt(
        "The settlor transferred Blackacre to trustee for beneficiary.",
        _make_question(),
        None,
        [],
        [supplemental_rule],
    )

    assert "## SUPPLEMENTAL RULES" in prompt
    assert "Creation > Valid Trust" in prompt
    assert "A valid trust requires intent" in prompt


def test_prompt_includes_selected_answer_issue_outline() -> None:
    question = _make_question("What rights do Amy, Bob, and John have in Mary's estate?")
    question.selected_answers = [
        SimpleNamespace(
            answer_label="A",
            normalized_text=(
                "QUESTION 1: SELECTED ANSWER A\n\n"
                "Validity of Mary's First Will:\n\n"
                "The issue is whether the will was valid.\n\n"
                "Revocation of Mary's First Will:\n\n"
                "Ademption\n\n"
                "This paragraph is ordinary analysis and should not be treated as a heading."
            ),
            raw_text="",
        )
    ]

    prompt = _build_user_prompt(
        "Mary validly executed her first will but later revoked it.",
        question,
        None,
        [],
    )

    assert "## SELECTED-ANSWER PASSAGES" in prompt
    assert "Answer A" in prompt
    assert "Ademption" in prompt
