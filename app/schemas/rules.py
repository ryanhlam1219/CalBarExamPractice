from typing import Any

from pydantic import BaseModel, Field


class ParsedRuleComponent(BaseModel):
    component_type: str
    label: str | None = None
    content: str
    display_order: int
    source_page: int
    source_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedTopicSource(BaseModel):
    topic_path: list[str]
    source_page: int
    source_text: str


class ParsedRule(BaseModel):
    topic_path: list[str]
    canonical_name: str
    rule_statement: str
    short_rule_statement: str | None = None
    jurisdiction_scope: str = "GENERAL"
    rule_status: str = "GENERAL"
    parse_confidence: float
    review_status: str
    start_page: int
    end_page: int
    source_text: str
    components: list[ParsedRuleComponent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuleParseResult(BaseModel):
    source_document_id: int | None = None
    subject_canonical_name: str
    subject_display_name: str
    subject_source_page: int | None = None
    subject_source_text: str | None = None
    topics: list[list[str]] = Field(default_factory=list)
    topic_sources: list[ParsedTopicSource] = Field(default_factory=list)
    rules: list[ParsedRule] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    parser_version: str
