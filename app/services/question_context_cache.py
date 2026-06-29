"""Pre-computed question context cache.

For each question, caches the template lookup, BM25 rule retrieval, and
selected-answer passage extraction so analysis doesn't re-derive them.
Only the essay text varies per submission — the question context is stable.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.essays import EssayQuestion
from app.db.models.rules import LegalRule
from app.db.models.templates import EssayTemplate, TemplateRuleCandidate

logger = logging.getLogger(__name__)


@dataclass
class QuestionContext:
    template: EssayTemplate | None
    rule_candidates: list[TemplateRuleCandidate]
    supplemental_rules: list[LegalRule]
    built_at: float = field(default_factory=time.monotonic)


_cache: dict[int, QuestionContext] = {}


def get_question_context(
    session: Session,
    question_id: int,
    essay_text: str = "",
) -> tuple[EssayTemplate | None, list[TemplateRuleCandidate], list[LegalRule]]:
    """Get cached question context or build it.

    The template, template rules, and supplemental rules depend only on the
    question (not the essay). Caching avoids repeated DB queries and BM25
    searches for questions that are analyzed multiple times.

    When essay_text is provided, BM25 retrieval is slightly more targeted,
    but the cache still provides a good baseline from the question text alone.
    """
    if question_id in _cache:
        ctx = _cache[question_id]
        logger.debug("Question context cache hit for %d (age=%.0fs)", question_id, time.monotonic() - ctx.built_at)
        return ctx.template, ctx.rule_candidates, ctx.supplemental_rules

    from app.services.question_subject_mapper import find_template_for_question

    t0 = time.monotonic()
    template, rule_candidates, supplemental_rules = find_template_for_question(
        session, question_id, essay_text=essay_text,
    )
    elapsed = time.monotonic() - t0

    _cache[question_id] = QuestionContext(
        template=template,
        rule_candidates=rule_candidates,
        supplemental_rules=supplemental_rules,
    )
    logger.info(
        "Built question context for %d in %.2fs (cached for reuse)",
        question_id, elapsed,
    )
    return template, rule_candidates, supplemental_rules


def clear_cache() -> None:
    _cache.clear()


def warm_cache(session: Session, question_ids: list[int] | None = None) -> int:
    """Pre-warm the cache for a set of questions (or all questions)."""
    from sqlalchemy import select
    from app.db.models.essays import EssayQuestion

    if question_ids is None:
        question_ids = list(session.scalars(select(EssayQuestion.id)).all())

    warmed = 0
    for qid in question_ids:
        if qid not in _cache:
            get_question_context(session, qid)
            warmed += 1
    return warmed
