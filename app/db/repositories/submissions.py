from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models.submissions import EssayAnalysis, EssaySubmission
from app.schemas.submissions import AnalysisResult


def create_submission(
    session: Session,
    essay_question_id: int,
    essay_text: str,
    started_at: datetime | None = None,
    time_spent_seconds: int | None = None,
) -> EssaySubmission:
    submission = EssaySubmission(
        essay_question_id=essay_question_id,
        essay_text=essay_text,
        started_at=started_at,
        time_spent_seconds=time_spent_seconds,
    )
    session.add(submission)
    session.flush()
    return submission


def get_submission(session: Session, submission_id: int) -> EssaySubmission | None:
    return session.get(EssaySubmission, submission_id)


def save_analysis(
    session: Session,
    submission_id: int,
    result: AnalysisResult,
    metadata: dict | None = None,
) -> EssayAnalysis:
    analysis = EssayAnalysis(
        essay_submission_id=submission_id,
        template_id=result.template_id,
        overall_score=result.scores.overall,
        issue_spotting_score=result.scores.issue_spotting,
        rule_statements_score=result.scores.rule_statements,
        fact_application_score=result.scores.fact_application,
        organization_score=result.scores.organization,
        feedback_json=result.model_dump(mode="json"),
        model_id=result.model_id,
        metadata_json=metadata or {},
    )
    session.add(analysis)
    session.flush()
    return analysis


def get_analysis(session: Session, submission_id: int) -> EssayAnalysis | None:
    submission = session.get(EssaySubmission, submission_id)
    if submission is None:
        return None
    return submission.analysis
