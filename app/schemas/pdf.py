from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PageBlockExtraction(BaseModel):
    page_number: int
    block_index: int
    block_type: str
    text: str
    bbox: tuple[float, float, float, float] | None = None
    font_names: list[str] = Field(default_factory=list)
    font_sizes: list[float] = Field(default_factory=list)
    is_bold: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PageExtraction(BaseModel):
    page_number: int
    raw_text: str
    normalized_text: str
    extraction_method: str
    extraction_quality_score: float
    width: float | None = None
    height: float | None = None
    blocks: list[PageBlockExtraction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentExtraction(BaseModel):
    source_path: Path
    sha256: str
    page_count: int
    pages: list[PageExtraction]
    parser_version: str

