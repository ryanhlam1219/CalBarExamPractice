from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class EssaySubmission(Base):
    __tablename__ = "essay_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    essay_question_id: Mapped[int] = mapped_column(
        ForeignKey("essay_questions.id", ondelete="CASCADE"), nullable=False
    )
    essay_text: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    essay_question: Mapped["EssayQuestion"] = relationship()  # noqa: F821
    analysis: Mapped[EssayAnalysis | None] = relationship(
        back_populates="essay_submission", uselist=False, cascade="all, delete-orphan"
    )


class EssayAnalysis(Base):
    __tablename__ = "essay_analyses"
    __table_args__ = (
        UniqueConstraint("essay_submission_id", name="uq_essay_analyses_submission"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    essay_submission_id: Mapped[int] = mapped_column(
        ForeignKey("essay_submissions.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("essay_templates.id", ondelete="SET NULL")
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    issue_spotting_score: Mapped[float] = mapped_column(Float, nullable=False)
    rule_statements_score: Mapped[float] = mapped_column(Float, nullable=False)
    fact_application_score: Mapped[float] = mapped_column(Float, nullable=False)
    organization_score: Mapped[float] = mapped_column(Float, nullable=False)
    feedback_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    essay_submission: Mapped[EssaySubmission] = relationship(back_populates="analysis")
