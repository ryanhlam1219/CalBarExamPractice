from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EssayQuestion(Base):
    __tablename__ = "essay_questions"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id", "question_number", "parser_version", name="uq_essay_questions_doc_num_parser"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    jurisdiction: Mapped[str] = mapped_column(String(100), nullable=False)
    exam_name: Mapped[str] = mapped_column(String(255), nullable=False)
    exam_year: Mapped[int | None] = mapped_column(Integer)
    exam_month: Mapped[str | None] = mapped_column(String(32))
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    instructions_text: Mapped[str | None] = mapped_column(Text)
    start_page: Mapped[int] = mapped_column(Integer, nullable=False)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False)
    start_character_offset: Mapped[int | None] = mapped_column(Integer)
    end_character_offset: Mapped[int | None] = mapped_column(Integer)
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")
    review_notes: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    selected_answers: Mapped[list[SelectedAnswer]] = relationship(back_populates="essay_question")


class SelectedAnswer(Base):
    __tablename__ = "selected_answers"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "essay_question_id",
            "answer_label",
            "parser_version",
            name="uq_selected_answers_doc_question_label_parser",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    essay_question_id: Mapped[int | None] = mapped_column(ForeignKey("essay_questions.id", ondelete="SET NULL"))
    answer_label: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_page: Mapped[int] = mapped_column(Integer, nullable=False)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False)
    start_character_offset: Mapped[int | None] = mapped_column(Integer)
    end_character_offset: Mapped[int | None] = mapped_column(Integer)
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")
    review_notes: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    essay_question: Mapped[EssayQuestion | None] = relationship(back_populates="selected_answers")

