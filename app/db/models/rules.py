from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LegalSubject(Base):
    __tablename__ = "legal_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    topics: Mapped[list[LegalTopic]] = relationship(back_populates="legal_subject")


class LegalTopic(Base):
    __tablename__ = "legal_topics"
    __table_args__ = (
        UniqueConstraint("legal_subject_id", "hierarchy_path", name="uq_legal_topics_subject_path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_subject_id: Mapped[int] = mapped_column(ForeignKey("legal_subjects.id", ondelete="CASCADE"))
    parent_topic_id: Mapped[int | None] = mapped_column(ForeignKey("legal_topics.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    hierarchy_path: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    legal_subject: Mapped[LegalSubject] = relationship(back_populates="topics")
    parent_topic: Mapped[LegalTopic | None] = relationship(remote_side="LegalTopic.id")
    rules: Mapped[list[LegalRule]] = relationship(back_populates="legal_topic")


class LegalRule(Base):
    __tablename__ = "legal_rules"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "legal_topic_id",
            "canonical_name",
            "parser_version",
            name="uq_legal_rules_doc_topic_name_parser",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"))
    legal_subject_id: Mapped[int] = mapped_column(ForeignKey("legal_subjects.id", ondelete="CASCADE"))
    legal_topic_id: Mapped[int] = mapped_column(ForeignKey("legal_topics.id", ondelete="CASCADE"))
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    rule_statement: Mapped[str] = mapped_column(Text, nullable=False)
    short_rule_statement: Mapped[str | None] = mapped_column(Text)
    jurisdiction_scope: Mapped[str] = mapped_column(String(100), nullable=False, default="GENERAL")
    rule_status: Mapped[str] = mapped_column(String(64), nullable=False, default="GENERAL")
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")
    review_notes: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    legal_topic: Mapped[LegalTopic] = relationship(back_populates="rules")
    components: Mapped[list[RuleComponent]] = relationship(
        back_populates="legal_rule", cascade="all, delete-orphan"
    )


class RuleComponent(Base):
    __tablename__ = "rule_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_rule_id: Mapped[int] = mapped_column(ForeignKey("legal_rules.id", ondelete="CASCADE"))
    component_type: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    legal_rule: Mapped[LegalRule] = relationship(back_populates="components")

