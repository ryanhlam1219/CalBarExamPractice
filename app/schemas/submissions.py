from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic import Field


class AnalysisScores(BaseModel):
    overall: float
    issue_spotting: float
    rule_statements: float
    fact_application: float
    organization: float


class RuleFunnelElement(BaseModel):
    """One step in a rule funnel — e.g. an element of an issue."""
    label: str
    met: bool
    quote: str = ""


class RuleFunnel(BaseModel):
    """A hierarchical rule breakdown for a single issue.

    Maps the issue → rule statement → required elements, and highlights
    which essay passages support each element.
    """
    issue_name: str
    rule_statement: str
    elements: list[RuleFunnelElement]
    essay_quotes: list[str] = []


class IssueAnalysis(BaseModel):
    issue_name: str
    spotted: bool
    rule_stated: bool
    facts_applied: bool
    feedback: str
    rule_funnel: RuleFunnel | None = None


class AnalysisResult(BaseModel):
    scores: AnalysisScores
    issues: list[IssueAnalysis]
    essay_review: dict[str, Any] = Field(default_factory=dict)
    strengths: list[str]
    areas_for_improvement: list[str]
    overall_feedback: str
    template_id: int | None = None
    model_id: str | None = None
