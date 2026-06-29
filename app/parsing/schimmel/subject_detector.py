from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.parsing.schimmel.models import SchimmelHeadingCandidate, SchimmelSubjectSection


SUBJECT_ALIASES: dict[str, str] = {
    "AGENCY": "Agency",
    "CIVIL PROCEDURE": "Civil Procedure",
    "CIV PRO": "Civil Procedure",
    "COMMUNITY PROPERTY": "Community Property",
    "CONSTITUTIONAL LAW": "Constitutional Law",
    "CONTRACTS": "Contracts",
    "CORPORATIONS": "Corporations",
    "CRIMINAL LAW": "Criminal Law",
    "CRIM PRO": "Criminal Procedure",
    "CRIMINAL PROCEDURE": "Criminal Procedure",
    "EVIDENCE": "Evidence",
    "LEGAL REMEDIES": "Legal Remedies",
    "PARTNERSHIPS": "Partnerships",
    "PROFESSIONAL RESPONSIBILITY": "Professional Responsibility",
    "REAL PROPERTY": "Real Property",
    "REMEDIES": "Remedies",
    "TORTS": "Torts",
    "TRUSTS": "Trusts",
    "WILLS": "Wills",
}

# Known major topics that might look like subjects but aren't
NON_SUBJECT_HEADINGS: set[str] = {
    "CREATION OF AN AGENCY RELATIONSHIP",
    "LIABILITY OF PRINCIPAL AND AGENT TO OTHERS",
    "UNDERLYING CAUSE OF ACTION",
    "GENERAL COMMUNITY PROPERTY PRINCIPLES",
    "MARRIED AND UNMARRIED COUPLES",
    "SPECIFIC TYPES OF PROPERTY",
    "SPECIAL RULES",
    "GENERAL PRINCIPALS",
    "CRIMES AGAINST THE PERSON",
    "CRIMES AGAINST PROPERTY",
    "INCHOATE CRIMES",
    "PARTY LIABILITY",
    "JUSTIFICATIONS",
    "FORMATION AND MANAGEMENT",
    "RELATIONSHIPS WITH THIRD PARTIES",
    "PARTNERSHIP CHANGES AND TERMINATION",
    "OTHER PARTNERSHIP FORMS",
    "LAWYER-CLENT RELATIONSHIP",
    "SCOPE OF LAWYER-CLIENT RELATIONSHIP",
    "PROFESSIONAL INTEGRITY",
    "ADVERTISING AND SOLICITATION",
    "LANDLORD AND TENANT",
    "GOVERNMENT REGULATION AND ZONING",
    "THE LAND SALE CONTRACT",
    "LIMITATIONS ON RECOVERY",
    "EQUITABLE REMEDIES",
    "EQUITABLE DEFENSES",
    "RESTITUTION REMEDIES",
    "MISCELLANEOUS CONCEPTS",
    "SURVIVAL ACTIONS",
    "DISTRIBUTION",
    "OTHER INTESTATE ISSUES",
    "TRUST MODIFICATION AND TERMINATION",
    "TRUSTEE'S POWERS, DUTIES AND REMEDIES AGAINST TRUSTEE",
    "TRUSTEE'S POWERS",
    "TRUSTEE'S DUTIES",
    "REMEDIES AGAINST TRUSTEE",
    "CHALLENGES TO VALIDITY",
    "REVOCATION OF WILLS",
    "REVIVAL OF REVOKED WILLS",
    "TERMS AND COMPONENTS OF WILLS",
    "BARS TO SUCCESSION",
    "INTESTATE DISTRIBUTION",
    # Compound headings that look like subjects but aren't
    "CONTRACTS AND TORTS",
    "CONTRACTS AND TORTS – RESTITUTION",
    "PLEADINGS AND MOTIONS",
    "REMOVAL AND REMAND",
    "ERIE DOCTRINE AND CHOICE OF LAW",
}


@dataclass
class SubjectBoundary:
    subject_name: str
    normalized_name: str
    start_page: int
    start_block_index: int
    end_page: int
    end_block_index: int
    raw_heading: str
    heading_blocks: list[dict[str, Any]] = field(default_factory=list)


class SchimmelSubjectDetector:
    """Detects subject boundaries from extracted layout blocks."""

    def __init__(self, subject_aliases: dict[str, str] | None = None) -> None:
        self.aliases = subject_aliases or SUBJECT_ALIASES
        self.known_subjects = set(self.aliases.keys())

    def detect_subjects(
        self,
        blocks_by_page: dict[int, list[dict[str, Any]]],
    ) -> list[SubjectBoundary]:
        """Detect subject sections across all pages."""
        candidate_blocks: list[tuple[int, int, str, dict[str, Any]]] = []

        for page_num in sorted(blocks_by_page.keys()):
            blocks = blocks_by_page[page_num]
            for block_idx, block in enumerate(blocks):
                block_text = (block.get("text", "") or "").strip()
                # Check each line in the block for a subject heading
                for line in block_text.splitlines():
                    text = line.strip()
                    if not text:
                        continue
                    if self._is_subject_heading(text, block):
                        candidate_blocks.append((page_num, block_idx, text, block))
                        break  # Only one subject per block

        boundaries = self._merge_boundaries(candidate_blocks)
        return boundaries

    def _is_subject_heading(self, text: str, block: dict[str, Any]) -> bool:
        """Check if a block text is a subject heading.

        Real subject headings in the Schimmel PDF appear as the first block
        near the top of a page.  The same word (e.g. "CONTRACTS") can appear
        mid-page as a subsection heading inside another subject — those must
        be rejected.
        """
        if not text or len(text) < 3 or len(text) > 80:
            return False

        if not text.isupper():
            return False

        upper_text = text.upper()
        if upper_text in NON_SUBJECT_HEADINGS:
            return False

        if upper_text not in self.known_subjects and upper_text not in self.aliases:
            return False

        # Must be near the top of the page — real subject headings start
        # at y0 ~ 72.5.  Sub-headings within another subject appear lower.
        bbox = block.get("bbox")
        y0 = bbox[1] if bbox and len(bbox) > 1 else 999.0

        if y0 > 150:
            return False

        return True

    def _merge_boundaries(
        self, candidates: list[tuple[int, int, str, dict[str, Any]]]
    ) -> list[SubjectBoundary]:
        """Merge candidate blocks into subject boundaries."""
        boundaries: list[SubjectBoundary] = []

        for idx, (page_num, block_idx, text, block) in enumerate(candidates):
            normalized = self.aliases.get(text, text.title())

            boundaries.append(
                SubjectBoundary(
                    subject_name=text,
                    normalized_name=normalized,
                    start_page=page_num,
                    start_block_index=block_idx,
                    end_page=page_num,
                    end_block_index=-1,
                    raw_heading=text,
                    heading_blocks=[block],
                )
            )

        # Each subject ends at the page before the next subject starts
        for i in range(len(boundaries) - 1):
            boundaries[i].end_page = boundaries[i + 1].start_page - 1

        return boundaries

    def build_subject_sections(
        self,
        boundaries: list[SubjectBoundary],
        blocks_by_page: dict[int, list[dict[str, Any]]],
    ) -> list[SchimmelSubjectSection]:
        """Convert boundaries to sections with heading candidates."""
        sections: list[SchimmelSubjectSection] = []
        for boundary in boundaries:
            heading_candidate = SchimmelHeadingCandidate(
                text=boundary.normalized_name,
                raw_text=boundary.raw_heading,
                page_number=boundary.start_page,
                heading_score=0.95,
                inferred_level=0,
                node_type="SUBJECT",
                evidence={
                    "all_caps": True,
                    "is_known_subject": True,
                    "page_position": boundary.start_page,
                    "heading_blocks_count": len(boundary.heading_blocks),
                },
            )
            sections.append(
                SchimmelSubjectSection(
                    subject_name=boundary.subject_name,
                    normalized_name=boundary.normalized_name,
                    start_page=boundary.start_page,
                    end_page=boundary.end_page,
                    raw_heading=boundary.raw_heading,
                    heading_candidate=heading_candidate,
                    heading_blocks=boundary.heading_blocks,
                )
            )
        return sections

    def classify_unknown_heading(self, text: str) -> str | None:
        """Return the normalized subject name if this is an unknown subject, or None."""
        text_upper = text.strip().upper()
        if text_upper in self.aliases:
            return self.aliases[text_upper]
        return None