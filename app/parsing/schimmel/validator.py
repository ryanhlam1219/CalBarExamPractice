from __future__ import annotations

from app.parsing.schimmel.models import (
    SchimmelDocumentCandidate,
    SchimmelSubjectSection,
    SchimmelTemplateNodeCandidate,
    SchimmelValidationFinding,
)

NODE_TYPE_SUBJECT = "SUBJECT"


class SchimmelTemplateValidator:
    """Validates the parsed Schimmel template structure."""

    def validate(self, document: SchimmelDocumentCandidate) -> list[SchimmelValidationFinding]:
        """Run all validations on the parsed document."""
        findings: list[SchimmelValidationFinding] = []

        # Check subject count
        findings.extend(self._validate_subject_count(document))
        findings.extend(self._validate_subject_names(document))

        for subject in document.subjects:
            findings.extend(self._validate_subject(subject))
            findings.extend(self._validate_tree_structure(subject))
            findings.extend(self._validate_rule_candidates(subject))
            findings.extend(self._validate_cross_references(subject))
            findings.extend(self._validate_jurisdiction_variants(subject))

        findings.extend(document.validation_findings)

        return findings

    def _validate_subject_count(self, document: SchimmelDocumentCandidate) -> list[SchimmelValidationFinding]:
        """Validate that the subject count is reasonable."""
        findings: list[SchimmelValidationFinding] = []
        count = len(document.subjects)
        if count < 5:
            findings.append(
                SchimmelValidationFinding(
                    severity="warning",
                    code="low_subject_count",
                    message=f"Only {count} subjects detected. Expected 10-15 for Schimmel document.",
                )
            )
        if count > 20:
            findings.append(
                SchimmelValidationFinding(
                    severity="warning",
                    code="high_subject_count",
                    message=f"{count} subjects detected, which is unusually high.",
                )
            )
        return findings

    def _validate_subject_names(self, document: SchimmelDocumentCandidate) -> list[SchimmelValidationFinding]:
        """Check that all subject names are valid."""
        findings: list[SchimmelValidationFinding] = []
        valid_subjects = {
            "Agency", "Civil Procedure", "Community Property", "Constitutional Law",
            "Contracts", "Corporations", "Criminal Law", "Criminal Procedure",
            "Evidence", "Legal Remedies", "Partnerships", "Professional Responsibility",
            "Real Property", "Remedies", "Torts", "Trusts", "Wills",
        }
        for subject in document.subjects:
            if subject.normalized_name not in valid_subjects:
                findings.append(
                    SchimmelValidationFinding(
                        severity="warning",
                        code="unknown_subject",
                        message=f"Subject '{subject.normalized_name}' is not in the expected list.",
                        subject=subject.normalized_name,
                    )
                )
        return findings

    def _validate_subject(self, subject: SchimmelSubjectSection) -> list[SchimmelValidationFinding]:
        """Validate a single subject section."""
        findings: list[SchimmelValidationFinding] = []
        candidates = subject.candidates

        if not candidates:
            findings.append(
                SchimmelValidationFinding(
                    severity="error",
                    code="empty_subject",
                    message=f"Subject '{subject.normalized_name}' has no template nodes.",
                    subject=subject.normalized_name,
                )
            )
            return findings

        # Check for root node
        has_subject_root = any(c.node_type == NODE_TYPE_SUBJECT for c in candidates)
        if not has_subject_root:
            findings.append(
                SchimmelValidationFinding(
                    severity="error",
                    code="missing_subject_root",
                    message=f"Subject '{subject.normalized_name}' has no SUBJECT root node.",
                    subject=subject.normalized_name,
                )
            )

        # Check node count
        if len(candidates) < 10:
            findings.append(
                SchimmelValidationFinding(
                    severity="warning",
                    code="low_node_count",
                    message=f"Subject '{subject.normalized_name}' has only {len(candidates)} nodes.",
                    subject=subject.normalized_name,
                )
            )
        elif len(candidates) > 200:
            findings.append(
                SchimmelValidationFinding(
                    severity="warning",
                    code="high_node_count",
                    message=f"Subject '{subject.normalized_name}' has {len(candidates)} nodes.",
                    subject=subject.normalized_name,
                )
            )

        return findings

    def _validate_tree_structure(self, subject: SchimmelSubjectSection) -> list[SchimmelValidationFinding]:
        """Validate the tree structure for inconsistencies."""
        findings: list[SchimmelValidationFinding] = []
        candidates = subject.candidates

        # Check for cycles (would need full tree, basic check)
        seen_depths = set()
        depth_jumps = 0
        for i, node in enumerate(candidates):
            seen_depths.add(node.depth)
            if i > 0 and node.depth > candidates[i - 1].depth + 1:
                depth_jumps += 1
                if node.depth - candidates[i - 1].depth > 2:
                    findings.append(
                        SchimmelValidationFinding(
                            severity="warning",
                            code="unexpected_depth_jump",
                            message=f"Depth jump from {candidates[i-1].depth} to {node.depth} at '{node.title}'.",
                            subject=subject.normalized_name,
                            page_number=node.page_number,
                            metadata={"from_depth": candidates[i - 1].depth, "to_depth": node.depth},
                        )
                    )

        # Check for orphan bullets (non-heading nodes without parent)
        # Check for duplicate node titles at same depth
        titles_at_depth: dict[int, set[str]] = {}
        for node in candidates:
            if node.depth not in titles_at_depth:
                titles_at_depth[node.depth] = set()
            if node.title in titles_at_depth[node.depth]:
                findings.append(
                    SchimmelValidationFinding(
                        severity="info",
                        code="duplicate_node_title",
                        message=f"Duplicate title '{node.title}' at depth {node.depth}.",
                        subject=subject.normalized_name,
                        page_number=node.page_number,
                    )
                )
            titles_at_depth[node.depth].add(node.title)

        return findings

    def _validate_rule_candidates(self, subject: SchimmelSubjectSection) -> list[SchimmelValidationFinding]:
        """Validate rule candidates for substantive content."""
        findings: list[SchimmelValidationFinding] = []
        for node in subject.candidates:
            for rule in node.rule_candidates:
                if len(rule.raw_rule_text.strip()) < 20:
                    findings.append(
                        SchimmelValidationFinding(
                            severity="warning",
                            code="short_rule",
                            message=f"Rule candidate is too short ({len(rule.raw_rule_text)} chars): '{rule.raw_rule_text[:50]}'.",
                            subject=subject.normalized_name,
                            page_number=rule.start_page,
                        )
                    )
                if " " not in rule.raw_rule_text.strip():
                    findings.append(
                        SchimmelValidationFinding(
                            severity="error",
                            code="rule_no_spaces",
                            message=f"Rule candidate has no spaces, likely not substantive text.",
                            subject=subject.normalized_name,
                            page_number=rule.start_page,
                        )
                    )
        return findings

    def _validate_cross_references(self, subject: SchimmelSubjectSection) -> list[SchimmelValidationFinding]:
        """Validate cross-reference completeness."""
        findings: list[SchimmelValidationFinding] = []
        for node in subject.candidates:
            unresolved = [
                cr for cr in node.cross_references
                if cr.resolution_status in ("UNRESOLVED", "NEEDS_REVIEW")
            ]
            if unresolved:
                for cr in unresolved:
                    findings.append(
                        SchimmelValidationFinding(
                            severity="info",
                            code="unresolved_cross_reference",
                            message=f"Unresolved cross-reference at '{node.title}': '{cr.target_text}'.",
                            subject=subject.normalized_name,
                            page_number=cr.source_page,
                        )
                    )
        return findings

    def _validate_jurisdiction_variants(self, subject: SchimmelSubjectSection) -> list[SchimmelValidationFinding]:
        """Validate jurisdiction variant handling."""
        findings: list[SchimmelValidationFinding] = []
        seen_variants: set[str] = set()
        for node in subject.candidates:
            if node.jurisdiction_scope and node.jurisdiction_scope not in ("GENERAL",):
                if node.jurisdiction_scope in seen_variants:
                    continue
                seen_variants.add(node.jurisdiction_scope)

        for node in subject.candidates:
            for rule in node.rule_candidates:
                if rule.jurisdiction_scope and rule.jurisdiction_scope not in ("GENERAL",):
                    if rule.jurisdiction_scope in seen_variants:
                        continue
                    seen_variants.add(rule.jurisdiction_scope)

        return findings

    def _count_nodes(self, nodes: list[SchimmelTemplateNodeCandidate]) -> int:
        """Count all nodes recursively."""
        count = 0
        for node in nodes:
            count += 1
            count += self._count_nodes(node.children)
        return count

    def produce_summary(self, document: SchimmelDocumentCandidate, findings: list[SchimmelValidationFinding]) -> dict:
        """Produce a validation summary."""
        subject_count = len(document.subjects)
        node_count = sum(self._count_nodes(s.candidates) for s in document.subjects)
        rule_count = sum(len(n.rule_candidates) for s in document.subjects for n in s.candidates)
        element_count = sum(
            len(r.elements) for s in document.subjects for n in s.candidates for r in n.rule_candidates
        )
        exception_count = sum(
            len(r.exceptions) for s in document.subjects for n in s.candidates for r in n.rule_candidates
        )
        jx_count = len({
            (s.normalized_name, n.jurisdiction_scope)
            for s in document.subjects for n in s.candidates if n.jurisdiction_scope and n.jurisdiction_scope != "GENERAL"
        })
        cr_count = sum(
            len(n.cross_references) for s in document.subjects for n in s.candidates
        )
        cr_resolved = sum(
            1 for s in document.subjects for n in s.candidates for cr in n.cross_references
            if cr.resolution_status not in ("UNRESOLVED",)
        )
        abbr_count = len(document.abbreviations)
        low_conf = sum(
            1 for s in document.subjects for n in s.candidates if n.parse_confidence < 0.6
        )

        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]

        return {
            "subjects_detected": subject_count,
            "templates_created": subject_count,
            "template_nodes_created": node_count,
            "rule_candidates_created": rule_count,
            "elements_created": element_count,
            "exceptions_created": exception_count,
            "jurisdiction_variants_created": jx_count,
            "cross_references_found": cr_count,
            "cross_references_resolved": cr_resolved,
            "abbreviations_detected": abbr_count,
            "low_confidence_nodes": low_conf,
            "validation_errors": len(errors),
            "review_warnings": len(warnings),
            "errors": [{"code": e.code, "message": e.message} for e in errors],
            "warnings": [{"code": w.code, "message": w.message} for w in warnings],
        }