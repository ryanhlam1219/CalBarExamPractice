from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, HttpUrl

from app.db.models.enums import DocumentCategory


class CalBarDiscoveryItem(BaseModel):
    jurisdiction: str = "California"
    exam_name: str = "California Bar Examination"
    year: int | None = None
    month: str | None = None
    administration_label: str | None = None
    document_category: DocumentCategory
    source_url: HttpUrl | str
    link_text: str
    context_heading: str | None = None
    discovered_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)


class DownloadManifestEntry(BaseModel):
    source_url: str
    status: str
    local_path: Path | None = None
    sha256: str | None = None
    file_size_bytes: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    discovered_item: CalBarDiscoveryItem | None = None

