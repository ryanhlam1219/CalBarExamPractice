from pathlib import Path

import httpx

from app.db.models.enums import DocumentCategory
from app.ingestion.calbar.downloader import CalBarDownloader, ManifestStore
from app.schemas.calbar import CalBarDiscoveryItem


def _item() -> CalBarDiscoveryItem:
    return CalBarDiscoveryItem(
        year=2017,
        month="february",
        administration_label="February 2017",
        document_category=DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS,
        source_url="https://example.test/feb2017.pdf",
        link_text="February 2017 Essay Questions and Selected Answers",
        discovered_at="2026-06-26T00:00:00Z",
    )


def test_downloader_writes_pdf_atomically_and_records_manifest(tmp_path: Path) -> None:
    pdf = b"%PDF-1.7\nsynthetic\n%%EOF"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"etag": '"v1"', "content-length": str(len(pdf))})
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf", "etag": '"v1"'})

    downloader = CalBarDownloader(data_dir=tmp_path, rate_limit_seconds=0, transport=httpx.MockTransport(handler))
    manifest = ManifestStore(tmp_path / "manifests" / "downloads.jsonl")
    try:
        first = downloader.download(_item(), manifest_store=manifest)
        second = downloader.download(_item(), manifest_store=manifest)
    finally:
        downloader.close()

    assert first.status == "downloaded"
    assert first.local_path is not None
    assert first.local_path.exists()
    assert second.status == "unchanged"
    assert len(manifest.entries()) == 2


def test_downloader_records_non_pdf_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not a pdf", headers={"content-type": "text/plain"})

    downloader = CalBarDownloader(data_dir=tmp_path, rate_limit_seconds=0, transport=httpx.MockTransport(handler))
    manifest = ManifestStore(tmp_path / "manifest.jsonl")
    try:
        result = downloader.download(_item(), manifest_store=manifest)
    finally:
        downloader.close()

    assert result.status == "failed"
    assert "PDF magic" in (result.error or "")

def test_downloader_detects_same_url_changed_content(tmp_path: Path) -> None:
    versions = [b"%PDF-1.7\nold\n%%EOF", b"%PDF-1.7\nnew\n%%EOF"]
    calls = {"get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"etag": f'"v{calls["get"]}"'})
        content = versions[min(calls["get"], 1)]
        calls["get"] += 1
        return httpx.Response(200, content=content, headers={"content-type": "application/pdf"})

    downloader = CalBarDownloader(data_dir=tmp_path, rate_limit_seconds=0, transport=httpx.MockTransport(handler))
    manifest = ManifestStore(tmp_path / "manifest.jsonl")
    try:
        first = downloader.download(_item(), manifest_store=manifest)
        second = downloader.download(_item(), manifest_store=manifest)
    finally:
        downloader.close()

    assert first.status == "downloaded"
    assert second.status == "changed"
    assert second.local_path is not None
    assert "__" in second.local_path.name

