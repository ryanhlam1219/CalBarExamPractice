from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SchimmelDocumentCandidate:
    """Intermediate representation of the full parsed Schimmel document."""
    source_path: str
    sha256: str
    page_count: int
    subjects: list[SchimmelSubjectSection] = field(default_factory=list)
    abbreviations: list[SchimmelAbbreviationCandidate] = field(default_factory=list)
    validation_findings: list[SchimmelValidationFinding] = field(default_factory=list)


@dataclass
class SchimmelSubjectSection:
    """A single subject section with its heading and page range."""
    subject_name: str
    normalized_name: str
    start_page: int
    end_page: int
    raw_heading: str
    heading_candidate: SchimmelHeadingCandidate | None = None
    heading_blocks: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[SchimmelTemplateNodeCandidate] = field(default_factory=list)


@dataclass
class SchimmelHeadingCandidate:
    """Evidence-based heading classification."""
    text: str
    raw_text: str
    page_number: int
    heading_score: float = 0.0
    inferred_level: int = 0
    node_type: str = "OTHER"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class SchimmelTemplateNodeCandidate:
    """A single node in the template hierarchy."""
    title: str
    raw_text: str | None = None
    normalized_text: str | None = None
    node_type: str = "OTHER"
    depth: int = 0
    display_order: int = 0
    page_number: int = 1
    end_page: int = 1
    parse_confidence: float = 0.0
    jurisdiction_scope: str | None = None
    rule_variant: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    children: list[SchimmelTemplateNodeCandidate] = field(default_factory=list)
    rule_candidates: list[SchimmelRuleCandidate] = field(default_factory=list)
    cross_references: list[SchimmelCrossReferenceCandidate] = field(default_factory=list)


@dataclass
class SchimmelRuleCandidate:
    """A concise rule statement extracted from a template node."""
    raw_rule_text: str
    normalized_rule_text: str | None = None
    jurisdiction_scope: str = "GENERAL"
    rule_variant: str | None = None
    start_page: int = 1
    end_page: int = 1
    parse_confidence: float = 0.0
    elements: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)


@dataclass
class SchimmelCrossReferenceCandidate:
    """A cross-reference found within template content."""
    target_text: str
    source_page: int
    resolution_status: str = "UNRESOLVED"
    parse_confidence: float = 0.0
    resolved_target_node_id: int | None = None
    resolved_subject_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SchimmelAbbreviationCandidate:
    """An abbreviation definition extracted from the document."""
    abbreviation: str
    normalized_term: str
    context_notes: str | None = None
    confidence: float = 0.0
    review_status: str = "UNREVIEWED"
    legal_subject_id: int | None = None


@dataclass
class SchimmelValidationFinding:
    """A validation finding for review."""
    severity: str  # "error", "warning", "info"
    code: str
    message: str
    subject: str | None = None
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)