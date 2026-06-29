from __future__ import annotations

import re
from statistics import median

from app.parsing.schimmel.models import SchimmelHeadingCandidate, SchimmelTemplateNodeCandidate

# Node type constants
NODE_TYPE_SUBJECT = "SUBJECT"
NODE_TYPE_MAJOR_TOPIC = "MAJOR_TOPIC"
NODE_TYPE_TOPIC = "TOPIC"
NODE_TYPE_ISSUE = "ISSUE"
NODE_TYPE_SUBISSUE = "SUBISSUE"
NODE_TYPE_RULE = "RULE"
NODE_TYPE_ELEMENT = "ELEMENT"
NODE_TYPE_EXCEPTION = "EXCEPTION"
NODE_TYPE_DEFINITION = "DEFINITION"
NODE_TYPE_JURISDICTION_VARIANT = "JURISDICTION_VARIANT"
NODE_TYPE_MAJORITY_RULE = "MAJORITY_RULE"
NODE_TYPE_MINORITY_RULE = "MINORITY_RULE"
NODE_TYPE_CROSS_REFERENCE = "CROSS_REFERENCE"
NODE_TYPE_OTHER = "OTHER"

# Typical major topic indicators (all-caps, multi-word, not a subject)
MAJOR_TOPIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(GENERAL\s+PRINCIPLE|CREATION\s+OF|LIABILITY\s+OF|FORMATION\s+OF|ELEMENTS\s+OF)"),
    re.compile(r"^[A-Z\s]{10,80}$"),
]

# Issue-level indicators: Title Case, short phrases
ISSUE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^[A-Z][a-z]+\s+[A-Z][a-z]+"),
]


class SchimmelHeadingClassifier:
    """Classifies headings into node types based on typography and content."""

    def __init__(self) -> None:
        self.base_font_size: float = 10.0

    def classify(
        self,
        text: str,
        font_size: float = 10.0,
        is_bold: bool = False,
        is_all_caps: bool = False,
        indent_level: int = 0,
        page_number: int = 1,
    ) -> SchimmelHeadingCandidate:
        """Classify a heading into a node type with evidence-based scoring."""
        evidence: dict = {
            "font_size": font_size,
            "is_bold": is_bold,
            "is_all_caps": is_all_caps,
            "indent_level": indent_level,
            "text_length": len(text),
        }

        heading_score, inferred_level, node_type = self._score_heading(
            text, font_size, is_bold, is_all_caps, indent_level, evidence
        )

        return SchimmelHeadingCandidate(
            text=text,
            raw_text=text,
            page_number=page_number,
            heading_score=heading_score,
            inferred_level=inferred_level,
            node_type=node_type,
            evidence=evidence,
        )

    def classify_block(self, block: dict) -> SchimmelHeadingCandidate:
        """Classify a layout block as a heading."""
        text = (block.get("text", "") or "").strip()
        font_sizes = block.get("font_sizes", [10.0])
        font_size = max(font_sizes) if font_sizes else 10.0
        is_bold = block.get("is_bold", False)
        is_all_caps = text.isupper() if text else False
        indent_level = self._estimate_indent(block)
        page_number = block.get("page_number", 1)

        return self.classify(text, font_size, is_bold, is_all_caps, indent_level, page_number)

    def _estimate_indent(self, block: dict) -> int:
        """Estimate indent level based on bbox x0."""
        bbox = block.get("bbox")
        if bbox and len(bbox) >= 1:
            x0 = bbox[0]
            if x0 > 200:
                return 3
            if x0 > 120:
                return 2
            if x0 > 50:
                return 1
        return 0

    def _score_heading(
        self,
        text: str,
        font_size: float,
        is_bold: bool,
        is_all_caps: bool,
        indent_level: int,
        evidence: dict,
    ) -> tuple[float, int, str]:
        """Score and classify a heading."""
        # Default low score
        score = 0.3
        level = 4
        node_type = NODE_TYPE_OTHER

        if not text or len(text) < 2:
            return 0.0, -1, NODE_TYPE_OTHER

        # Subject level (all caps, large font, known subject)
        if is_all_caps and is_bold and font_size > self.base_font_size + 4:
            score = 0.95
            level = 1
            node_type = NODE_TYPE_SUBJECT
            evidence["reason"] = "all_caps_bold_large_font"
            return score, level, node_type

        # Major topic (all caps, bold, slightly smaller than subject)
        if is_all_caps and font_size >= self.base_font_size + 1 and len(text) > 8:
            score = 0.85
            level = 2
            node_type = NODE_TYPE_MAJOR_TOPIC
            evidence["reason"] = "all_caps_medium_font"
            return score, level, node_type

        # Topic level (bold, title case, medium font above base)
        if is_bold and font_size > self.base_font_size and not is_all_caps and len(text) <= 100:
            score = 0.80
            level = 3
            node_type = NODE_TYPE_TOPIC
            evidence["reason"] = "bold_title_case"
            return score, level, node_type

        # Subissue (bold + deeply indented, or small font + indented)
        if is_bold and indent_level >= 2 and len(text) <= 100:
            score = 0.70
            level = 5
            node_type = NODE_TYPE_SUBISSUE
            evidence["reason"] = "bold_indented_short_text"
            return score, level, node_type

        # Issue level (bold, smaller font)
        if is_bold and len(text) <= 120:
            score = 0.75
            level = 4
            node_type = NODE_TYPE_ISSUE
            evidence["reason"] = "bold_short_text"
            return score, level, node_type

        # Subissue (indented, not bold)
        if indent_level >= 2 and len(text) <= 100:
            score = 0.65
            level = 5
            node_type = NODE_TYPE_SUBISSUE
            evidence["reason"] = "indented_short_text"
            return score, level, node_type

        # Rule text (longer, not bold)
        if len(text) > 80:
            score = 0.50
            level = 5
            node_type = NODE_TYPE_RULE
            evidence["reason"] = "long_text"
            return score, level, node_type

        # Element (bullet starting text)
        if indent_level >= 1 and len(text) <= 120:
            score = 0.60
            level = 5
            node_type = NODE_TYPE_ELEMENT
            evidence["reason"] = "indented_medium_text"
            return score, level, node_type

        # Lower confidence for other patterns
        if len(text) <= 60:
            score = 0.40
            level = 5
            node_type = NODE_TYPE_OTHER
            evidence["reason"] = "short_low_confidence"

        return score, level, node_type

    def detect_jurisdiction_variant(self, text: str) -> str | None:
        """Detect jurisdiction from text like 'California', 'Common Law', 'UCC'."""
        lowered = text.casefold()
        if "california" in lowered:
            return "CALIFORNIA"
        if "common law" in lowered:
            return "COMMON_LAW"
        if "ucc" in lowered or "uniform commercial code" in lowered:
            return "UCC"
        if "federal" in lowered:
            return "FEDERAL"
        if "majority" in lowered:
            return "MAJORITY"
        if "minority" in lowered:
            return "MINORITY"
        if "traditional" in lowered or "modern" in lowered:
            return "TRADITIONAL_MODERN"
        return None

    def detect_rule_variant(self, text: str) -> str | None:
        """Detect rule variant from text."""
        lowered = text.casefold()
        if "mirror image" in lowered:
            return "MIRROR_IMAGE_RULE"
        if "battle of the forms" in lowered:
            return "BATTLE_OF_THE_FORMS"
        if "perfect tender" in lowered:
            return "PERFECT_TENDER_RULE"
        return None