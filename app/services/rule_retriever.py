"""BM25-based retrieval of supplemental rules for RAG-enhanced essay analysis.

Instead of loading rules by topic order (which always returns the same intro
content), this module indexes all rules for a subject and retrieves the most
relevant ones based on the question prompt and student essay text.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Sequence

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.db.models.rules import LegalRule, LegalSubject
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "must", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each", "every",
    "all", "any", "few", "more", "most", "other", "some", "such", "no",
    "of", "in", "to", "for", "with", "on", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "about", "than", "that", "this", "these", "those",
    "it", "its", "if", "then", "also", "very", "just", "only", "own",
    "same", "too", "up", "out", "off", "over", "here", "there", "when",
    "where", "why", "how", "what", "which", "who", "whom", "whose",
    "he", "she", "they", "we", "you", "me", "him", "her", "us", "them",
})


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, remove stop words and short tokens."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


class SubjectRuleIndex:
    """BM25 index over all supplemental rules for one legal subject."""

    def __init__(self, rules: list[LegalRule]) -> None:
        self.rules = rules
        corpus = []
        for rule in rules:
            topic = getattr(rule, "legal_topic", None)
            topic_name = getattr(topic, "name", "") if topic else ""
            doc = f"{rule.canonical_name} {topic_name} {rule.rule_statement}"
            components = getattr(rule, "components", []) or []
            for comp in components:
                content = getattr(comp, "content", "")
                if content:
                    doc += f" {content}"
            corpus.append(tokenize(doc))
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, top_k: int = 25) -> list[LegalRule]:
        if not self.bm25 or not self.rules:
            return []
        tokens = tokenize(query)
        if not tokens:
            return self.rules[:top_k]
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self.rules[i] for i in ranked[:top_k]]


_index_cache: dict[int, SubjectRuleIndex] = {}


def _get_or_build_index(session: Session, subject_id: int) -> SubjectRuleIndex:
    if subject_id in _index_cache:
        return _index_cache[subject_id]

    t0 = time.monotonic()
    all_rules = list(session.scalars(
        select(LegalRule)
        .where(LegalRule.legal_subject_id == subject_id)
        .options(
            selectinload(LegalRule.legal_topic),
            selectinload(LegalRule.components),
        )
        .order_by(LegalRule.id)
    ).all())

    index = SubjectRuleIndex(all_rules)
    _index_cache[subject_id] = index
    logger.info(
        "Built BM25 index for subject %d: %d rules in %.2fs",
        subject_id, len(all_rules), time.monotonic() - t0,
    )
    return index


def retrieve_relevant_rules(
    session: Session,
    subject_id: int,
    question_text: str,
    essay_text: str = "",
    max_rules: int = 25,
) -> list[LegalRule]:
    """Retrieve the most relevant supplemental rules for a question + essay using BM25.

    Combines the question prompt and essay text into a search query,
    then ranks all rules for the subject by relevance.
    """
    index = _get_or_build_index(session, subject_id)
    query = f"{question_text}\n{essay_text}"
    results = index.search(query, top_k=max_rules)
    logger.info(
        "BM25 retrieved %d rules for subject %d (query: %d tokens)",
        len(results), subject_id, len(tokenize(query)),
    )
    return results


def clear_index_cache() -> None:
    """Clear the cached BM25 indexes (e.g., after re-parsing rules)."""
    _index_cache.clear()
