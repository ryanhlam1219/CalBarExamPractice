from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import get_settings
from app.db.models.enums import DocumentCategory
from app.parsing.text import collapse_inline_whitespace
from app.schemas.calbar import CalBarDiscoveryItem

logger = logging.getLogger(__name__)

MONTH_ALIASES = {
    "jan": "january",
    "january": "january",
    "feb": "february",
    "february": "february",
    "jul": "july",
    "july": "july",
    "oct": "october",
    "october": "october",
}

DEFAULT_ESSAY_CATEGORIES = {
    DocumentCategory.EXAM_QUESTIONS,
    DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS,
}


class CalBarDiscoveryError(RuntimeError):
    pass


class CalBarCrawler:
    def __init__(self, user_agent: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.user_agent = user_agent or get_settings().user_agent
        self.timeout_seconds = timeout_seconds

    def fetch_html(self, url: str) -> str:
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout_seconds,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CalBarDiscoveryError(f"Could not fetch CalBar past-exams page: {exc}") from exc
        return response.text

    def discover(
        self,
        url: str | None = None,
        include_categories: set[DocumentCategory] | None = None,
        html: str | None = None,
    ) -> list[CalBarDiscoveryItem]:
        source_url = url or get_settings().past_exams_url
        soup = BeautifulSoup(html if html is not None else self.fetch_html(source_url), "html.parser")
        include_categories = include_categories or DEFAULT_ESSAY_CATEGORIES
        discovered_at = datetime.now(UTC)
        items: list[CalBarDiscoveryItem] = []
        seen: set[tuple[str, DocumentCategory]] = set()

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", ""))
            if not _is_pdf_href(href):
                continue
            absolute_url = urljoin(source_url, href)
            link_text = collapse_inline_whitespace(anchor.get_text(" ", strip=True)) or _filename_from_url(
                absolute_url
            )
            heading = _nearest_heading(anchor)
            context = " ".join(part for part in [heading, _nearby_text(anchor), link_text, absolute_url] if part)
            category = classify_calbar_link(link_text=link_text, href=absolute_url, context=context)
            if category == DocumentCategory.UNKNOWN:
                logger.warning("Ambiguous CalBar PDF link: %s [%s]", link_text, absolute_url)
            if category not in include_categories:
                continue
            key = (absolute_url, category)
            if key in seen:
                continue
            seen.add(key)
            year, month = extract_administration_metadata(" ".join([link_text, absolute_url, context]))
            administration_label = f"{month.title()} {year}" if year and month else None
            items.append(
                CalBarDiscoveryItem(
                    year=year,
                    month=month,
                    administration_label=administration_label,
                    document_category=category,
                    source_url=absolute_url,
                    link_text=link_text,
                    context_heading=heading,
                    discovered_at=discovered_at,
                    metadata={"url_path": urlparse(absolute_url).path},
                )
            )

        return sorted(
            items,
            key=lambda item: (
                item.year or 0,
                item.month or "",
                item.document_category.value,
                str(item.source_url),
            ),
            reverse=True,
        )


def classify_calbar_link(link_text: str, href: str, context: str = "") -> DocumentCategory:
    haystack = " ".join([link_text, href, context]).casefold()
    if "first-year" in haystack or "first year" in haystack or "fylsx" in haystack:
        return DocumentCategory.FIRST_YEAR_LAW_STUDENT_EXAM
    if (
        "performance test" in haystack
        or "performance-test" in haystack
        or "ptanswers" in haystack
        or "ptandanswers" in haystack
        or "pta-b" in haystack
        or re.search(r"\bpt\b", haystack)
    ):
        return DocumentCategory.PERFORMANCE_TESTS_AND_SELECTED_ANSWERS
    if "essay" in haystack and ("selected answer" in haystack or "selected-answer" in haystack):
        return DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS
    if "cbx" in haystack and ("selected" in haystack or "answer" in haystack) and "essay" in haystack:
        return DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS
    if "essay" in haystack and "question" in haystack:
        return DocumentCategory.EXAM_QUESTIONS
    if (
        "california bar examination questions" in haystack
        or "cbxquestions" in haystack
        or re.search(r"cbx[-_/]?(?:essay[-_/]?)?questions", haystack)
    ):
        return DocumentCategory.EXAM_QUESTIONS
    return DocumentCategory.UNKNOWN


def extract_administration_metadata(text: str) -> tuple[int | None, str | None]:
    lowered = text.casefold()
    month: str | None = None
    for alias, canonical in MONTH_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}(?:ruary)?\b", lowered) or re.search(
            rf"{re.escape(alias)}(?=20\d{{2}})", lowered
        ):
            month = canonical
            break
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", lowered)
    if year_match is None:
        year_match = re.search(r"(19\d{2}|20\d{2})", lowered)
    year = int(year_match.group(1)) if year_match else None
    return year, month


def _is_pdf_href(href: str) -> bool:
    parsed = urlparse(href)
    candidate = parsed.path.casefold()
    return candidate.endswith(".pdf") or ".pdf" in candidate


def _nearest_heading(anchor: Tag) -> str | None:
    for current in [anchor, *anchor.parents]:
        previous = current.find_previous(["h1", "h2", "h3", "h4", "h5"])
        if previous:
            text = collapse_inline_whitespace(previous.get_text(" ", strip=True))
            if text:
                return text
    return None


def _nearby_text(anchor: Tag) -> str:
    parent = anchor.find_parent(["li", "p", "div", "td", "tr", "section"])
    if parent is None:
        return ""
    return collapse_inline_whitespace(parent.get_text(" ", strip=True))[:1000]


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    return path.rsplit("/", 1)[-1] or "document.pdf"
