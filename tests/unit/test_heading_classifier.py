import pytest
from app.parsing.schimmel.heading_classifier import SchimmelHeadingClassifier, SchimmelHeadingCandidate

@pytest.fixture
def classifier():
    return SchimmelHeadingClassifier()

def test_classify_subject(classifier):
    candidate = classifier.classify(
        text="CONTRACT LAW",
        font_size=16.0,
        is_bold=True,
        is_all_caps=True,
        indent_level=0,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "SUBJECT"
    assert candidate.inferred_level == 1

def test_classify_major_topic(classifier):
    candidate = classifier.classify(
        text="CONTRACT FORMATION",
        font_size=12.0,
        is_bold=True,
        is_all_caps=True,
        indent_level=0,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "MAJOR_TOPIC"
    assert candidate.inferred_level == 2

def test_classify_topic(classifier):
    candidate = classifier.classify(
        text="Formation of a Contract",
        font_size=11.0,
        is_bold=True,
        is_all_caps=False,
        indent_level=0,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "TOPIC"
    assert candidate.inferred_level == 3

def test_classify_issue(classifier):
    candidate = classifier.classify(
        text="Breach of Contract",
        font_size=8.0,
        is_bold=True,
        is_all_caps=False,
        indent_level=0,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "ISSUE"
    assert candidate.inferred_level == 4

def test_classify_subissue(classifier):
    candidate = classifier.classify(
        text="Material Breach",
        font_size=6.0,
        is_bold=True,
        is_all_caps=False,
        indent_level=2,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "SUBISSUE"
    assert candidate.inferred_level == 5

def test_classify_rule(classifier):
    candidate = classifier.classify(
        text="The rule that a contract must be supported by adequate consideration from both parties to be enforceable.",
        font_size=8.0,
        is_bold=False,
        is_all_caps=False,
        indent_level=0,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "RULE"
    assert candidate.inferred_level == 5

def test_classify_element(classifier):
    candidate = classifier.classify(
        text="- Element of a contract: mutual agreement",
        font_size=8.0,
        is_bold=False,
        is_all_caps=False,
        indent_level=1,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "ELEMENT"
    assert candidate.inferred_level == 5

def test_classify_other(classifier):
    candidate = classifier.classify(
        text="A quick brown fox",
        font_size=8.0,
        is_bold=False,
        is_all_caps=False,
        indent_level=0,
        page_number=1
    )
    assert isinstance(candidate, SchimmelHeadingCandidate)
    assert candidate.node_type == "OTHER"
    assert candidate.inferred_level == 5

def test_detect_jurisdiction_variant(classifier):
    assert classifier.detect_jurisdiction_variant("California") == "CALIFORNIA"
    assert classifier.detect_jurisdiction_variant("Common Law") == "COMMON_LAW"
    assert classifier.detect_jurisdiction_variant("UCC") == "UCC"
    assert classifier.detect_jurisdiction_variant("Federal") == "FEDERAL"
    assert classifier.detect_jurisdiction_variant("Majority") == "MAJORITY"
    assert classifier.detect_jurisdiction_variant("Minority") == "MINORITY"
    assert classifier.detect_jurisdiction_variant("Traditional") == "TRADITIONAL_MODERN"
    assert classifier.detect_jurisdiction_variant("Modern") == "TRADITIONAL_MODERN"
    assert classifier.detect_jurisdiction_variant("Unknown Jurisdiction") is None

def test_detect_rule_variant(classifier):
    assert classifier.detect_rule_variant("Mirror Image Rule") == "MIRROR_IMAGE_RULE"
    assert classifier.detect_rule_variant("Battle of the Forms") == "BATTLE_OF_THE_FORMS"
    assert classifier.detect_rule_variant("Perfect Tender Rule") == "PERFECT_TENDER_RULE"
    assert classifier.detect_rule_variant("Unknown Rule Variant") is None