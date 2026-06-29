from pathlib import Path

from app.parsing.rules.parser import RuleOutlineParser
from app.schemas.pdf import DocumentExtraction, PageBlockExtraction, PageExtraction


def test_rule_parser_builds_topics_rules_components_and_pages() -> None:
    page = PageExtraction(
        page_number=1,
        raw_text="",
        normalized_text="",
        extraction_method="synthetic",
        extraction_quality_score=1.0,
        blocks=[
            PageBlockExtraction(
                page_number=1,
                block_index=0,
                block_type="text",
                text="TRUSTS",
                font_sizes=[18],
                is_bold=True,
                bbox=(0, 0, 100, 20),
            ),
            PageBlockExtraction(
                page_number=1,
                block_index=1,
                block_type="text",
                text="I. Creation of Trusts",
                font_sizes=[14],
                is_bold=True,
                bbox=(0, 30, 200, 50),
            ),
            PageBlockExtraction(
                page_number=1,
                block_index=2,
                block_type="text",
                text="A valid trust requires a settlor with capacity, trust intent, trust property, a beneficiary, and a lawful purpose.",
                font_sizes=[10],
                bbox=(0, 60, 500, 100),
            ),
            PageBlockExtraction(
                page_number=1,
                block_index=3,
                block_type="text",
                text="- trust intent",
                font_sizes=[10],
                bbox=(20, 110, 300, 130),
            ),
            PageBlockExtraction(
                page_number=1,
                block_index=4,
                block_type="text",
                text="Exception: a charitable trust may have indefinite beneficiaries.",
                font_sizes=[10],
                bbox=(20, 140, 500, 160),
            ),
        ],
    )
    extraction = DocumentExtraction(
        source_path=Path("trusts.pdf"),
        sha256="1" * 64,
        page_count=1,
        pages=[page],
        parser_version="test",
    )

    result = RuleOutlineParser(parser_version="test").parse(extraction)

    assert result.subject_canonical_name == "trusts"
    assert any(topic[-1] == "Creation Of Trusts" for topic in result.topics)
    assert len(result.rules) == 1
    assert result.rules[0].start_page == 1
    assert [component.component_type for component in result.rules[0].components] == ["ELEMENT", "EXCEPTION"]

