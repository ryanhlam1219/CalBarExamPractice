from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


BULLET_PATTERN = re.compile(r"^\s*(?:\d+[.)]|\(?\d+\)|[a-z][.)]|\(?[a-z]\)|[-*•])\.?\s+")
NUMBERED_ELEMENT_PATTERN = re.compile(r"^\s*\(\d+\)\s+", re.MULTILINE)
LIST_ITEM_PATTERN = re.compile(r"^\s*[-•]\s+")


@dataclass
class BulletClassification:
    """Classification result for a bullet point or element."""
    bullet_type: str  # "element", "exception", "example", "subissue", "consequence", "definition", "rule_variant", "list_item"
    text: str
    raw_text: str
    confidence: float = 0.0
    label: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class SchimmelBulletClassifier:
    """Classifies bullet points into their semantic meaning."""

    # Indicator words for elements of a rule/test
    ELEMENT_INDICATORS: set[str] = {
        "elements", "requirements", "factors", "prerequisites",
        "must show", "requires", "involves",
    }

    # Patterns for numbered elements like "(1) Manifest intent"
    NUMBERED_ELEMENT_RE = re.compile(r"^\s*\(\d+\)\s+")
    PAREN_LETTER_RE = re.compile(r"^\s*\([a-z]\)\s+")
    ROMAN_RE = re.compile(r"^\s*[ivx]+\.\s+")

    def classify(self, text: str, context: str | None = None) -> BulletClassification:
        """Classify a single bullet line."""
        stripped = text.strip()

        # Check for numbered elements: "(1) ..."
        if self.NUMBERED_ELEMENT_RE.match(stripped) or self.PAREN_LETTER_RE.match(stripped):
            return BulletClassification(
                bullet_type="element",
                text=re.sub(r"^\s*\(?[a-z0-9]+\)?\s+", "", stripped),
                raw_text=stripped,
                confidence=0.85,
                label=re.match(r"^\s*\(?[a-z0-9]+\)?", stripped).group(0).strip() if re.match(r"^\s*\(?[a-z0-9]+\)?", stripped) else None,
                evidence={"format": "numbered_element"},
            )

        # Check for bullet list items: "- text" or "• text"
        if LIST_ITEM_PATTERN.match(stripped):
            # Look for contextual clues
            lowered = stripped.casefold()

            if lowered.startswith("except") or "exception" in lowered:
                return BulletClassification(
                    bullet_type="exception",
                    text=re.sub(r"^\s*[-•]\s+", "", stripped),
                    raw_text=stripped,
                    confidence=0.80,
                    evidence={"keyword": "exception"},
                )

            if lowered.startswith("e.g.") or lowered.startswith("example") or lowered.startswith("for example"):
                return BulletClassification(
                    bullet_type="example",
                    text=re.sub(r"^\s*[-•]\s+", "", stripped),
                    raw_text=stripped,
                    confidence=0.80,
                    evidence={"keyword": "example"},
                )

            # Default: treat as list item / subissue
            return BulletClassification(
                bullet_type="list_item",
                text=re.sub(r"^\s*[-•]\s+", "", stripped),
                raw_text=stripped,
                confidence=0.60,
                evidence={"format": "bullet_list"},
            )

        # Check for lines starting with keywords
        lowered = stripped.casefold()
        if lowered.startswith("exception"):
            return BulletClassification(
                bullet_type="exception",
                text=re.sub(r"^exception[s]?\s*[:.-]\s*", "", stripped, flags=re.IGNORECASE).strip(),
                raw_text=stripped,
                confidence=0.85,
                evidence={"keyword": "Exception"},
            )

        if lowered.startswith(("definition", "defined")):
            return BulletClassification(
                bullet_type="definition",
                text=re.sub(r"^definition[s]?\s*[:.-]\s*", "", stripped, flags=re.IGNORECASE).strip(),
                raw_text=stripped,
                confidence=0.85,
                evidence={"keyword": "Definition"},
            )

        if lowered.startswith("note") or lowered.startswith("tip") or lowered.startswith("exam tip"):
            return BulletClassification(
                bullet_type="definition",  # exam tips
                text=re.sub(r"^(?:exam\s+)?tips?\s*[:.-]\s*", "", stripped, flags=re.IGNORECASE).strip(),
                raw_text=stripped,
                confidence=0.75,
                evidence={"keyword": "tip"},
            )

        # Unclassified bullet
        return BulletClassification(
            bullet_type="list_item",
            text=stripped,
            raw_text=stripped,
            confidence=0.40,
            evidence={"format": "unclassified"},
        )

    def is_element_list(self, text: str) -> bool:
        """Check if text appears to be an element list with numbered items."""
        return bool(self.NUMBERED_ELEMENT_RE.search(text))

    def extract_elements(self, text: str) -> list[str]:
        """Extract individual elements from a numbered list text."""
        items = []
        for line in text.split("\n"):
            stripped = line.strip()
            if self.NUMBERED_ELEMENT_RE.match(stripped):
                items.append(re.sub(r"^\s*\(\d+\)\s+", "", stripped))
            elif LIST_ITEM_PATTERN.match(stripped) and items:
                # Continuation
                bullet_pattern_part = r"^\s*[-•]\s+"
                items[-1] = f"{items[-1]} {re.sub(bullet_pattern_part, '', stripped)}"
        return items