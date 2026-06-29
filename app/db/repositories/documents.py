from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import DocumentPage, PageBlock, SourceDocument, SourceSpan
from app.db.models.enums import IngestionStatus, ReviewStatus
from app.schemas.pdf import DocumentExtraction
from app.services.files import sha256_file


def register_source_document(
    session: Session,
    *,
    local_path: Path,
    source_type: str,
    publisher: str,
    title: str,
    original_filename: str | None = None,
    source_url: str | None = None,
    jurisdiction: str | None = None,
    subject: str | None = None,
    exam_year: int | None = None,
    exam_month: str | None = None,
    document_category: str | None = None,
    mime_type: str = "application/pdf",
    parser_version: str | None = None,
    page_count: int | None = None,
    license_status: str = "UNKNOWN",
    redistribution_allowed: bool = False,
    usage_notes: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> SourceDocument:
    local_path = local_path.resolve()
    sha256 = sha256_file(local_path)
    file_size = local_path.stat().st_size
    existing = _find_existing_source_document(session, source_url, str(local_path), sha256)
    payload = {
        "source_type": source_type,
        "publisher": publisher,
        "title": title,
        "jurisdiction": jurisdiction,
        "subject": subject,
        "exam_year": exam_year,
        "exam_month": exam_month,
        "document_category": document_category,
        "source_url": source_url,
        "original_filename": original_filename or local_path.name,
        "local_path": str(local_path),
        "mime_type": mime_type,
        "sha256": sha256,
        "file_size_bytes": file_size,
        "page_count": page_count,
        "parser_version": parser_version,
        "ingestion_status": IngestionStatus.DOWNLOADED.value,
        "license_status": license_status,
        "redistribution_allowed": redistribution_allowed,
        "usage_notes": usage_notes,
        "metadata_json": metadata_json or {},
    }
    if existing:
        approved = existing.review_status == ReviewStatus.APPROVED.value
        for key, value in payload.items():
            if approved and key in {"review_status", "metadata_json"}:
                continue
            setattr(existing, key, value)
        session.flush()
        return existing
    document = SourceDocument(**payload, review_status=ReviewStatus.UNREVIEWED.value)
    session.add(document)
    session.flush()
    return document


def replace_document_pages(
    session: Session,
    source_document: SourceDocument,
    extraction: DocumentExtraction,
) -> dict[int, DocumentPage]:
    page_ids = [
        page_id
        for page_id in session.scalars(
            select(DocumentPage.id).where(DocumentPage.source_document_id == source_document.id)
        ).all()
    ]
    if page_ids:
        session.execute(delete(PageBlock).where(PageBlock.document_page_id.in_(page_ids)))
        session.execute(delete(DocumentPage).where(DocumentPage.id.in_(page_ids)))
        session.flush()

    page_by_number: dict[int, DocumentPage] = {}
    for page in extraction.pages:
        db_page = DocumentPage(
            source_document_id=source_document.id,
            page_number=page.page_number,
            raw_text=page.raw_text,
            normalized_text=page.normalized_text,
            extraction_method=page.extraction_method,
            extraction_quality_score=page.extraction_quality_score,
            width=page.width,
            height=page.height,
            metadata_json=page.metadata,
        )
        session.add(db_page)
        session.flush()
        for block in page.blocks:
            bbox = block.bbox or (None, None, None, None)
            session.add(
                PageBlock(
                    document_page_id=db_page.id,
                    block_index=block.block_index,
                    block_type=block.block_type,
                    text=block.text,
                    bbox_x0=bbox[0],
                    bbox_y0=bbox[1],
                    bbox_x1=bbox[2],
                    bbox_y1=bbox[3],
                    font_names=block.font_names,
                    font_sizes=block.font_sizes,
                    is_bold=block.is_bold,
                    metadata_json=block.metadata,
                )
            )
        page_by_number[page.page_number] = db_page

    source_document.page_count = extraction.page_count
    source_document.ingestion_status = IngestionStatus.EXTRACTED.value
    source_document.parser_version = extraction.parser_version
    session.flush()
    return page_by_number


def add_source_span(
    session: Session,
    *,
    source_document_id: int,
    document_page_id: int | None,
    entity_type: str,
    entity_id: int,
    quoted_text: str,
    extraction_method: str,
    start_offset: int | None = None,
    end_offset: int | None = None,
    bbox_json: dict[str, Any] | None = None,
) -> SourceSpan:
    span = SourceSpan(
        source_document_id=source_document_id,
        document_page_id=document_page_id,
        entity_type=entity_type,
        entity_id=entity_id,
        quoted_text=quoted_text,
        start_offset=start_offset,
        end_offset=end_offset,
        bbox_json=bbox_json,
        extraction_method=extraction_method,
    )
    session.add(span)
    session.flush()
    return span


def page_map(session: Session, source_document_id: int) -> dict[int, DocumentPage]:
    pages = session.scalars(
        select(DocumentPage).where(DocumentPage.source_document_id == source_document_id)
    ).all()
    return {page.page_number: page for page in pages}


def _find_existing_source_document(
    session: Session,
    source_url: str | None,
    local_path: str,
    sha256: str,
) -> SourceDocument | None:
    if source_url:
        existing = session.scalar(
            select(SourceDocument).where(SourceDocument.source_url == source_url, SourceDocument.sha256 == sha256)
        )
        if existing:
            return existing
    return session.scalar(
        select(SourceDocument).where(SourceDocument.local_path == local_path, SourceDocument.sha256 == sha256)
    )

