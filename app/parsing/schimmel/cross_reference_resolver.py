from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.parsing.schimmel.models import SchimmelCrossReferenceCandidate, SchimmelValidationFinding


CROSS_REFERENCE_PATTERNS: list[re.Pattern] = [
    re.compile(r"see\s+above", re.IGNORECASE),
    re.compile(r"see\s+(?:the\s+)?(\w+(?:\s+\w+)?)\s+(?:template|section|discussion)", re.IGNORECASE),
    re.compile(r"See\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:cross[- ]?reference|cf\.?|compare)", re.IGNORECASE),
]

SAME_SUBJECT_REFERENCES = re.compile(r"see\s+above\.?", re.IGNORECASE)
CROSS_SUBJECT_REFERENCES = re.compile(r"see\s+(.+?)\s+template\.?", re.IGNORECASE)
SPECIFIC_TOPIC_REFERENCES = re.compile(r"see\s+(.+?)(?:\.|$)", re.IGNORECASE)


class SchimmelCrossReferenceResolver:
    """Detects and resolves cross-references within and across subjects."""

    def __init__(self) -> None:
        self.known_subjects: set[str] = set()
        self.subject_nodes: dict[str, list[int]] = {}  # subject -> list of node IDs

    def detect_cross_references(self, text: str, page_number: int) -> list[SchimmelCrossReferenceCandidate]:
        """Detect cross-references in text."""
        candidates: list[SchimmelCrossReferenceCandidate] = []

        # "See above" references
        if SAME_SUBJECT_REFERENCES.search(text):
            candidates.append(
                SchimmelCrossReferenceCandidate(
                    target_text="See above.",
                    source_page=page_number,
                    resolution_status="UNRESOLVED",
                    parse_confidence=0.70,
                    metadata={"type": "same_subject", "pattern": "see_above"},
                )
            )

        # Cross-subject references: "See Wills Template"
        for match in CROSS_SUBJECT_REFERENCES.finditer(text):
            target = match.group(1).strip()
            candidates.append(
                SchimmelCrossReferenceCandidate(
                    target_text=f"See {target} Template.",
                    source_page=page_number,
                    resolution_status="NEEDS_REVIEW",
                    parse_confidence=0.60,
                    metadata={"type": "cross_subject", "target_subject": target},
                )
            )

        # General "See X" references
        for match in SPECIFIC_TOPIC_REFERENCES.finditer(text):
            target = match.group(1).strip()
            # Skip "See above" since already handled
            if target.casefold() == "above":
                continue
            # Skip if already caught by cross-subject pattern
            if "template" in target.casefold():
                continue
            candidates.append(
                SchimmelCrossReferenceCandidate(
                    target_text=target,
                    source_page=page_number,
                    resolution_status="NEEDS_REVIEW",
                    parse_confidence=0.40,
                    metadata={"type": "specific_topic"},
                )
            )

        return candidates

    def resolve_same_subject(self, candidate: SchimmelCrossReferenceCandidate, subject: str) -> SchimmelCrossReferenceCandidate:
        """Try to resolve a same-subject cross-reference."""
        if candidate.resolution_status == "RESOLVED":
            return candidate

        if "above" in candidate.target_text.casefold():
            candidate.resolution_status = "NEEDS_REVIEW"
            candidate.parse_confidence = 0.50
            candidate.metadata["resolution_note"] = "Same-subject 'see above' - resolves to previous node(s)"
            return candidate

        return candidate

    def resolve_cross_subject(
        self,
        candidate: SchimmelCrossReferenceCandidate,
        known_subject_names: set[str],
    ) -> SchimmelCrossReferenceCandidate:
        """Try to resolve a cross-subject reference."""
        target_subject = candidate.metadata.get("target_subject", "")
        if target_subject in known_subject_names:
            candidate.resolution_status = "AUTO_RESOLVED"
            candidate.parse_confidence = 0.85
            candidate.metadata["resolved_subject"] = target_subject
        else:
            candidate.resolution_status = "NEEDS_REVIEW"
            candidate.parse_confidence = 0.40
            candidate.metadata["unresolved_target"] = target_subject

        return candidate