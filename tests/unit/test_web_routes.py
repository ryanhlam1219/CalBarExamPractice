"""Tests for the web application routes."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.essays import EssayQuestion, SelectedAnswer
from app.db.models.rules import LegalSubject
from app.db.models.source_documents import SourceDocument
from app.db.models.submissions import EssayAnalysis
from app.db.models.templates import EssayTemplate, TemplateNode
from app.db.repositories.submissions import create_submission
from app.db.session import get_session
from app.web.app import create_app


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture()
def client(db_session):
    application = create_app()

    def _override():
        yield db_session

    application.dependency_overrides[get_session] = _override

    # Override the background thread's session factory to use the same in-memory DB
    import app.web.routes.practice as practice_module
    from app.services.analysis import MockAnalysisService

    original_factory = practice_module._session_factory
    original_get_analysis_service = practice_module.get_analysis_service
    original_chat_about_analysis = practice_module.chat_about_analysis
    practice_module._session_factory = db_session.get_bind().connect  # won't work, need sessionmaker

    # Create a sessionmaker bound to the same engine for background threads
    from sqlalchemy.orm import sessionmaker
    test_session_factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    practice_module._session_factory = test_session_factory
    practice_module.get_analysis_service = lambda: MockAnalysisService()
    practice_module.chat_about_analysis = lambda submission, message, history=None: "Mock chat response"

    yield TestClient(application, raise_server_exceptions=True)

    practice_module._session_factory = original_factory
    practice_module.get_analysis_service = original_get_analysis_service
    practice_module.chat_about_analysis = original_chat_about_analysis


def _seed_question(
    session: Session,
    *,
    year: int = 2017,
    month: str = "february",
    question_number: int = 1,
    title: str = "Trusts Question",
) -> EssayQuestion:
    doc = SourceDocument(
        source_type="OFFICIAL_SELECTED_ANSWERS",
        publisher="State Bar of California",
        title=f"{month.title()} {year} Essays",
        original_filename="test.pdf",
        local_path="/tmp/test.pdf",
        sha256=f"{year}{month}{question_number}".ljust(64, "a")[:64],
        file_size_bytes=1000,
        ingestion_status="PARSED",
    )
    session.add(doc)
    session.flush()

    q = EssayQuestion(
        source_document_id=doc.id,
        jurisdiction="California",
        exam_name="California Bar Examination",
        exam_year=year,
        exam_month=month,
        question_number=question_number,
        title=title,
        raw_text="Discuss the elements of a valid trust.",
        normalized_text="Discuss the elements of a valid trust.",
        start_page=1,
        end_page=1,
        parse_confidence=0.9,
        parser_version="test",
    )
    session.add(q)
    session.flush()
    return q


def test_practice_home_empty(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "No essay questions loaded" in response.text


def test_practice_home_with_questions(client, db_session) -> None:
    _seed_question(db_session)
    response = client.get("/")
    assert response.status_code == 200
    assert "Question 1" in response.text
    assert "Start" in response.text
    assert "Random Question" in response.text


def test_practice_search_terms_include_matching_schimmel_template_nodes(client, db_session) -> None:
    q = _seed_question(
        db_session,
        title="Civil Procedure Question",
    )
    q.raw_text = "Plaintiff filed in federal court and defendant challenged venue."
    q.normalized_text = q.raw_text

    subject = LegalSubject(canonical_name="civil_procedure", display_name="Civil Procedure")
    db_session.add(subject)
    db_session.flush()
    template = EssayTemplate(
        legal_subject_id=subject.id,
        source_document_id=q.source_document_id,
        name="Civil Procedure Essay Template",
        version="1",
        parse_confidence=0.9,
        parser_version="test",
        metadata_json={"source": "schimmel_template_parser"},
    )
    db_session.add(template)
    db_session.flush()
    db_session.add(
        TemplateNode(
            essay_template_id=template.id,
            node_type="ELEMENT",
            title="Venue Transfer",
            display_order=1,
            depth=1,
            parse_confidence=0.9,
            parser_version="test",
        )
    )
    db_session.add(
        SelectedAnswer(
            source_document_id=q.source_document_id,
            essay_question_id=q.id,
            answer_label="A",
            raw_text="Venue Analysis\nA party may make a motion to transfer venue.",
            normalized_text="Venue Analysis\nA party may make a motion to transfer venue.",
            start_page=1,
            end_page=1,
            parse_confidence=0.9,
            parser_version="test",
        )
    )
    db_session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert 'data-search-terms="venue analysis||venue transfer"' in response.text


def test_exam_page(client, db_session) -> None:
    q = _seed_question(db_session)
    response = client.get(f"/exam/{q.id}")
    assert response.status_code == 200
    assert "valid trust" in response.text


def test_exam_page_not_found(client) -> None:
    response = client.get("/exam/999")
    assert response.status_code == 404


def test_processing_page_shows_analysis_context_panel(client, db_session) -> None:
    q = _seed_question(db_session)
    submission = create_submission(
        db_session,
        essay_question_id=q.id,
        essay_text="A trust requires intent, property, trustee, and beneficiary.",
    )
    db_session.commit()

    response = client.get(f"/results/{submission.id}")

    assert response.status_code == 200
    assert "Analysis Context" in response.text
    assert "Schimmel template" in response.text


def test_random_question_redirects_to_exam(client, db_session) -> None:
    q = _seed_question(db_session)
    response = client.get("/random", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/exam/{q.id}"


def test_random_question_respects_year_and_month_filters(client, db_session) -> None:
    _seed_question(db_session, year=2017, month="february", question_number=1)
    july_question = _seed_question(db_session, year=2018, month="july", question_number=2)

    response = client.get("/random?year=2018&month=july", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/exam/{july_question.id}"


def test_random_question_not_found(client) -> None:
    response = client.get("/random", follow_redirects=False)
    assert response.status_code == 404


def test_submit_and_results(client, db_session) -> None:
    import time

    q = _seed_question(db_session)
    response = client.post(
        f"/exam/{q.id}/submit",
        data={
            "essay_text": "A trust requires a settlor who has intent to create a trust.",
            "started_at": "",
            "time_spent_seconds": "120",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    results_url = response.headers["location"]
    assert "/results/" in results_url
    submission_id = results_url.split("/")[-1]

    # Wait for background analysis to complete
    for _ in range(50):
        status = client.get(f"/api/analysis-status/{submission_id}")
        if status.json().get("status") == "complete":
            break
        time.sleep(0.2)
    else:
        pytest.fail("Analysis did not complete within timeout")

    results_response = client.get(results_url)
    assert results_response.status_code == 200
    assert "/ 100" in results_response.text
    assert "Issue Spotting" in results_response.text
    assert "Essay Review" in results_response.text
    assert "Ask AI" in results_response.text

    complete_status = client.get(f"/api/analysis-status/{submission_id}")
    assert complete_status.json()["analysis_context"]["schimmel_template_used"] is False

    chat_response = client.post(
        f"/api/analysis-chat/{submission_id}",
        json={"message": "How can I improve?", "history": []},
    )
    assert chat_response.status_code == 200
    assert chat_response.json()["reply"] == "Mock chat response"


def test_saved_analysis_infers_schimmel_context_from_template_id(client, db_session) -> None:
    q = _seed_question(db_session)
    subject = LegalSubject(canonical_name="trusts", display_name="Trusts")
    db_session.add(subject)
    db_session.flush()
    template = EssayTemplate(
        legal_subject_id=subject.id,
        source_document_id=q.source_document_id,
        name="Trusts Essay Template",
        version="1",
        parse_confidence=0.9,
        parser_version="test",
        metadata_json={"source": "schimmel_template_parser"},
    )
    db_session.add(template)
    db_session.flush()
    submission = create_submission(
        db_session,
        essay_question_id=q.id,
        essay_text="The settlor manifested intent to create a trust.",
    )
    db_session.flush()
    analysis = EssayAnalysis(
        essay_submission_id=submission.id,
        template_id=template.id,
        overall_score=70,
        issue_spotting_score=24,
        rule_statements_score=18,
        fact_application_score=20,
        organization_score=8,
        feedback_json={
            "scores": {},
            "issues": [],
            "strengths": [],
            "areas_for_improvement": [],
            "overall_feedback": "",
        },
        model_id="test",
        metadata_json={},
    )
    db_session.add(analysis)
    db_session.commit()

    status = client.get(f"/api/analysis-status/{submission.id}")

    assert status.status_code == 200
    assert status.json()["analysis_context"]["schimmel_template_used"] is True
    assert status.json()["analysis_context"]["template_name"] == "Trusts Essay Template"


def test_results_not_found(client) -> None:
    response = client.get("/results/999")
    assert response.status_code == 404
