from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.db.models.enums import DocumentCategory
from app.schemas.calbar import CalBarDiscoveryItem, DownloadManifestEntry
from app.services.files import append_jsonl, ensure_parent, read_jsonl, safe_filename, sha256_bytes

logger = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    pass


class ManifestStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, object]]:
        return read_jsonl(self.path)

    def latest_for_url(self, source_url: str) -> dict[str, object] | None:
        matches = [entry for entry in self.entries() if entry.get("source_url") == source_url]
        return matches[-1] if matches else None

    def append(self, entry: DownloadManifestEntry) -> None:
        append_jsonl(self.path, entry.model_dump(mode="json"))


class CalBarDownloader:
    def __init__(
        self,
        data_dir: Path | None = None,
        user_agent: str | None = None,
        timeout_seconds: float | None = None,
        rate_limit_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.data_dir = data_dir or settings.data_dir
        self.user_agent = user_agent or settings.user_agent
        self.timeout_seconds = timeout_seconds or settings.downloader_timeout_seconds
        self.rate_limit_seconds = (
            settings.downloader_rate_limit_seconds if rate_limit_seconds is None else rate_limit_seconds
        )
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def download(
        self,
        item: CalBarDiscoveryItem,
        manifest_store: ManifestStore | None = None,
        force: bool = False,
    ) -> DownloadManifestEntry:
        manifest_store = manifest_store or ManifestStore(self.data_dir / "manifests" / "calbar-downloads.jsonl")
        source_url = str(item.source_url)
        prior = manifest_store.latest_for_url(source_url)
        if not force and prior:
            prior_path = prior.get("local_path")
            prior_sha = prior.get("sha256")
            if isinstance(prior_path, str) and prior_sha and Path(prior_path).exists():
                prior_size = prior.get("file_size_bytes")
                file_size = int(prior_size) if isinstance(prior_size, int | str) else Path(prior_path).stat().st_size
                head = self._head_or_none(source_url)
                if _headers_match_prior(head, prior):
                    entry = DownloadManifestEntry(
                        source_url=source_url,
                        status="unchanged",
                        local_path=Path(prior_path),
                        sha256=str(prior_sha),
                        file_size_bytes=file_size,
                        content_type=str(prior.get("content_type") or "application/pdf"),
                        etag=str(prior.get("etag")) if prior.get("etag") else None,
                        last_modified=str(prior.get("last_modified")) if prior.get("last_modified") else None,
                        discovered_item=item,
                    )
                    manifest_store.append(entry)
                    return entry

        try:
            response = self._get(source_url)
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            content = response.content
            _validate_pdf_response(content, content_type)
            sha256 = sha256_bytes(content)
            if prior and prior.get("sha256") == sha256 and prior.get("local_path"):
                entry = DownloadManifestEntry(
                    source_url=source_url,
                    status="unchanged",
                    local_path=Path(str(prior["local_path"])),
                    sha256=sha256,
                    file_size_bytes=len(content),
                    content_type=content_type or "application/pdf",
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    discovered_item=item,
                )
                manifest_store.append(entry)
                return entry

            target_path = self._target_path(item, sha256 if prior and prior.get("sha256") != sha256 else None)
            self._write_atomic(target_path, content)
            status = "changed" if prior and prior.get("sha256") and prior.get("sha256") != sha256 else "downloaded"
            entry = DownloadManifestEntry(
                source_url=source_url,
                status=status,
                local_path=target_path,
                sha256=sha256,
                file_size_bytes=len(content),
                content_type=content_type or "application/pdf",
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                discovered_item=item,
            )
        except Exception as exc:  # noqa: BLE001 - failures are recorded without stopping the batch
            logger.exception("Failed to download %s", source_url)
            entry = DownloadManifestEntry(source_url=source_url, status="failed", error=str(exc), discovered_item=item)
        manifest_store.append(entry)
        if self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds)
        return entry

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get(self, url: str) -> httpx.Response:
        response = self.client.get(url)
        response.raise_for_status()
        return response

    def _head_or_none(self, url: str) -> httpx.Response | None:
        try:
            response = self.client.head(url)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        return response

    def _target_path(self, item: CalBarDiscoveryItem, sha_suffix: str | None = None) -> Path:
        parsed = urlparse(str(item.source_url))
        filename = safe_filename(Path(parsed.path).name or "calbar.pdf")
        if sha_suffix:
            filename = f"{Path(filename).stem}__{sha_suffix[:12]}{Path(filename).suffix or '.pdf'}"
        year = str(item.year or "unknown-year")
        month = item.month or "unknown-month"
        category = item.document_category.value.lower()
        return self.data_dir / "raw" / "calbar" / year / month / category / filename

    def _write_atomic(self, target_path: Path, content: bytes) -> None:
        ensure_parent(target_path)
        temp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.part")
        try:
            temp_path.write_bytes(content)
            temp_path.replace(target_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def _validate_pdf_response(content: bytes, content_type: str) -> None:
    if not content.startswith(b"%PDF"):
        raise DownloadError("response did not start with PDF magic bytes")
    if content_type and "pdf" not in content_type and content_type not in {"application/octet-stream", "binary/octet-stream"}:
        raise DownloadError(f"unexpected PDF content type: {content_type}")


def _headers_match_prior(response: httpx.Response | None, prior: dict[str, object]) -> bool:
    if response is None:
        return False
    etag = response.headers.get("etag")
    last_modified = response.headers.get("last-modified")
    content_length = response.headers.get("content-length")
    if etag and prior.get("etag") == etag:
        return True
    return bool(
        last_modified
        and prior.get("last_modified") == last_modified
        and (not content_length or str(prior.get("file_size_bytes")) == content_length)
    )


def filter_discovered_items(
    items: list[CalBarDiscoveryItem],
    year: int | None = None,
    month: str | None = None,
    category: DocumentCategory | None = None,
    limit: int | None = None,
) -> list[CalBarDiscoveryItem]:
    filtered = [
        item
        for item in items
        if (year is None or item.year == year)
        and (month is None or item.month == month.casefold())
        and (category is None or item.document_category == category)
    ]
    return filtered[:limit] if limit else filtered
