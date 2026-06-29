from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class EssayTemplate(Base):
    __tablename__ = "essay_templates"
    __table_args__ = (
        UniqueConstraint(
            "legal_subject_id", "source_document_id", "version", name="uq_essay_templates_subject_doc_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_subject_id: Mapped[int] = mapped_column(
        ForeignKey("legal_subjects.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    jurisdiction_scope: Mapped[str] = mapped_column(String(100), nullable=False, default="GENERAL")
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(100), nullable=False, default="1")
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    legal_subject: Mapped["LegalSubject"] = relationship()
    source_document: Mapped["SourceDocument"] = relationship()
    nodes: Mapped[list[TemplateNode]] = relationship(
        back_populates="essay_template", cascade="all, delete-orphan"
    )


class TemplateNode(Base):
    __tablename__ = "template_nodes"
    __table_args__ = (
        UniqueConstraint(
            "essay_template_id", "parent_node_id", "display_order", name="uq_template_nodes_order"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    essay_template_id: Mapped[int] = mapped_column(
        ForeignKey("essay_templates.id", ondelete="CASCADE"), nullable=False
    )
    parent_node_id: Mapped[int | None] = mapped_column(ForeignKey("template_nodes.id", ondelete="SET NULL"))
    canonical_issue_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_issue_candidates.id", ondelete="SET NULL"))
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jurisdiction_scope: Mapped[str | None] = mapped_column(String(100))
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    essay_template: Mapped[EssayTemplate] = relationship(back_populates="nodes")
    parent_node: Mapped[TemplateNode | None] = relationship(remote_side="TemplateNode.id")
    children: Mapped[list[TemplateNode]] = relationship(
        back_populates="parent_node",
        cascade="all, delete-orphan",
        foreign_keys=[parent_node_id],
    )
    rule_candidates: Mapped[list[TemplateRuleCandidate]] = relationship(
        back_populates="template_node", cascade="all, delete-orphan"
    )
    cross_references: Mapped[list[TemplateCrossReference]] = relationship(
        back_populates="source_template_node",
        cascade="all, delete-orphan",
        foreign_keys="TemplateCrossReference.source_template_node_id",
    )


class TemplateRuleCandidate(Base):
    __tablename__ = "template_rule_candidates"
    __table_args__ = (
        UniqueConstraint(
            "template_node_id", "rule_variant", "parser_version", name="uq_template_rule_candidates_node_variant"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_node_id: Mapped[int] = mapped_column(
        ForeignKey("template_nodes.id", ondelete="CASCADE"), nullable=False
    )
    legal_subject_id: Mapped[int] = mapped_column(
        ForeignKey("legal_subjects.id", ondelete="CASCADE"), nullable=False
    )
    canonical_issue_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_issue_candidates.id", ondelete="SET NULL"))
    raw_rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_rule_text: Mapped[str | None] = mapped_column(Text)
    jurisdiction_scope: Mapped[str] = mapped_column(String(100), nullable=False, default="GENERAL")
    rule_variant: Mapped[str | None] = mapped_column(String(64))
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    start_page: Mapped[int] = mapped_column(Integer, nullable=False)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    template_node: Mapped[TemplateNode] = relationship(back_populates="rule_candidates")


class TemplateCrossReference(Base):
    __tablename__ = "template_cross_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_template_node_id: Mapped[int] = mapped_column(
        ForeignKey("template_nodes.id", ondelete="CASCADE"), nullable=False
    )
    target_template_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("template_nodes.id", ondelete="SET NULL")
    )
    target_subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("legal_subjects.id", ondelete="SET NULL")
    )
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNRESOLVED")
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    source_template_node: Mapped[TemplateNode] = relationship(
        foreign_keys=[source_template_node_id],
        back_populates="cross_references",
    )
    target_template_node: Mapped[TemplateNode | None] = relationship(
        foreign_keys=[target_template_node_id],
    )


class DocumentAbbreviation(Base):
    __tablename__ = "document_abbreviations"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id", "abbreviation", "legal_subject_id", name="uq_doc_abbreviations_doc_abbr_subj"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    legal_subject_id: Mapped[int | None] = mapped_column(ForeignKey("legal_subjects.id", ondelete="SET NULL"))
    abbreviation: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_term: Mapped[str] = mapped_column(String(500), nullable=False)
    context_notes: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")


class CanonicalIssueCandidate(Base):
    __tablename__ = "canonical_issue_candidates"
    __table_args__ = (
        UniqueConstraint(
            "legal_subject_id", "proposed_name", name="uq_canonical_issue_candidates_subject_name"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_subject_id: Mapped[int] = mapped_column(
        ForeignKey("legal_subjects.id", ondelete="CASCADE"), nullable=False
    )
    parent_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_issue_candidates.id", ondelete="SET NULL")
    )
    source_template_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("template_nodes.id", ondelete="SET NULL")
    )
    proposed_name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    proposed_issue_type: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")