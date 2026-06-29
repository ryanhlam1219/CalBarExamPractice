from typing import Any

from pydantic import BaseModel, Field


class ParsedEssayQuestion(BaseModel):
    question_number: int
    title: str | None = None
    raw_text: str
    normalized_text: str
    instructions_text: str | None = None
    start_page: int
    end_page: int
    start_character_offset: int | None = None
    end_character_offset: int | None = None
    parse_confidence: float
    review_status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedSelectedAnswer(BaseModel):
    question_number: int | None
    answer_label: str
    raw_text: str
    normalized_text: str
    start_page: int
    end_page: int
    start_character_offset: int | None = None
    end_character_offset: int | None = None
    parse_confidence: float
    review_status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReconciliationIssue(BaseModel):
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EssayParseResult(BaseModel):
    source_document_id: int | None = None
    questions: list[ParsedEssayQuestion] = Field(default_factory=list)
    selected_answers: list[ParsedSelectedAnswer] = Field(default_factory=list)
    issues: list[ReconciliationIssue] = Field(default_factory=list)
    parser_version: str

