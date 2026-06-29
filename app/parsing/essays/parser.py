from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings
from app.db.models.enums import ReviewStatus
from app.parsing.text import normalize_paragraph_text, short_preview
from app.schemas.essays import (
    EssayParseResult,
    ParsedEssayQuestion,
    ParsedSelectedAnswer,
    ReconciliationIssue,
)
from app.schemas.pdf import DocumentExtraction

QUESTION_RE = re.compile(
    r"^\s*(?:essay\s+)?question(?:\s+no\.?)?\s*(\d+)\b(?:\s*[:.-]?\s*.*)?$",
    re.IGNORECASE,
)
ANSWER_RE = re.compile(
    r"^\s*(?:(?:essay\s+)?question\s+(\d+)\s*[:.-]?\s*)?"
    r"(?:selected\s+)?answer\s+([A-Z]|\d+)\b"
    r"(?:\s+to\s+(?:essay\s+)?question\s+(\d+))?"
    r"(?:\s*[:.-]?\s*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Marker:
    kind: str
    start: int
    end: int
    page_number: int
    question_number: int | None = None
    answer_label: str | None = None
    line: str = ""


@dataclass(frozen=True)
class PageSpan:
    page_number: int
    start: int
    end: int


class EssayParser:
    def __init__(self, parser_version: str | None = None, min_question_chars: int = 250) -> None:
        self.parser_version = parser_version or get_settings().parser_version
        self.min_question_chars = min_question_chars

    def parse(
        self,
        extraction: DocumentExtraction,
        source_document_id: int | None = None,
        jurisdiction: str = "California",
        exam_name: str = "California Bar Examination",
        exam_year: int | None = None,
        exam_month: str | None = None,
    ) -> EssayParseResult:
        combined, page_spans = _combine_pages(extraction)
        markers = _find_markers(combined, page_spans)
        answer_markers = [marker for marker in markers if marker.kind == "answer"]
        question_markers = [marker for marker in markers if marker.kind == "question"]

        issues: list[ReconciliationIssue] = []
        questions = _dedupe_questions_by_number(self._parse_questions(
            combined, page_spans, markers, question_markers, jurisdiction, exam_name, exam_year, exam_month
        ))
        answers = self._parse_answers(combined, page_spans, markers, answer_markers)
        issues.extend(_reconcile(questions, answers))
        if not questions:
            issues.append(
                ReconciliationIssue(
                    severity="error",
                    code="no_questions",
                    message="No essay question boundaries were detected.",
                )
            )
        return EssayParseResult(
            source_document_id=source_document_id,
            questions=questions,
            selected_answers=answers,
            issues=issues,
            parser_version=self.parser_version,
        )

    def _parse_questions(
        self,
        combined: str,
        page_spans: list[PageSpan],
        markers: list[Marker],
        question_markers: list[Marker],
        jurisdiction: str,
        exam_name: str,
        exam_year: int | None,
        exam_month: str | None,
    ) -> list[ParsedEssayQuestion]:
        questions: list[ParsedEssayQuestion] = []
        parsed_markers: list[tuple[Marker, int]] = []
        for marker in question_markers:
            following = [candidate for candidate in markers if candidate.start > marker.start]
            next_marker = following[0] if following else None
            next_start = next_marker.start if next_marker else len(combined)
            text_before_next_marker = combined[marker.end : next_start].strip()
            if next_marker and next_marker.kind == "answer" and len(text_before_next_marker) < 80:
                continue
            parsed_markers.append((marker, next_start))

        instructions = combined[: parsed_markers[0][0].start].strip() if parsed_markers else None
        for index, (marker, next_start) in enumerate(parsed_markers):
            raw = combined[marker.start:next_start].strip()
            normalized = normalize_paragraph_text(raw)
            confidence = _question_confidence(raw, marker, self.min_question_chars)
            start_page = _page_for_offset(page_spans, marker.start)
            end_page = _page_for_offset(page_spans, max(marker.start, next_start - 1))
            questions.append(
                ParsedEssayQuestion(
                    question_number=marker.question_number or 0,
                    raw_text=raw,
                    normalized_text=normalized,
                    instructions_text=instructions if index == 0 and instructions else None,
                    start_page=start_page,
                    end_page=end_page,
                    start_character_offset=marker.start,
                    end_character_offset=next_start,
                    parse_confidence=confidence,
                    review_status=(
                        ReviewStatus.AUTO_ACCEPTED.value if confidence >= 0.82 else ReviewStatus.NEEDS_REVIEW.value
                    ),
                    metadata={
                        "heading": marker.line,
                        "preview": short_preview(raw),
                        "exam_year": exam_year,
                        "exam_month": exam_month,
                        "jurisdiction": jurisdiction,
                        "exam_name": exam_name,
                    },
                )
            )
        return questions

    def _parse_answers(
        self,
        combined: str,
        page_spans: list[PageSpan],
        markers: list[Marker],
        answer_markers: list[Marker],
    ) -> list[ParsedSelectedAnswer]:
        answers: list[ParsedSelectedAnswer] = []
        answer_zone_markers = [marker for marker in markers if marker.start >= answer_markers[0].start] if answer_markers else []
        for marker in answer_markers:
            following = [candidate for candidate in answer_zone_markers if candidate.start > marker.start]
            next_start = following[0].start if following else len(combined)
            raw = combined[marker.start:next_start].strip()
            question_number = marker.question_number or _nearest_prior_question(marker, markers)
            normalized = normalize_paragraph_text(raw)
            confidence = _answer_confidence(raw, question_number)
            answers.append(
                ParsedSelectedAnswer(
                    question_number=question_number,
                    answer_label=(marker.answer_label or "").upper(),
                    raw_text=raw,
                    normalized_text=normalized,
                    start_page=_page_for_offset(page_spans, marker.start),
                    end_page=_page_for_offset(page_spans, max(marker.start, next_start - 1)),
                    start_character_offset=marker.start,
                    end_character_offset=next_start,
                    parse_confidence=confidence,
                    review_status=(
                        ReviewStatus.AUTO_ACCEPTED.value if confidence >= 0.8 else ReviewStatus.NEEDS_REVIEW.value
                    ),
                    metadata={"heading": marker.line, "preview": short_preview(raw)},
                )
            )
        return answers


def _dedupe_questions_by_number(questions: list[ParsedEssayQuestion]) -> list[ParsedEssayQuestion]:
    """Keep the first detected prompt for each question number.

    Selected-answer bodies often contain headings like "Question 1 -- Negligence".
    Those are useful answer text, but they should not become additional prompt
    records or violate the per-document question-number constraint.
    """
    seen: set[int] = set()
    deduped: list[ParsedEssayQuestion] = []
    for question in questions:
        if question.question_number in seen:
            continue
        seen.add(question.question_number)
        deduped.append(question)
    return deduped


def _combine_pages(extraction: DocumentExtraction) -> tuple[str, list[PageSpan]]:
    parts: list[str] = []
    spans: list[PageSpan] = []
    offset = 0
    for page in extraction.pages:
        page_text = page.raw_text.strip()
        if parts:
            parts.append("\n\n")
            offset += 2
        start = offset
        parts.append(page_text)
        offset += len(page_text)
        spans.append(PageSpan(page_number=page.page_number, start=start, end=offset))
    return "".join(parts), spans


def _find_markers(combined: str, page_spans: list[PageSpan]) -> list[Marker]:
    markers: list[Marker] = []
    offset = 0
    for line in combined.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            answer_match = ANSWER_RE.match(stripped)
            if answer_match:
                question_number = int(answer_match.group(1) or answer_match.group(3)) if (answer_match.group(1) or answer_match.group(3)) else None
                markers.append(
                    Marker(
                        kind="answer",
                        start=offset,
                        end=offset + len(line),
                        page_number=_page_for_offset(page_spans, offset),
                        question_number=question_number,
                        answer_label=answer_match.group(2).upper(),
                        line=stripped,
                    )
                )
            question_match = QUESTION_RE.match(stripped)
            if question_match and "answer" not in stripped.casefold():
                markers.append(
                    Marker(
                        kind="question",
                        start=offset,
                        end=offset + len(line),
                        page_number=_page_for_offset(page_spans, offset),
                        question_number=int(question_match.group(1)),
                        line=stripped,
                    )
                )
        offset += len(line)
    return sorted(markers, key=lambda marker: (marker.start, marker.kind))


def _page_for_offset(page_spans: list[PageSpan], offset: int) -> int:
    previous: PageSpan | None = None
    for span in page_spans:
        if span.start <= offset <= span.end:
            return span.page_number
        if previous is not None and previous.end < offset < span.start:
            return previous.page_number
        previous = span
    return page_spans[-1].page_number if page_spans else 1


def _nearest_prior_question(marker: Marker, markers: list[Marker]) -> int | None:
    prior_questions = [
        candidate
        for candidate in markers
        if candidate.kind == "question" and candidate.start < marker.start and candidate.question_number is not None
    ]
    return prior_questions[-1].question_number if prior_questions else None


def _question_confidence(raw: str, marker: Marker, min_question_chars: int) -> float:
    score = 0.65
    if marker.question_number:
        score += 0.15
    if len(raw) >= min_question_chars:
        score += 0.15
    if "selected answer" in raw.casefold():
        score -= 0.4
    if len(raw) < 80:
        score -= 0.25
    return max(0.0, min(1.0, score))


def _answer_confidence(raw: str, question_number: int | None) -> float:
    score = 0.65
    if question_number:
        score += 0.15
    if len(raw) > 300:
        score += 0.15
    if "question" in raw[:80].casefold() or "answer" in raw[:80].casefold():
        score += 0.05
    if len(raw) < 100:
        score -= 0.25
    return max(0.0, min(1.0, score))


def _reconcile(
    questions: list[ParsedEssayQuestion],
    answers: list[ParsedSelectedAnswer],
) -> list[ReconciliationIssue]:
    issues: list[ReconciliationIssue] = []
    question_numbers = [question.question_number for question in questions]
    duplicate_questions = {number for number in question_numbers if question_numbers.count(number) > 1}
    for number in sorted(duplicate_questions):
        issues.append(
            ReconciliationIssue(
                severity="error",
                code="duplicate_question",
                message=f"Question {number} appeared more than once.",
            )
        )
    for question in questions:
        if len(question.raw_text) < 250:
            issues.append(
                ReconciliationIssue(
                    severity="warning",
                    code="short_question",
                    message=f"Question {question.question_number} is shorter than expected.",
                    metadata={"length": len(question.raw_text)},
                )
            )
        if "selected answer" in question.raw_text.casefold():
            issues.append(
                ReconciliationIssue(
                    severity="error",
                    code="answer_text_in_question",
                    message=f"Question {question.question_number} appears to include selected-answer text.",
                )
            )
        matched = [answer for answer in answers if answer.question_number == question.question_number]
        if not matched:
            issues.append(
                ReconciliationIssue(
                    severity="warning",
                    code="question_without_answers",
                    message=f"Question {question.question_number} has no selected answers.",
                )
            )
    for answer in answers:
        if answer.question_number not in question_numbers:
            issues.append(
                ReconciliationIssue(
                    severity="warning",
                    code="answer_without_question",
                    message=f"Selected answer {answer.answer_label} could not be matched to a parsed question.",
                    metadata={"question_number": answer.question_number},
                )
            )
    seen_answer_keys: set[tuple[int | None, str]] = set()
    for answer in answers:
        key = (answer.question_number, answer.answer_label)
        if key in seen_answer_keys:
            issues.append(
                ReconciliationIssue(
                    severity="warning",
                    code="duplicate_answer_label",
                    message=f"Duplicate selected answer label {answer.answer_label} for question {answer.question_number}.",
                )
            )
        seen_answer_keys.add(key)
    return issues
