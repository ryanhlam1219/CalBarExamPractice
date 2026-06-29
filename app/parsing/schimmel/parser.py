from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.models.enums import ReviewStatus
from app.parsing.pdf.extractor import PDFExtractor
from app.parsing.schimmel.abbreviation_normalizer import SchimmelAbbreviationNormalizer
from app.parsing.schimmel.bullet_classifier import SchimmelBulletClassifier
from app.parsing.schimmel.cross_reference_resolver import SchimmelCrossReferenceResolver
from app.parsing.schimmel.heading_classifier import (
    NODE_TYPE_CROSS_REFERENCE,
    NODE_TYPE_ELEMENT,
    NODE_TYPE_EXCEPTION,
    NODE_TYPE_ISSUE,
    NODE_TYPE_MAJOR_TOPIC,
    NODE_TYPE_OTHER,
    NODE_TYPE_RULE,
    NODE_TYPE_SUBISSUE,
    NODE_TYPE_SUBJECT,
    NODE_TYPE_TOPIC,
    SchimmelHeadingClassifier,
)
from app.parsing.schimmel.hierarchy_builder import SchimmelHierarchyBuilder
from app.parsing.schimmel.jurisdiction_classifier import SchimmelJurisdictionClassifier
from app.parsing.schimmel.models import (
    SchimmelAbbreviationCandidate,
    SchimmelCrossReferenceCandidate,
    SchimmelDocumentCandidate,
    SchimmelHeadingCandidate,
    SchimmelRuleCandidate,
    SchimmelSubjectSection,
    SchimmelTemplateNodeCandidate,
    SchimmelValidationFinding,
)
from app.parsing.schimmel.subject_detector import SchimmelSubjectDetector
from app.parsing.schimmel.validator import SchimmelTemplateValidator
from app.parsing.text import collapse_inline_whitespace, normalize_extracted_text, normalize_paragraph_text
from app.schemas.pdf import DocumentExtraction
from app.services.files import sha256_file, write_json


# Common footer patterns in Schimmel document
FOOTER_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\d+$"),  # Page numbers
    re.compile(r"Prof\.\s+Sarah\s+Schimmel", re.IGNORECASE),
    re.compile(r"sschimmel@swlaw\.edu", re.IGNORECASE),
    re.compile(r"^\d+\s*of\s*\d+$"),  # "1 of 15" style
]

# Lines that look like repeated author footnotes
AUTHOR_FOOTNOTE_MARKERS: set[str] = {
    "Prof. Sarah Schimmel",
    "sschimmel@swlaw.edu",
}

BULLET_OR_NUMBERED_PREFIX = re.compile(r"^\s*(?:[-•*\d]+[.)]|\(?\d+\)|[a-z][.)])\s+")
NUMBERED_ELEMENT = re.compile(r"^\s*\(\d+\)\s+")


class SchimmelTemplateParser:
    """Parses the Schimmel essay-template PDF into a structured hierarchy."""

    def __init__(
        self,
        parser_version: str | None = None,
    ) -> None:
        self.parser_version = parser_version or get_settings().parser_version
        self.subject_detector = SchimmelSubjectDetector()
        self.heading_classifier = SchimmelHeadingClassifier()
        self.hierarchy_builder = SchimmelHierarchyBuilder()
        self.bullet_classifier = SchimmelBulletClassifier()
        self.jurisdiction_classifier = SchimmelJurisdictionClassifier()
        self.abbreviation_normalizer = SchimmelAbbreviationNormalizer()
        self.cross_reference_resolver = SchimmelCrossReferenceResolver()
        self.validator = SchimmelTemplateValidator()
        self.extractor = PDFExtractor(parser_version=parser_version)

    def parse_from_pdf(
        self,
        pdf_path: Path,
        output_dir: Path | None = None,
        dry_run: bool = False,
    ) -> SchimmelDocumentCandidate:
        """Full parse pipeline: PDF -> extraction -> blocks -> subjects -> nodes."""
        settings = get_settings()

        # Stage 1: Extract PDF
        extraction = self.extractor.extract(pdf_path)

        # Save intermediate extraction
        if output_dir:
            extracted_path = output_dir / f"{pdf_path.stem}.extraction.json"
            write_json(extracted_path, extraction.model_dump(mode="json"))

        # Stage 2: Parse blocks into document candidate
        document = self._parse_extraction(extraction, output_dir)

        # Stage 3: Validate
        findings = self.validator.validate(document)
        document.validation_findings = findings

        # Save validation report
        if output_dir:
            summary = self.validator.produce_summary(document, findings)
            report_path = output_dir / f"{pdf_path.stem}.validation.json"
            write_json(report_path, summary)

        return document

    def _parse_extraction(
        self,
        extraction: DocumentExtraction,
        output_dir: Path | None = None,
    ) -> SchimmelDocumentCandidate:
        """Convert DocumentExtraction into a structured SchimmelDocumentCandidate."""
        # Convert pages to blocks_by_page format
        blocks_by_page: dict[int, list[dict[str, Any]]] = {}
        for page in extraction.pages:
            blocks_by_page[page.page_number] = []
            for block in page.blocks:
                blocks_by_page[page.page_number].append({
                    "page_number": page.page_number,
                    "block_index": block.block_index,
                    "block_type": block.block_type,
                    "text": block.text,
                    "bbox": block.bbox,
                    "font_names": block.font_names,
                    "font_sizes": block.font_sizes,
                    "is_bold": block.is_bold,
                    "metadata": block.metadata,
                })

        # Detect subjects
        boundaries = self.subject_detector.detect_subjects(blocks_by_page)
        sections = self.subject_detector.build_subject_sections(boundaries, blocks_by_page)

        # Detect abbreviations
        all_abbreviations: list[SchimmelAbbreviationCandidate] = []
        for page in extraction.pages:
            candidates = self.abbreviation_normalizer.detect_abbreviations(page.normalized_text)
            all_abbreviations.extend(candidates)

        # Build candidate for each section
        for section in sections:
            candidates = self._build_section_candidates(section, blocks_by_page)
            section.candidates = candidates

        # Build tree for each section
        for section in sections:
            if section.candidates:
                trees = self.hierarchy_builder.build_tree(section.candidates)
                section.candidates = trees

        # Save intermediate per-subject JSON
        if output_dir:
            for section in sections:
                subject_dir = output_dir / "subjects"
                subject_dir.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r"[^a-z0-9]+", "_", section.normalized_name.casefold()).strip("_")
                subject_path = subject_dir / f"{safe_name}.json"
                self._write_subject_json(section, subject_path)

        document = SchimmelDocumentCandidate(
            source_path=str(extraction.source_path),
            sha256=extraction.sha256,
            page_count=extraction.page_count,
            subjects=sections,
            abbreviations=all_abbreviations,
        )

        return document

    def _build_section_candidates(
        self,
        section: SchimmelSubjectSection,
        blocks_by_page: dict[int, list[dict[str, Any]]],
    ) -> list[SchimmelTemplateNodeCandidate]:
        """Build template node candidates for a subject section from its blocks."""
        candidates: list[SchimmelTemplateNodeCandidate] = []

        # Add subject root node
        subject_root = SchimmelTemplateNodeCandidate(
            title=section.normalized_name,
            raw_text=section.raw_heading,
            normalized_text=section.normalized_name,
            node_type="SUBJECT",
            depth=0,
            display_order=0,
            page_number=section.start_page,
            end_page=section.end_page,
            parse_confidence=0.95,
            evidence={"source": "subject_detector", "is_subject": True},
        )
        candidates.append(subject_root)

        # Process blocks within subject page range
        order = 0
        for page_num in range(section.start_page, section.end_page + 1):
            blocks = blocks_by_page.get(page_num, [])

            for block in blocks:
                text = (block.get("text", "") or "").strip()
                if not text or self._is_noise(text):
                    continue

                # Split multi-line blocks into logical sections.
                # A block like "Insane Delusion\n• ...\nUndue Influence\n• ..."
                # contains multiple issues that must be processed separately.
                logical_units = self._split_block_into_units(text, block, page_num)

                for unit_text, unit_block in logical_units:
                    if not unit_text or self._is_noise(unit_text):
                        continue

                    heading = self.heading_classifier.classify_block(unit_block)
                    is_bullet_or_element = bool(BULLET_OR_NUMBERED_PREFIX.match(unit_text))

                    # Attach bullet content to the last heading node
                    if is_bullet_or_element and heading.heading_score < 0.6:
                        if candidates:
                            last_node = candidates[-1]
                            if last_node.node_type in (NODE_TYPE_ISSUE, NODE_TYPE_SUBISSUE, NODE_TYPE_RULE, NODE_TYPE_TOPIC):
                                self._attach_as_rule_content(last_node, unit_text, page_num)
                        continue

                    node_type = heading.node_type
                    jx = self.jurisdiction_classifier.detect_jurisdiction(unit_text)[0]
                    rule_variant = self.jurisdiction_classifier.detect_rule_variant(unit_text)[0]
                    normalized = self.abbreviation_normalizer.normalize_text(unit_text)
                    cross_refs = self.cross_reference_resolver.detect_cross_references(unit_text, page_num)

                    is_long_rule_text = len(unit_text) > 80 and node_type not in (
                        NODE_TYPE_SUBJECT, NODE_TYPE_MAJOR_TOPIC, NODE_TYPE_TOPIC, NODE_TYPE_ISSUE
                    )

                    order += 1
                    node = SchimmelTemplateNodeCandidate(
                        title=unit_text[:120] if len(unit_text) > 120 else unit_text,
                        raw_text=unit_text,
                        normalized_text=normalized,
                        node_type=node_type,
                        depth=heading.inferred_level,
                        display_order=order,
                        page_number=page_num,
                        end_page=page_num,
                        parse_confidence=heading.heading_score,
                        jurisdiction_scope=jx,
                        rule_variant=rule_variant,
                        evidence=heading.evidence,
                        cross_references=cross_refs,
                    )

                    if is_long_rule_text or node_type == NODE_TYPE_RULE:
                        self._attach_as_rule_content(node, unit_text, page_num)

                    candidates.append(node)

        # Fix depths relative to subject root
        for i, node in enumerate(candidates):
            if i == 0:
                node.depth = 0
            else:
                # Keep the depth from heading classification but ensure it's reasonable
                if node.depth < 1:
                    node.depth = 1

        return candidates

    _HEADING_LINE = re.compile(
        r"^[A-Z][A-Za-z' \-–—]+(?:\s+[A-Z][A-Za-z' \-–—]*)*$"
    )

    def _split_block_into_units(
        self,
        text: str,
        block: dict[str, Any],
        page_num: int,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Split a multi-line block into logical sections.

        A new section starts when we encounter a heading-like line (Title Case
        or ALL CAPS, multiple words, not a bullet) after bullet/element content.
        Single bullet lines do NOT start a new section — they belong to the
        preceding heading.
        """
        lines = text.split("\n")
        if len(lines) <= 2:
            return [(text, block)]

        sections: list[list[str]] = []
        current: list[str] = []
        prev_was_bullet = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            is_bullet = stripped.startswith(("•", "-", "(")) or stripped == "•"
            is_heading = (
                not is_bullet
                and self._HEADING_LINE.match(stripped) is not None
                and len(stripped) > 5
                and " " in stripped  # multi-word — single words are usually elements
            )

            # Start a new section when a heading appears after bullet content
            if current and is_heading and prev_was_bullet:
                sections.append(current)
                current = [stripped]
            else:
                current.append(stripped)

            prev_was_bullet = is_bullet

        if current:
            sections.append(current)

        if len(sections) <= 1:
            return [(text, block)]

        results: list[tuple[str, dict[str, Any]]] = []
        for section_lines in sections:
            section_text = "\n".join(section_lines)
            unit_block = {
                **block,
                "text": section_text,
                "is_bold": block.get("is_bold", False),
            }
            results.append((section_text, unit_block))
        return results

    def _attach_as_rule_content(self, node: SchimmelTemplateNodeCandidate, text: str, page_num: int) -> None:
        """Attach inline text as a rule candidate or elements to the node."""
        # Check if this is a numbered element list
        if NUMBERED_ELEMENT.match(text):
            elements = self.bullet_classifier.extract_elements(text)
            if not node.rule_candidates:
                # Create a rule candidate for the parent issue
                rule = SchimmelRuleCandidate(
                    raw_rule_text=text,
                    normalized_rule_text=self.abbreviation_normalizer.normalize_text(text),
                    start_page=page_num,
                    end_page=page_num,
                    parse_confidence=0.7,
                    elements=elements,
                )
                node.rule_candidates.append(rule)
            else:
                node.rule_candidates[-1].elements.extend(elements)
        elif len(text) > 30:
            # It's a rule-like statement
            jx = self.jurisdiction_classifier.detect_jurisdiction(text)[0]
            bullet_classification = self.bullet_classifier.classify(text)

            if bullet_classification.bullet_type == "exception":
                if node.rule_candidates:
                    node.rule_candidates[-1].exceptions.append(text)
                return

            if not node.rule_candidates:
                rule = SchimmelRuleCandidate(
                    raw_rule_text=text,
                    normalized_rule_text=self.abbreviation_normalizer.normalize_text(text),
                    jurisdiction_scope=jx or "GENERAL",
                    start_page=page_num,
                    end_page=page_num,
                    parse_confidence=0.65,
                )
                node.rule_candidates.append(rule)
            else:
                # Append to existing rule
                existing = node.rule_candidates[-1]
                existing.raw_rule_text = f"{existing.raw_rule_text}\n{text}"
                if existing.normalized_rule_text:
                    existing.normalized_rule_text = f"{existing.normalized_rule_text} {text}"

    def _is_noise(self, text: str) -> bool:
        """Check if text block is noise/footer content."""
        if not text or len(text) < 2:
            return True

        stripped = text.strip()

        # Check common footer patterns
        for pattern in FOOTER_PATTERNS:
            if pattern.match(stripped):
                return True

        if stripped in AUTHOR_FOOTNOTE_MARKERS:
            return True

        # Single page numbers
        if stripped.isdigit() and len(stripped) <= 4:
            return True

        return False

    def _write_subject_json(self, section: SchimmelSubjectSection, path: Path) -> None:
        """Write a subject section to review-friendly JSON."""
        def node_to_dict(node: SchimmelTemplateNodeCandidate) -> dict:
            result = {
                "title": node.title,
                "type": node.node_type,
                "depth": node.depth,
                "page": node.page_number,
                "confidence": node.parse_confidence,
            }
            if node.jurisdiction_scope and node.jurisdiction_scope != "GENERAL":
                result["jurisdiction"] = node.jurisdiction_scope
            if node.rule_candidates:
                result["rule_candidates"] = [
                    {
                        "raw_text": r.raw_rule_text,
                        "normalized_text": r.normalized_rule_text,
                        "elements": r.elements,
                        "exceptions": r.exceptions,
                    }
                    for r in node.rule_candidates
                ]
            if node.cross_references:
                result["cross_references"] = [
                    {
                        "target": cr.target_text,
                        "resolution": cr.resolution_status,
                    }
                    for cr in node.cross_references
                ]
            if node.children:
                result["children"] = [node_to_dict(c) for c in node.children]
            return result

        tree = [node_to_dict(c) for c in section.candidates] if section.candidates else []
        payload = {
            "subject": section.normalized_name,
            "pages": f"{section.start_page}-{section.end_page}",
            "template": {
                "title": f"{section.normalized_name} Essay Template",
                "children": tree,
            },
        }
        write_json(path, payload)

    @staticmethod
    def export_tree_text(subject_name: str, nodes: list[SchimmelTemplateNodeCandidate]) -> str:
        """Export a readable tree view of the template hierarchy."""
        lines: list[str] = [f"\n{subject_name}\n{'=' * len(subject_name)}\n"]

        def walk(node: SchimmelTemplateNodeCandidate, prefix: str = "", is_last: bool = True) -> None:
            connector = "└── " if is_last else "├── "
            node_prefix = f"{prefix}{connector}" if node.depth > 0 else ""
            label = node.title
            if len(label) > 100:
                label = label[:97] + "..."

            jx_suffix = f" [{node.jurisdiction_scope}]" if node.jurisdiction_scope and node.jurisdiction_scope != "GENERAL" else ""
            lines.append(f"{node_prefix}{label}{jx_suffix}")

            # Show rule candidates as sub-bullets
            for rule in node.rule_candidates:
                rule_text = rule.raw_rule_text[:80] + "..." if len(rule.raw_rule_text) > 80 else rule.raw_rule_text
                child_prefix = f"{prefix}    " if is_last else f"{prefix}│   "
                lines.append(f"{child_prefix}  • {rule_text}")
                for elem in rule.elements:
                    lines.append(f"{child_prefix}    - {elem[:60]}")
                for exc in rule.exceptions:
                    lines.append(f"{child_prefix}    ⚠ {exc[:60]}")

            # Show cross-references
            for cr in node.cross_references:
                child_prefix = f"{prefix}    " if is_last else f"{prefix}│   "
                lines.append(f"{child_prefix}  ↪ {cr.target_text}")

            # Recursively add children
            children = node.children
            for i, child in enumerate(children):
                child_is_last = i == len(children) - 1
                child_prefix = f"{prefix}    " if is_last else f"{prefix}│   "
                walk(child, child_prefix, child_is_last)

        for node in nodes:
            walk(node)

        return "\n".join(lines)