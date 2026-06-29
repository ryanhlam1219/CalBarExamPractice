from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from app.config import get_settings
from app.parsing.text import extraction_quality_score, normalize_extracted_text
from app.schemas.pdf import DocumentExtraction, PageBlockExtraction, PageExtraction
from app.services.files import sha256_file, write_json


class PDFExtractionError(RuntimeError):
    pass


class PDFExtractor:
    def __init__(self, parser_version: str | None = None) -> None:
        self.parser_version = parser_version or get_settings().parser_version

    def extract(self, pdf_path: Path) -> DocumentExtraction:
        if not pdf_path.exists():
            raise PDFExtractionError(f"PDF does not exist: {pdf_path}")
        pages: list[PageExtraction] = []
        try:
            document = fitz.open(pdf_path)
        except Exception as exc:  # noqa: BLE001
            raise PDFExtractionError(f"Could not open PDF {pdf_path}: {exc}") from exc

        with document:
            for page_index, page in enumerate(document, start=1):
                raw_text = page.get_text("text", sort=True)
                normalized = normalize_extracted_text(raw_text)
                rect = page.rect
                blocks = _extract_blocks(page, page_index)
                pages.append(
                    PageExtraction(
                        page_number=page_index,
                        raw_text=raw_text,
                        normalized_text=normalized,
                        extraction_method="pymupdf-text",
                        extraction_quality_score=extraction_quality_score(
                            raw_text, page_area=float(rect.width * rect.height)
                        ),
                        width=float(rect.width),
                        height=float(rect.height),
                        blocks=blocks,
                        metadata={"rotation": page.rotation},
                    )
                )

        return DocumentExtraction(
            source_path=pdf_path,
            sha256=sha256_file(pdf_path),
            page_count=len(pages),
            pages=pages,
            parser_version=self.parser_version,
        )

    def extract_to_json(self, pdf_path: Path, output_path: Path | None = None) -> DocumentExtraction:
        extraction = self.extract(pdf_path)
        target = output_path or get_settings().extracted_dir / f"{pdf_path.stem}.pages.json"
        write_json(target, extraction.model_dump(mode="json"))
        return extraction


def _extract_blocks(page: fitz.Page, page_number: int) -> list[PageBlockExtraction]:
    raw_dict: dict[str, Any] = page.get_text("dict", sort=True)
    extracted: list[PageBlockExtraction] = []
    for block_index, block in enumerate(raw_dict.get("blocks", [])):
        lines = block.get("lines", [])
        text_parts: list[str] = []
        font_names: set[str] = set()
        font_sizes: set[float] = set()
        is_bold = False
        for line in lines:
            line_parts: list[str] = []
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                line_parts.append(span_text)
                font = str(span.get("font", ""))
                if font:
                    font_names.add(font)
                size = span.get("size")
                if size is not None:
                    font_sizes.add(round(float(size), 2))
                flags = int(span.get("flags", 0) or 0)
                is_bold = is_bold or "bold" in font.casefold() or bool(flags & 16)
            if "".join(line_parts).strip():
                text_parts.append("".join(line_parts))
        text = normalize_extracted_text("\n".join(text_parts))
        if not text:
            continue
        bbox = block.get("bbox")
        extracted.append(
            PageBlockExtraction(
                page_number=page_number,
                block_index=len(extracted),
                block_type=str(block.get("type", "text")),
                text=text,
                bbox=tuple(float(v) for v in bbox) if bbox else None,
                font_names=sorted(font_names),
                font_sizes=sorted(font_sizes),
                is_bold=is_bold,
                metadata={"original_block_index": block_index},
            )
        )
    return extracted

