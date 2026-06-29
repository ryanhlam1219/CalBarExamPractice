from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from app.config import get_settings
from app.db.models.enums import ComponentType, ReviewStatus, RuleStatus
from app.parsing.text import collapse_inline_whitespace, normalize_paragraph_text, normalized_key
from app.schemas.pdf import DocumentExtraction
from app.schemas.rules import ParsedRule, ParsedRuleComponent, ParsedTopicSource, RuleParseResult


@dataclass
class RuleLine:
    text: str
    page_number: int
    font_size: float
    is_bold: bool
    y0: float | None


HEADING_PREFIX_RE = re.compile(
    r"^(?:(chapter|part)\s+\d+|[IVXLC]+\.\s+|[A-Z]\.\s+|\d+\.\s+|[a-z]\)\s+|\([a-z0-9]+\)\s+)",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\(?\d+\)|\(?[a-z]\)|\d+\.|[a-z]\.)\s+")


class RuleOutlineParser:
    def __init__(self, parser_version: str | None = None) -> None:
        self.parser_version = parser_version or get_settings().parser_version

    def parse(
        self,
        extraction: DocumentExtraction,
        source_document_id: int | None = None,
        subject_hint: str | None = None,
    ) -> RuleParseResult:
        lines = _lines_from_extraction(extraction)
        if not lines:
            fallback = subject_hint or "Unknown"
            return RuleParseResult(
                source_document_id=source_document_id,
                subject_canonical_name=fallback.lower().replace(" ", "_"),
                subject_display_name=fallback,
                issues=["No text blocks were extracted."],
                parser_version=self.parser_version,
            )
        base_font = median([line.font_size for line in lines if line.font_size > 0] or [10.0])
        subject, subject_line = _detect_subject(lines, subject_hint)
        topic_stack: list[str] = [subject]
        topics: list[list[str]] = [[subject]]
        topic_sources: list[ParsedTopicSource] = []
        rules: list[ParsedRule] = []

        section_heading: str | None = None
        section_topic: list[str] = topic_stack.copy()
        section_body: list[str] = []
        section_start_page: int = 1
        section_components: list[ParsedRuleComponent] = []
        component_order = 0

        for line in lines:
            text = collapse_inline_whitespace(line.text)
            if _is_noise(text) or _is_footnote(text):
                continue
            heading_level = _heading_level(line, base_font)
            if heading_level is not None and len(text) <= 140:
                rule = _finalize_section(
                    section_heading, section_topic, section_body,
                    section_start_page, line.page_number, section_components,
                )
                if rule:
                    rules.append(rule)
                section_heading = _clean_heading(text)
                section_body = []
                section_components = []
                component_order = 0
                topic_stack = _replace_topic(topic_stack, section_heading, heading_level)
                section_topic = topic_stack.copy()
                section_start_page = line.page_number
                if topic_stack not in topics:
                    topics.append(topic_stack.copy())
                    topic_sources.append(
                        ParsedTopicSource(
                            topic_path=topic_stack.copy(),
                            source_page=line.page_number,
                            source_text=text,
                        )
                    )
                continue

            component_type = _component_type(text)
            if component_type and section_heading:
                component_order += 1
                section_components.append(
                    ParsedRuleComponent(
                        component_type=component_type.value,
                        label=_component_label(text),
                        content=normalize_paragraph_text(_strip_component_prefix(text)),
                        display_order=component_order,
                        source_page=line.page_number,
                        source_text=text,
                    )
                )
                continue

            section_body.append(text)

        rule = _finalize_section(
            section_heading, section_topic, section_body,
            section_start_page, lines[-1].page_number if lines else 1,
            section_components,
        )
        if rule:
            rules.append(rule)

        rules = _merge_continuation_fragments(rules)

        fallback = subject_hint or "Unknown"
        return RuleParseResult(
            source_document_id=source_document_id,
            subject_canonical_name=(subject or fallback).lower().replace(" ", "_"),
            subject_display_name=subject or fallback,
            subject_source_page=subject_line.page_number if subject_line else None,
            subject_source_text=subject_line.text if subject_line else None,
            topics=topics,
            topic_sources=topic_sources,
            rules=rules,
            issues=[],
            parser_version=self.parser_version,
        )


def _lines_from_extraction(extraction: DocumentExtraction) -> list[RuleLine]:
    lines: list[RuleLine] = []
    for page in extraction.pages:
        blocks = page.blocks or []
        for block in sorted(blocks, key=lambda item: (item.bbox[1] if item.bbox else 0, item.block_index)):
            font_size = max(block.font_sizes) if block.font_sizes else 0.0
            y0 = block.bbox[1] if block.bbox else None
            for text_line in block.text.splitlines():
                text = collapse_inline_whitespace(text_line)
                if text:
                    lines.append(
                        RuleLine(
                            text=text,
                            page_number=page.page_number,
                            font_size=font_size,
                            is_bold=block.is_bold,
                            y0=y0,
                        )
                    )
    if lines:
        return lines
    for page in extraction.pages:
        for text_line in page.normalized_text.splitlines():
            text = collapse_inline_whitespace(text_line)
            if text:
                lines.append(RuleLine(text=text, page_number=page.page_number, font_size=0.0, is_bold=False, y0=None))
    return lines


_SUBJECT_NAMES: dict[str, str] = {
    "agency": "Agency",
    "civil procedure": "Civil Procedure",
    "community property": "Community Property",
    "constitutional law": "Constitutional Law",
    "contract": "Contracts",
    "contracts": "Contracts",
    "contracts and sales": "Contracts",
    "corporation": "Corporations",
    "corporations": "Corporations",
    "criminal law": "Criminal Law",
    "criminal procedure": "Criminal Procedure",
    "evidence": "Evidence",
    "legal remedies": "Legal Remedies",
    "remedies": "Legal Remedies",
    "partnership": "Partnerships",
    "partnerships": "Partnerships",
    "professional responsibility": "Professional Responsibility",
    "real property": "Real Property",
    "tort": "Torts",
    "torts": "Torts",
    "trust": "Trusts",
    "trusts": "Trusts",
    "will": "Wills",
    "wills": "Wills",
    "wills and succession": "Wills",
}


def _finalize_section(
    heading: str | None,
    topic_path: list[str],
    body_lines: list[str],
    start_page: int,
    end_page: int,
    components: list[ParsedRuleComponent],
) -> ParsedRule | None:
    """Convert an accumulated outline section into a ParsedRule."""
    if not heading:
        return None
    cleaned = [_clean_body_line(ln) for ln in body_lines]
    cleaned = [ln for ln in cleaned if ln]
    statement = normalize_paragraph_text("\n".join(cleaned)) if cleaned else ""
    if len(statement) < 20 and not components:
        return None

    full_text = f"{heading}\n{statement}" if statement else heading
    confidence = 0.75 if len(statement) > 80 else 0.60
    if components:
        confidence = min(confidence + 0.10, 0.95)

    return ParsedRule(
        topic_path=topic_path,
        canonical_name=heading,
        rule_statement=statement or heading,
        short_rule_statement=_short_rule(statement or heading),
        jurisdiction_scope=_jurisdiction_scope(full_text),
        rule_status=_rule_status(full_text).value,
        parse_confidence=confidence,
        review_status=(
            ReviewStatus.AUTO_ACCEPTED.value if confidence >= 0.75 else ReviewStatus.NEEDS_REVIEW.value
        ),
        start_page=start_page,
        end_page=end_page,
        source_text=full_text[:1000],
        components=components,
        metadata={},
    )


def _merge_continuation_fragments(rules: list[ParsedRule]) -> list[ParsedRule]:
    """Fix mid-sentence splits: when a section's statement starts with lowercase,
    its leading text likely belongs to the previous section's statement."""
    if len(rules) < 2:
        return rules
    merged: list[ParsedRule] = [rules[0]]
    for rule in rules[1:]:
        stmt = rule.rule_statement
        words = stmt.split()
        if not words:
            merged.append(rule)
            continue
        first = words[0]
        if first[0].islower() and not re.match(r"^[ivx]+\)", first):
            prev = merged[-1]
            # Split at first sentence boundary to find the fragment
            sent_match = re.search(r"[.!?]\s+[A-Z]", stmt)
            if sent_match:
                fragment = stmt[:sent_match.start() + 1]
                remainder = stmt[sent_match.start() + 1:].strip()
            else:
                fragment = stmt
                remainder = ""
            prev.rule_statement = f"{prev.rule_statement} {fragment}".strip()
            prev.end_page = rule.start_page
            if remainder:
                rule.rule_statement = remainder
                rule.short_rule_statement = _short_rule(remainder)
                merged.append(rule)
        else:
            merged.append(rule)
    return merged


_INLINE_FOOTNOTE_RE = re.compile(r"\[(?:[ivxlc]{1,8})\]", re.IGNORECASE)


def _clean_body_line(text: str) -> str:
    """Clean a single body text line: strip footnote refs, page fractions, URLs, branding."""
    text = _INLINE_FOOTNOTE_RE.sub("", text)
    text = re.sub(r"\d+/\d+$", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"©\s*\d{4}\s*themisbar\.com.*$", "", text, flags=re.IGNORECASE)
    return collapse_inline_whitespace(text).strip()


def _is_footnote(text: str) -> bool:
    stripped = text.strip()
    if re.match(r"^\[(?:i{1,4}v?|vi{0,3}|ix|x{1,4}v?|xi{1,4}v?|xix|xx[iv]{0,4})\]", stripped, re.IGNORECASE):
        return True
    if re.match(r"^(?:Fed\.\s*R\.|CEC\s*§|Cal\.\s*Evid|U\.S\.C|F\.\s*(?:2d|3d|Supp))", stripped):
        return True
    if re.match(r"https?://", stripped):
        return True
    if re.match(r"^\d+/\d+$", stripped):
        return True
    return False


def _detect_subject(lines: list[RuleLine], hint: str | None = None) -> tuple[str, RuleLine | None]:
    if hint:
        for line in lines[:40]:
            if hint.casefold() in line.text.casefold():
                return hint, line
        return hint, lines[0] if lines else None

    for line in lines[:40]:
        lowered = line.text.casefold()
        for key, display in _SUBJECT_NAMES.items():
            if key in lowered and len(line.text.strip()) < 100:
                return display, line
    return "Unknown", lines[0] if lines else None


_NOISE_PHRASES = {
    "table of contents", "contents", "back to top", "page break",
    "continued", "see above", "see below", "end of chapter",
    "notes", "note", "sidebar", "summary", "review",
    "task is completed", "your task is completed",
    "click to view", "scroll to top",
    "quick jump menu", "search", "granted", "editor's note",
}


def _is_noise(text: str) -> bool:
    lowered = text.casefold().strip()
    return (
        len(text) <= 2
        or lowered in _NOISE_PHRASES
        or bool(re.fullmatch(r"\d+", text))
        or "copyright" in lowered
        or "mythemis" in lowered
        or "learners" in lowered
        or bool(re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", text))
    )


def _heading_level(line: RuleLine, base_font: float) -> int | None:
    text = line.text.strip()
    lowered = text.casefold()
    if len(text) > 160 or text.endswith("."):
        return None
    if lowered in _NOISE_PHRASES:
        return None
    if lowered.startswith(("chapter ", "part ")):
        return 1
    if re.match(r"^[IVXLC]+\.\s+", text):
        return 1
    if re.match(r"^[A-Z]\.\s+", text):
        return 2
    if re.match(r"^\d+\.\s+", text):
        return 3
    if line.is_bold and line.font_size >= base_font + 1 and len(text) <= 100:
        return 2
    if text.isupper() and 6 <= len(text) <= 80 and not re.match(r"^\(.*\)$", text):
        return 2
    if HEADING_PREFIX_RE.match(text) and len(text) <= 100 and len(text) >= 6:
        return 3
    return None


def _replace_topic(stack: list[str], heading: str, level: int) -> list[str]:
    stack = stack[: max(level, 1)]
    if not stack:
        stack = [heading or "Unknown"]
    if heading and heading != stack[-1]:
        stack.append(heading)
    return stack


def _clean_heading(text: str) -> str:
    text = re.sub(r"^(?:chapter|part)\s+\d+\s*[:.-]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[IVXLC]+\.\s+", "", text)
    text = re.sub(r"^[A-Za-z]\.\s+", "", text)
    text = re.sub(r"^\d+\.\s+", "", text)
    text = re.sub(r"^[a-z0-9]+\)\s+", "", text)
    text = re.sub(r"^\([a-z0-9]+\)\s+", "", text)
    text = re.sub(r"^[ivxlc]+\)\s+", "", text, flags=re.IGNORECASE)
    text = text.rstrip(";:,")
    text = re.sub(r"\s+(?:and|or)\s*$", "", text, flags=re.IGNORECASE)
    result = collapse_inline_whitespace(text).title()
    if len(result) > 80:
        result = result[:77].rsplit(" ", 1)[0] + "..."
    return result


def _looks_like_rule(text: str) -> bool:
    lowered = text.casefold()
    if len(text) < 40 or BULLET_RE.match(text):
        return False
    cues = [
        " is ", " are ", " must ", " may ", " shall ",
        " requires ", " requirement", " required ",
        " rule", " standard", " test ",
        " if ", " when ", " unless ", " where ",
        " defined as ", " means ",
        " liable ", " liability ",
        " entitled ", " right to ",
        " duty ", " obligation ",
        " prohibited ", " unlawful ",
        " valid ", " invalid ",
        " presumed ", " presumption ",
        " burden of ",
        " elements ",
    ]
    return any(cue in f" {lowered} " for cue in cues)


def _continuation_line(text: str) -> bool:
    return len(text) > 35 and not BULLET_RE.match(text)


def _component_type(text: str) -> ComponentType | None:
    lowered = text.casefold().strip()
    if len(text) > 120:
        return None
    if lowered in {"exam tip", "tip"} or lowered.startswith("exam tip:") or lowered.startswith("tip:"):
        return ComponentType.EXAM_TIP
    if lowered in {"exception", "exceptions"} or lowered.startswith("exception:"):
        return ComponentType.EXCEPTION
    if lowered in {"definition", "definitions"} or lowered.startswith("definition:"):
        return ComponentType.DEFINITION
    if lowered.startswith("majority rule") or lowered.startswith("majority:") or lowered == "majority":
        return ComponentType.MAJORITY_RULE
    if lowered.startswith("minority rule") or lowered.startswith("minority:") or lowered == "minority":
        return ComponentType.MINORITY_RULE
    if lowered.startswith("traditional rule") or lowered.startswith("traditional:") or lowered == "traditional":
        return ComponentType.TRADITIONAL_RULE
    if lowered.startswith("modern rule") or lowered.startswith("modern:") or lowered in {"modern", "utc"}:
        return ComponentType.MODERN_RULE
    if BULLET_RE.match(text):
        return ComponentType.ELEMENT
    return None


def _component_label(text: str) -> str | None:
    match = BULLET_RE.match(text)
    if match:
        return match.group(0).strip()
    if ":" in text:
        return text.split(":", 1)[0].strip()
    return None


def _strip_component_prefix(text: str) -> str:
    text = BULLET_RE.sub("", text).strip()
    return re.sub(r"^(Exam Tip|Tip|Exception|Definition|Majority|Minority|Traditional|Modern|UTC)\s*[:.-]\s*", "", text, flags=re.IGNORECASE)


def _rule_status(text: str) -> RuleStatus:
    lowered = text.casefold()
    if "california" in lowered or " ca " in f" {lowered} ":
        return RuleStatus.CALIFORNIA_SPECIFIC
    if "utc" in lowered or "uniform trust code" in lowered:
        return RuleStatus.UTC
    if "majority" in lowered:
        return RuleStatus.MAJORITY
    if "minority" in lowered:
        return RuleStatus.MINORITY
    if "traditional" in lowered:
        return RuleStatus.TRADITIONAL
    if "modern" in lowered:
        return RuleStatus.MODERN_TREND
    if "exception" in lowered:
        return RuleStatus.EXCEPTION
    return RuleStatus.GENERAL


def _jurisdiction_scope(text: str) -> str:
    lowered = text.casefold()
    if "california" in lowered:
        return "CALIFORNIA"
    if "utc" in lowered:
        return "UTC"
    return "GENERAL"


def _rule_confidence(text: str, topic_stack: list[str]) -> float:
    score = 0.6
    if len(text) > 90:
        score += 0.15
    if topic_stack:
        score += 0.1
    if _rule_status(text) != RuleStatus.GENERAL:
        score += 0.05
    return min(score, 0.95)


_SKIP_TOPIC_NAMES = {"generally", "overview", "introduction", "back to top", "note", "notes"}


def _rule_name(topic_stack: list[str], text: str) -> str:
    m = re.match(
        r"^([A-Z][a-zA-Z][\w\s,’’’-]{1,58}?)\s+"
        r"(?:is|are|describes?|means?|refers?\s+to|requires?|involves?"
        r"|provides?|occurs?|exists?|includes?|applies|permits?|prohibits?)\b",
        text,
    )
    if m:
        name = collapse_inline_whitespace(m.group(1)).strip(" ,;:-")
        if len(name) >= 3:
            return name

    if ":" in text:
        before = text.split(":", 1)[0].strip()
        cleaned = collapse_inline_whitespace(before)
        if 3 < len(cleaned) <= 70:
            return cleaned

    topic_name = topic_stack[-1] if len(topic_stack) > 1 else ""
    if topic_name and topic_name.lower() not in _SKIP_TOPIC_NAMES:
        qualifier = _short_qualifier(text)
        if qualifier and qualifier.lower() != topic_name.lower():
            return f"{topic_name} — {qualifier}"
        return topic_name

    cleaned = collapse_inline_whitespace(text)
    words = cleaned.split()[:8]
    return " ".join(words).rstrip(".,;:")


def _short_qualifier(text: str) -> str:
    """Extract first few meaningful words from rule text as a qualifier."""
    cleaned = collapse_inline_whitespace(text)
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    cleaned = re.sub(r"\(.*?\)", "", cleaned)
    skip = {
        "a", "an", "the", "if", "in", "on", "at", "to", "of", "for",
        "by", "is", "are", "it", "or", "and", "but", "as", "so", "no",
        "be", "do", "has", "have", "had", "was", "were", "not", "this",
        "that", "with", "from", "its", "may", "can", "will", "shall",
    }
    words = [w.strip(".,;:") for w in cleaned.split() if w.lower().strip(".,;:") not in skip]
    words = [w for w in words if len(w) > 1]
    result = " ".join(words[:4])
    if len(result) > 45:
        result = result[:45].rsplit(" ", 1)[0]
    return result


def _short_rule(text: str) -> str:
    normalized = collapse_inline_whitespace(text)
    if len(normalized) <= 180:
        return normalized
    return normalized[:177].rsplit(" ", 1)[0] + "..."
