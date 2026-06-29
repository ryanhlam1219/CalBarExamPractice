from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from app.db.models.rules import LegalRule, LegalSubject
from app.db.models.templates import EssayTemplate, TemplateNode, TemplateRuleCandidate
from app.db.repositories.templates import get_subject_templates

logger = logging.getLogger(__name__)

SUBJECT_KEYWORDS: dict[str, list[str]] = {
    "Agency": ["agent", "principal", "agency", "vicarious liability", "respondeat superior", "scope of employment"],
    "Civil Procedure": ["jurisdiction", "venue", "diversity", "federal court", "personal jurisdiction", "removal", "remand", "erie", "joinder", "class action"],
    "Community Property": ["community property", "separate property", "marital", "spouse", "dissolution", "marriage", "divorce", "premarital"],
    "Constitutional Law": ["constitutional", "first amendment", "due process", "equal protection", "commerce clause", "free speech", "establishment clause"],
    "Contracts": ["contract", "offer", "acceptance", "consideration", "breach", "UCC", "damages", "promissory estoppel", "statute of frauds"],
    "Corporations": ["corporation", "shareholder", "director", "fiduciary", "board of directors", "piercing the corporate veil", "bylaws", "dividend"],
    "Criminal Law": ["murder", "manslaughter", "robbery", "burglary", "larceny", "homicide", "felony", "misdemeanor", "arson", "conspiracy", "accomplice", "criminal"],
    "Criminal Procedure": ["fourth amendment", "search and seizure", "miranda", "exclusionary rule", "arrest", "warrant", "probable cause", "sixth amendment"],
    "Evidence": ["hearsay", "privilege", "relevance", "impeachment", "witness", "testimony", "admissible", "character evidence", "expert witness"],
    "Legal Remedies": ["injunction", "specific performance", "restitution", "remedies"],
    "Partnerships": ["partnership", "partner", "limited partnership", "general partner", "limited partner"],
    "Professional Responsibility": ["attorney", "lawyer", "malpractice", "conflict of interest", "confidential", "duty of loyalty", "bar", "ethics"],
    "Real Property": ["easement", "covenant", "deed", "mortgage", "landlord", "tenant", "lease", "adverse possession", "recording", "zoning"],
    "Torts": ["negligence", "duty of care", "proximate cause", "strict liability", "defamation", "battery", "assault", "false imprisonment", "intentional tort", "product liability"],
    "Trusts": ["trust", "trustee", "beneficiary", "settlor", "fiduciary", "trust res", "revocable trust"],
    "Wills": ["will", "testator", "intestate", "probate", "bequest", "devise", "codicil", "executor", "heir", "testate", "holographic", "estate"],
}

_KNOWN_SUBJECT_WORDS: set[str] = {
    "trusts", "agency", "civil", "procedure", "community", "property",
    "constitutional", "law", "contracts", "corporations", "criminal",
    "evidence", "legal", "remedies", "partnerships", "professional",
    "responsibility", "real", "torts", "wills", "business", "associations",
    "and",
}


SUBJECT_LABEL_ALIASES: dict[str, list[str]] = {
    "agency": ["Agency"],
    "business associations": ["Corporations", "Partnerships", "Agency"],
    "civil procedure": ["Civil Procedure"],
    "community property": ["Community Property"],
    "constitutional law": ["Constitutional Law"],
    "contracts": ["Contracts"],
    "corporations": ["Corporations"],
    "criminal law": ["Criminal Law"],
    "criminal procedure": ["Criminal Procedure"],
    "criminal law and procedure": ["Criminal Law", "Criminal Procedure"],
    "evidence": ["Evidence"],
    "legal remedies": ["Legal Remedies"],
    "partnerships": ["Partnerships"],
    "professional responsibility": ["Professional Responsibility"],
    "real property": ["Real Property"],
    "remedies": ["Legal Remedies"],
    "torts": ["Torts"],
    "trusts": ["Trusts"],
    "wills": ["Wills"],
}


def find_template_for_question(
    session: Session,
    question_id: int,
    essay_text: str = "",
) -> tuple[EssayTemplate | None, list[TemplateRuleCandidate], list[LegalRule]]:
    from app.db.models.essays import EssayQuestion

    question = session.get(EssayQuestion, question_id)
    if question is None:
        logger.warning("Question %d not found", question_id)
        return None, [], []

    subjects = session.scalars(select(LegalSubject)).all()
    if not subjects:
        logger.warning("No legal subjects in database")
        return None, [], []

    question_text = "\n".join(
        text
        for text in [question.title, question.normalized_text, question.raw_text]
        if text
    )
    official_subject_label = _official_subject_label_for_question(session, question)
    matched_subject = None
    if official_subject_label:
        matched_subject = _match_subject_label(official_subject_label, question_text, subjects)
        if matched_subject:
            logger.info(
                "Question %d mapped to official subject: %s -> %s",
                question_id,
                official_subject_label,
                matched_subject.display_name,
            )
    if matched_subject is None:
        matched_subject = _match_subject(question_text, subjects)
    if matched_subject is None:
        logger.info("No subject matched for question %d", question_id)
        return None, [], []

    logger.info("Question %d mapped to subject: %s", question_id, matched_subject.display_name)
    from app.services.rule_retriever import retrieve_relevant_rules
    supplemental_rules = retrieve_relevant_rules(
        session, matched_subject.id, question_text, essay_text=essay_text, max_rules=20,
    )

    templates = get_subject_templates(session, matched_subject.display_name)
    if not templates:
        logger.info("No templates found for subject: %s", matched_subject.display_name)
        return None, [], supplemental_rules

    template = _prefer_schimmel_template(templates)
    rule_candidates = _load_rule_candidates(session, template.id)
    logger.info(
        "Template '%s' loaded with %d rule candidates and %d supplemental rules",
        template.name, len(rule_candidates), len(supplemental_rules),
    )
    return template, rule_candidates, supplemental_rules


def _prefer_schimmel_template(templates: list[EssayTemplate]) -> EssayTemplate:
    """Prefer the Schimmel parser output when multiple template sources exist."""
    for template in templates:
        metadata = template.metadata_json or {}
        if metadata.get("source") == "schimmel_template_parser":
            return template
    return templates[0]


def _official_subject_label_for_question(session: Session, question: object) -> str | None:
    instructions_text = getattr(question, "instructions_text", None)
    if not instructions_text:
        from app.db.models.essays import EssayQuestion

        instructions_text = session.scalar(
            select(EssayQuestion.instructions_text)
            .where(
                EssayQuestion.source_document_id == getattr(question, "source_document_id", None),
                EssayQuestion.instructions_text.is_not(None),
            )
            .limit(1)
        )
    if not instructions_text:
        return None

    subject_map = _extract_official_subjects(instructions_text)
    question_number = getattr(question, "question_number", None)
    return subject_map.get(question_number)


def _extract_official_subjects(instructions_text: str) -> dict[int, str]:
    """Extract the CalBar cover-page question-to-subject table when present.

    Handles multiple CalBar PDF formats:
    - Period format:    ``1.  Trusts``
    - Space format:     ``1    Trusts``
    - With page number: ``1    Trusts    4``
    """
    subjects: dict[int, str] = {}
    for line in instructions_text.splitlines():
        match = re.match(r"^\s*(\d+)[.)]\s+(.+?)\s*$", line)
        if not match:
            match = re.match(r"^\s*(\d)\s{2,}(.+?)\s*$", line)
        if not match:
            continue
        qnum = int(match.group(1))
        if qnum < 1 or qnum > 10:
            continue
        raw_label = match.group(2)
        raw_label = re.sub(r"\s{3,}\d+\s*$", "", raw_label)
        label = _clean_subject_label(_degarble_subject_label(raw_label))
        if label:
            subjects[qnum] = label
    return subjects


def _clean_subject_label(label: str) -> str:
    label = re.sub(r"\s+", " ", label.replace("&", "and")).strip(" .;:-")
    if not label or label.casefold() in {"question", "subject"}:
        return ""
    return label


def _degarble_subject_label(label: str) -> str:
    """Fix garbled PDF font encoding where characters are shifted by +29 in ASCII.

    Some CalBar PDFs use custom font encodings that PyMuPDF doesn't fully decode.
    The garbled characters are consistently shifted by +29 from their correct value.
    Ungarbled text (from standard fonts in the same PDF) is left as-is.
    """
    words = label.split()
    fixed: list[str] = []
    for word in words:
        if word.lower() in _KNOWN_SUBJECT_WORDS:
            fixed.append(word)
            continue
        shifted = "".join(
            chr(ord(c) + 29) if not c.islower() and 33 <= ord(c) <= 96 else c
            for c in word
        )
        if shifted.lower() in _KNOWN_SUBJECT_WORDS:
            fixed.append(shifted)
        else:
            fixed.append(word)
    return " ".join(fixed)


def _match_subject_label(
    label: str,
    question_text: str,
    subjects: list[LegalSubject],
) -> LegalSubject | None:
    subject_by_name = {subject.display_name.casefold(): subject for subject in subjects}
    normalized = _clean_subject_label(label).casefold()
    candidate_names = SUBJECT_LABEL_ALIASES.get(normalized)
    if candidate_names is None:
        parts = [part.strip() for part in re.split(r"/|,", normalized) if part.strip()]
        candidate_names = [
            alias
            for part in parts
            for alias in SUBJECT_LABEL_ALIASES.get(part, [])
        ]

    candidates = [
        subject_by_name[name.casefold()]
        for name in candidate_names or []
        if name.casefold() in subject_by_name
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    return _match_subject(question_text, candidates) or candidates[0]


def _match_subject(
    question_text: str,
    subjects: list[LegalSubject],
) -> LegalSubject | None:
    text_lower = question_text.lower()

    scores: list[tuple[float, LegalSubject]] = []
    for subject in subjects:
        keywords = SUBJECT_KEYWORDS.get(subject.display_name, [subject.display_name.lower()])
        score = 0.0
        for kw in keywords:
            hits = len(re.findall(r"\b" + re.escape(kw) + r"\b", text_lower))
            if hits == 0:
                continue
            weight = 3.0 if " " in kw else 1.0
            score += hits * weight
        if score >= 2.0:
            scores.append((score, subject))

    if not scores:
        return None

    scores.sort(key=lambda x: x[0], reverse=True)
    best_score, best_subject = scores[0]
    logger.debug(
        "Subject scores: %s",
        ", ".join(f"{s.display_name}={c:.0f}" for c, s in scores[:5]),
    )
    return best_subject


def _load_rule_candidates(
    session: Session,
    template_id: int,
) -> list[TemplateRuleCandidate]:
    node_ids = session.scalars(
        select(TemplateNode.id).where(TemplateNode.essay_template_id == template_id)
    ).all()
    if not node_ids:
        return []
    return list(session.scalars(
        select(TemplateRuleCandidate).where(
            TemplateRuleCandidate.template_node_id.in_(node_ids)
        )
    ).all())


def _load_supplemental_rules(
    session: Session,
    subject_id: int,
    question_text: str = "",
    max_rules: int = 30,
) -> list[LegalRule]:
    """Load parsed outline rules spread across the full topic hierarchy.

    Instead of taking the first N rules (which always returns intro content),
    this samples proportionally from each top-level topic so every area of
    the outline gets representation. When question text is provided, rules
    whose names match question keywords are prioritised.
    """
    all_rules = list(session.scalars(
        select(LegalRule)
        .where(LegalRule.legal_subject_id == subject_id)
        .options(
            selectinload(LegalRule.legal_topic),
            selectinload(LegalRule.components),
        )
        .order_by(LegalRule.legal_topic_id, LegalRule.parse_confidence.desc())
    ).all())

    if len(all_rules) <= max_rules:
        return all_rules

    by_topic: dict[int | None, list[LegalRule]] = {}
    for rule in all_rules:
        top_topic = _top_level_topic_id(rule)
        by_topic.setdefault(top_topic, []).append(rule)

    topics = list(by_topic.keys())
    per_topic = max(1, max_rules // len(topics))
    selected: list[LegalRule] = []
    for topic_id in topics:
        group = by_topic[topic_id]
        selected.extend(group[:per_topic])

    if question_text and len(selected) > max_rules:
        text_lower = question_text.lower()
        selected.sort(
            key=lambda r: sum(
                1 for w in r.canonical_name.lower().split()
                if len(w) > 3 and w in text_lower
            ),
            reverse=True,
        )

    return selected[:max_rules]


def _top_level_topic_id(rule: LegalRule) -> int | None:
    topic = getattr(rule, "legal_topic", None)
    if not topic:
        return None
    path = getattr(topic, "hierarchy_path", "") or ""
    parts = path.split("/")
    if len(parts) >= 2:
        return hash(parts[1])
    return topic.id
