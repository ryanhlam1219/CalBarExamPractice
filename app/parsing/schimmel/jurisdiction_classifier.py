from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


JURISDICTION_PATTERNS: list[tuple[str, str, float]] = [
    ("california", "CALIFORNIA", 0.95),
    ("federal", "FEDERAL", 0.90),
    ("common law", "COMMON_LAW", 0.95),
    ("ucc", "UCC", 0.95),
    ("uniform commercial code", "UCC", 0.95),
    ("majority rule", "MAJORITY", 0.90),
    ("majority", "MAJORITY", 0.70),
    ("minority rule", "MINORITY", 0.90),
    ("minority", "MINORITY", 0.70),
    ("traditional rule", "TRADITIONAL", 0.90),
    ("traditional", "TRADITIONAL", 0.70),
    ("modern rule", "MODERN", 0.90),
    ("modern", "MODERN", 0.70),
    ("utc", "UTC", 0.95),
    ("uniform trust code", "UTC", 0.95),
    ("model penal code", "MODEL_PENAL_CODE", 0.90),
]

RULE_VARIANT_PATTERNS: list[tuple[str, str, float]] = [
    ("mirror image rule", "MIRROR_IMAGE_RULE", 0.95),
    ("battle of the forms", "BATTLE_OF_THE_FORMS", 0.95),
    ("perfect tender rule", "PERFECT_TENDER_RULE", 0.95),
    ("mailbox rule", "MAILBOX_RULE", 0.95),
    ("parol evidence rule", "PAROL_EVIDENCE_RULE", 0.95),
    ("statute of frauds", "STATUTE_OF_FRAUDS", 0.95),
]


@dataclass
class JurisdictionContext:
    """The active jurisdiction context at a given point in the document."""
    jurisdiction: str | None = None
    rule_variant: str | None = None
    inherited_from: str | None = None  # "parent", "heading", "explicit"


class SchimmelJurisdictionClassifier:
    """Classifies jurisdiction and rule variants within template content."""

    def __init__(self) -> None:
        self.context_stack: list[JurisdictionContext] = []
        self.default_context = JurisdictionContext(jurisdiction="GENERAL")

    def detect_jurisdiction(self, text: str) -> tuple[str | None, float]:
        """Detect jurisdiction scope from text."""
        lowered = text.casefold()
        for pattern, jurisdiction, confidence in JURISDICTION_PATTERNS:
            if pattern in lowered:
                return jurisdiction, confidence
        return None, 0.0

    def detect_rule_variant(self, text: str) -> tuple[str | None, float]:
        """Detect rule variant from text."""
        lowered = text.casefold()
        for pattern, variant, confidence in RULE_VARIANT_PATTERNS:
            if pattern in lowered:
                return variant, confidence
        return None, 0.0

    def push_context(self, text: str) -> JurisdictionContext:
        """Push a new jurisdiction context based on text."""
        jx, jx_conf = self.detect_jurisdiction(text)
        variant, var_conf = self.detect_rule_variant(text)

        # Inherit from parent context if no explicit jurisdiction
        parent = self.context_stack[-1] if self.context_stack else None
        if jx is None and parent is not None:
            jx = parent.jurisdiction
            jx_conf = 0.5

        ctx = JurisdictionContext(
            jurisdiction=jx or "GENERAL",
            rule_variant=variant,
            inherited_from="explicit" if jx else ("parent" if parent else "default"),
        )
        self.context_stack.append(ctx)
        return ctx

    def pop_context(self) -> JurisdictionContext | None:
        """Pop the current jurisdiction context."""
        if self.context_stack:
            return self.context_stack.pop()
        return None

    def current_context(self) -> JurisdictionContext:
        """Get the current jurisdiction context."""
        if self.context_stack:
            return self.context_stack[-1]
        return self.default_context

    def reset(self) -> None:
        """Reset the context stack."""
        self.context_stack = []