from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("source_url", "sha256", name="uq_source_documents_url_sha256"),
        Index("ix_source_documents_source_url", "source_url"),
        Index("ix_source_documents_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(100))
    subject: Mapped[str | None] = mapped_column(String(100))
    exam_year: Mapped[int | None] = mapped_column(Integer)
    exam_month: Mapped[str | None] = mapped_column(String(32))
    document_category: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), default="application/pdf", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    parser_version: Mapped[str | None] = mapped_column(String(100))
    ingestion_status: Mapped[str] = mapped_column(String(64), nullable=False, default="DOWNLOADED")
    review_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNREVIEWED")
    review_notes: Mapped[str | None] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN")
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    usage_notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="source_document", cascade="all, delete-orphan"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("source_document_id", "page_number", name="uq_document_pages_doc_page"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(100), nullable=False)
    extraction_quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    source_document: Mapped[SourceDocument] = relationship(back_populates="pages")
    blocks: Mapped[list[PageBlock]] = relationship(back_populates="document_page", cascade="all, delete-orphan")


class PageBlock(Base):
    __tablename__ = "page_blocks"
    __table_args__ = (
        UniqueConstraint("document_page_id", "block_index", name="uq_page_blocks_page_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_page_id: Mapped[int] = mapped_column(
        ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False
    )
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bbox_x0: Mapped[float | None] = mapped_column(Float)
    bbox_y0: Mapped[float | None] = mapped_column(Float)
    bbox_x1: Mapped[float | None] = mapped_column(Float)
    bbox_y1: Mapped[float | None] = mapped_column(Float)
    font_names: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    font_sizes: Mapped[list[float]] = mapped_column(JSON, default=list, nullable=False)
    is_bold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    document_page: Mapped[DocumentPage] = relationship(back_populates="blocks")


class SourceSpan(Base):
    __tablename__ = "source_spans"
    __table_args__ = (
        Index("ix_source_spans_entity", "entity_type", "entity_id"),
        Index("ix_source_spans_source_document", "source_document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    document_page_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_pages.id", ondelete="SET NULL")
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quoted_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    bbox_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    extraction_method: Mapped[str] = mapped_column(String(100), nullable=False)

