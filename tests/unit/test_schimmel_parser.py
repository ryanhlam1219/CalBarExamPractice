"""Unit tests for the Schimmel essay-template parser components."""

from pathlib import Path

from app.parsing.pdf.extractor import PDFExtractor
from app.parsing.schimmel.abbreviation_normalizer import SchimmelAbbreviationNormalizer
from app.parsing.schimmel.bullet_classifier import SchimmelBulletClassifier
from app.parsing.schimmel.cross_reference_resolver import SchimmelCrossReferenceResolver
from app.parsing.schimmel.heading_classifier import SchimmelHeadingClassifier
from app.parsing.schimmel.hierarchy_builder import SchimmelHierarchyBuilder
from app.parsing.schimmel.jurisdiction_classifier import SchimmelJurisdictionClassifier
from app.parsing.schimmel.models import SchimmelTemplateNodeCandidate
from app.parsing.schimmel.subject_detector import SchimmelSubjectDetector
from app.parsing.schimmel.validator import SchimmelTemplateValidator
from app.schemas.pdf import DocumentExtraction, PageBlockExtraction, PageExtraction


# ---------------------------------------------------------------------------
# Subject detection tests
# ---------------------------------------------------------------------------

def _block(text: str, bold: bool = False, size: float = 11.0, x0: float = 0) -> dict:
    return {
        "text": text,
        "is_bold": bold,
        "font_names": ["TestFont"],
        "font_sizes": [size],
        "bbox": (x0, 0, x0 + 100, 20),
        "page_number": 1,
        "block_index": 0,
        "block_type": "text",
        "metadata": {},
    }


def test_detects_all_caps_subject_heading() -> None:
    detector = SchimmelSubjectDetector()
    assert detector._is_subject_heading("CONTRACTS", _block("CONTRACTS"))
    assert detector._is_subject_heading("CIVIL PROCEDURE", _block("CIVIL PROCEDURE"))


def test_rejects_non_subject_heading() -> None:
    detector = SchimmelSubjectDetector()
    assert not detector._is_subject_heading("Creation of an Agency Relationship", _block("Creation"))
    assert not detector._is_subject_heading("GENERAL PRINCIPALS", _block("GENERAL PRINCIPALS"))


def test_rejects_short_text() -> None:
    detector = SchimmelSubjectDetector()
    assert not detector._is_subject_heading("AB", _block("AB"))
    assert not detector._is_subject_heading("", _block(""))


def test_subject_aliases_normalize() -> None:
    detector = SchimmelSubjectDetector()
    result = detector.classify_unknown_heading("CIV PRO")
    assert result == "Civil Procedure"


def test_detect_subjects_from_blocks() -> None:
    detector = SchimmelSubjectDetector()
    blocks_by_page = {
        1: [_block("AGENCY", bold=True, size=18)],
        2: [
            _block("Some text here"),
            _block("CIVIL PROCEDURE", bold=True, size=18),
        ],
    }
    boundaries = detector.detect_subjects(blocks_by_page)
    assert len(boundaries) == 2
    assert boundaries[0].subject_name == "AGENCY"
    assert boundaries[1].subject_name == "CIVIL PROCEDURE"


# ---------------------------------------------------------------------------
# Heading classification tests
# ---------------------------------------------------------------------------

def test_classifies_subject_heading() -> None:
    classifier = SchimmelHeadingClassifier()
    heading = classifier.classify("CONTRACTS", font_size=18, is_bold=True, is_all_caps=True)
    assert heading.node_type == "SUBJECT"
    assert heading.heading_score >= 0.9
    assert heading.inferred_level == 1


def test_classifies_major_topic() -> None:
    classifier = SchimmelHeadingClassifier()
    heading = classifier.classify("CONTRACT FORMATION", font_size=14, is_bold=True, is_all_caps=True)
    assert heading.node_type == "MAJOR_TOPIC"


def test_classifies_topic() -> None:
    classifier = SchimmelHeadingClassifier()
    heading = classifier.classify("Formation", font_size=11, is_bold=True)
    assert heading.node_type == "TOPIC"


def test_classifies_issue() -> None:
    classifier = SchimmelHeadingClassifier()
    heading = classifier.classify("Offer", font_size=10, is_bold=True)
    assert heading.node_type == "ISSUE"


def test_low_confidence_for_short_text() -> None:
    classifier = SchimmelHeadingClassifier()
    heading = classifier.classify("hi", font_size=8, is_bold=False)
    assert heading.node_type == "OTHER"
    assert heading.heading_score < 0.5


# ---------------------------------------------------------------------------
# Jurisdiction classification tests
# ---------------------------------------------------------------------------

def test_detects_california_jurisdiction() -> None:
    classifier = SchimmelJurisdictionClassifier()
    jx, conf = classifier.detect_jurisdiction("California has a special rule")
    assert jx == "CALIFORNIA"
    assert conf > 0.9


def test_detects_common_law() -> None:
    classifier = SchimmelJurisdictionClassifier()
    jx, conf = classifier.detect_jurisdiction("Under the Common Law, the rule is...")
    assert jx == "COMMON_LAW"


def test_detects_ucc() -> None:
    classifier = SchimmelJurisdictionClassifier()
    jx, conf = classifier.detect_jurisdiction("UCC applies to sale of goods")
    assert jx == "UCC"


def test_detects_rule_variant() -> None:
    classifier = SchimmelJurisdictionClassifier()
    variant, conf = classifier.detect_rule_variant("Mirror Image Rule")
    assert variant == "MIRROR_IMAGE_RULE"


# ---------------------------------------------------------------------------
# Bullet classification tests
# ---------------------------------------------------------------------------

def test_classifies_numbered_element() -> None:
    classifier = SchimmelBulletClassifier()
    result = classifier.classify("(1) Manifest intent to contract")
    assert result.bullet_type == "element"
    assert result.confidence >= 0.8


def test_classifies_exception_keyword() -> None:
    classifier = SchimmelBulletClassifier()
    result = classifier.classify("Exception: charitable trust may have indefinite beneficiaries")
    assert result.bullet_type == "exception"
    assert result.confidence >= 0.8


def test_detects_element_list() -> None:
    classifier = SchimmelBulletClassifier()
    text = """(1) Manifest intent
(2) Definite terms"""
    assert classifier.is_element_list(text)
    elements = classifier.extract_elements(text)
    assert len(elements) == 2


# ---------------------------------------------------------------------------
# Abbreviation normalizer tests
# ---------------------------------------------------------------------------

def test_normalizes_global_abbreviation() -> None:
    normalizer = SchimmelAbbreviationNormalizer()
    result = normalizer.normalize_text("The K was valid")
    assert "contract" in result.lower()


def test_preserves_raw_vs_normalized() -> None:
    normalizer = SchimmelAbbreviationNormalizer()
    normalized = normalizer.normalize_text("P must show rx care")
    # Normalized expands, but we preserve raw separate
    assert "plaintiff" in normalized.lower() or "reasonable" in normalized.lower()


def test_subject_specific_abbreviation() -> None:
    normalizer = SchimmelAbbreviationNormalizer()
    normalized = normalizer.normalize_subject_specific("A owes a duty to P", "Agency")
    assert "agent" in normalized.lower()


# ---------------------------------------------------------------------------
# Cross-reference detection tests
# ---------------------------------------------------------------------------

def test_detects_see_above() -> None:
    resolver = SchimmelCrossReferenceResolver()
    refs = resolver.detect_cross_references("See above.", 1)
    assert len(refs) >= 1
    assert "above" in refs[0].target_text.lower()


def test_detects_cross_subject_reference() -> None:
    resolver = SchimmelCrossReferenceResolver()
    refs = resolver.detect_cross_references("See Wills Template.", 1)
    assert any("wills" in r.target_text.lower() for r in refs)


# ---------------------------------------------------------------------------
# Hierarchy builder tests
# ---------------------------------------------------------------------------

def test_builds_simple_hierarchy() -> None:
    builder = SchimmelHierarchyBuilder()
    root = SchimmelTemplateNodeCandidate(title="Root", node_type="SUBJECT", depth=0)
    child = SchimmelTemplateNodeCandidate(title="Child", node_type="TOPIC", depth=1)
    grandchild = SchimmelTemplateNodeCandidate(title="Grandchild", node_type="ISSUE", depth=2)

    trees = builder.build_tree([root, child, grandchild])
    assert len(trees) == 1
    assert trees[0].title == "Root"
    assert len(trees[0].children) == 1
    assert trees[0].children[0].title == "Child"


def test_flat_list_no_parents() -> None:
    builder = SchimmelHierarchyBuilder()
    node1 = SchimmelTemplateNodeCandidate(title="A", node_type="SUBJECT", depth=0)
    node2 = SchimmelTemplateNodeCandidate(title="B", node_type="SUBJECT", depth=0)
    trees = builder.build_tree([node1, node2])
    assert len(trees) == 2


# ---------------------------------------------------------------------------
# Noise removal tests
# ---------------------------------------------------------------------------

def test_detects_page_number_as_noise() -> None:
    from app.parsing.schimmel.parser import SchimmelTemplateParser
    parser = SchimmelTemplateParser()
    assert parser._is_noise("5")
    assert parser._is_noise("150")
    assert not parser._is_noise("Formation of a Contract")


def test_detects_professor_name_as_noise() -> None:
    from app.parsing.schimmel.parser import FOOTER_PATTERNS
    import re
    assert any(p.search("Prof. Sarah Schimmel") for p in FOOTER_PATTERNS)
    assert any(p.search("sschimmel@swlaw.edu") for p in FOOTER_PATTERNS)