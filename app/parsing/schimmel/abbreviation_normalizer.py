from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.parsing.schimmel.models import SchimmelAbbreviationCandidate


# Globally safe abbreviations (same meaning regardless of subject)
GLOBAL_ABBREVIATIONS: dict[str, str] = {
    "rx": "reasonable",
    "rxly": "reasonably",
    "fx": "foreseeable",
    "K": "contract",
    "Ks": "contracts",
    "3P": "third party",
    "3Ps": "third parties",
    "3PD": "third-party defendant",
    "COA": "cause of action",
    "COAs": "causes of action",
    "LLC": "limited liability company",
    "LLP": "limited liability partnership",
    "SMJ": "subject matter jurisdiction",
    "PJ": "personal jurisdiction",
    "AIC": "amount in controversy",
    "FMV": "fair market value",
    "SOF": "statute of frauds",
    "SOL": "statute of limitations",
    "SOLs": "statutes of limitations",
    "REOP": "reasonable expectation of privacy",
    "FOPT": "fruit of the poisonous tree",
    "CP": "community property",
    "SP": "separate property",
    "QCP": "quasi-community property",
    "PPB": "principal place of business",
    "ROS": "right of survivorship",
    "FMV": "fair market value",
    "FMV": "fair market value",
}

# Subject-ambiguous abbreviations
SUBJECT_AMBIGUOUS_ABBREVIATIONS: dict[str, dict[str, str]] = {
    "A": {
        "default": "agent",
        "Agency": "agent",
        "Criminal Law": "accomplice",
        "Corporations": "agent",
        "Professional Responsibility": "attorney",
    },
    "P": {
        "default": "plaintiff",
        "Agency": "principal",
        "Contracts": "plaintiff",
        "Civil Procedure": "plaintiff",
        "Criminal Law": "prosecution",
        "Criminal Procedure": "prosecution",
    },
    "D": {
        "default": "defendant",
        "Criminal Law": "defendant",
        "Criminal Procedure": "defendant",
        "Contracts": "defendant",
        "Civil Procedure": "defendant",
        "Agency": "defendant",
    },
    "L": {
        "default": "lawyer",
        "Professional Responsibility": "lawyer",
        "Evidence": "lawyer",
    },
    "C": {
        "default": "client",
        "Professional Responsibility": "client",
        "Contracts": "counterparty",
    },
    "rx": {
        "default": "reasonable",
        "Torts": "reasonable",
        "Contracts": "reasonable",
        "Criminal Procedure": "reasonable",
        "Criminal Law": "reasonable",
    },
}


class SchimmelAbbreviationNormalizer:
    """Detects and normalizes abbreviations in the Schimmel document."""

    def __init__(self) -> None:
        self.detected_abbreviations: dict[str, SchimmelAbbreviationCandidate] = {}
        self.current_subject: str | None = None

    def set_current_subject(self, subject: str | None) -> None:
        """Set the current subject for ambiguous abbreviation resolution."""
        self.current_subject = subject

    def normalize_text(self, text: str) -> str:
        """Normalize abbreviations in text. Preserves raw text in parallel."""
        normalized = text[:]  # Make a mutable copy

        # First, handle multi-word abbreviations to avoid partial matches
        for abbr, expanded in sorted(GLOBAL_ABBREVIATIONS.items(), key=lambda x: -len(x[0])):
            pattern = re.compile(rf'\b{re.escape(abbr)}\b', re.IGNORECASE)
            if pattern.search(normalized):
                self._record_abbreviation(abbr, expanded, confidence=0.95, subject_specific=False)
                normalized = pattern.sub(expanded, normalized)

        return normalized

    def normalize_subject_specific(self, text: str, subject: str) -> str:
        """Normalize subject-ambiguous abbreviations."""
        normalized = text[:]
        for abbr, subject_map in SUBJECT_AMBIGUOUS_ABBREVIATIONS.items():
            expanded = subject_map.get(subject, subject_map.get("default", abbr))
            pattern = re.compile(rf'\b{re.escape(abbr)}\b', re.IGNORECASE)
            if pattern.search(normalized):
                self._record_abbreviation(
                    abbr, expanded,
                    confidence=0.85,
                    subject_specific=True,
                    subject=subject,
                )
                normalized = pattern.sub(expanded, normalized)
        return normalized

    def detect_abbreviations(self, text: str, subject: str | None = None) -> list[SchimmelAbbreviationCandidate]:
        """Scan text for potential abbreviations (short uppercase sequences)."""
        candidates: list[SchimmelAbbreviationCandidate] = []

        # Find sequences of 2-5 uppercase letters that might be abbreviations
        for match in re.finditer(r'\b([A-Z]{2,5})\b', text):
            abbr = match.group(1)
            if abbr not in GLOBAL_ABBREVIATIONS and abbr not in SUBJECT_AMBIGUOUS_ABBREVIATIONS:
                # Unknown abbreviation
                candidates.append(
                    SchimmelAbbreviationCandidate(
                        abbreviation=abbr,
                        normalized_term=abbr,
                        context_notes="Unknown abbreviation, needs review",
                        confidence=0.3,
                        review_status="NEEDS_REVIEW",
                    )
                )

        return candidates

    def get_all_candidates(self) -> list[SchimmelAbbreviationCandidate]:
        """Get all abbreviation candidates detected so far."""
        return list(self.detected_abbreviations.values())

    def _record_abbreviation(
        self,
        abbreviation: str,
        normalized_term: str,
        confidence: float = 0.9,
        subject_specific: bool = False,
        subject: str | None = None,
    ) -> None:
        """Record a detected abbreviation."""
        key = f"{abbreviation}:{subject if subject_specific else ''}"
        if key not in self.detected_abbreviations:
            self.detected_abbreviations[key] = SchimmelAbbreviationCandidate(
                abbreviation=abbreviation,
                normalized_term=normalized_term,
                context_notes=f"{'Subject-specific' if subject_specific else 'Global'} abbreviation",
                confidence=confidence,
                review_status="AUTO_ACCEPTED" if confidence >= 0.9 else "NEEDS_REVIEW",
            )

    def reset(self) -> None:
        """Reset the normalizer state."""
        self.detected_abbreviations = {}
        self.current_subject = None