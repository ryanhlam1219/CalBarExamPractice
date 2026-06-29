from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from html import escape
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentPage,
    EssayQuestion,
    LegalRule,
    LegalSubject,
    LegalTopic,
    RuleComponent,
    SelectedAnswer,
    SourceDocument,
    SourceSpan,
)
from app.services.export import build_document_review_payload
from app.services.files import ensure_parent


def export_document_review_html(session: Session, source_document_id: int, output_path: Path) -> Path:
    payload = build_document_review_payload(session, source_document_id)
    document = _expect_dict(payload["source_document"])
    pages = [_expect_dict(item) for item in _expect_list(payload["pages"])]
    questions = [_expect_dict(item) for item in _expect_list(payload["essay_questions"])]
    answers = [_expect_dict(item) for item in _expect_list(payload["selected_answers"])]
    rules = [_expect_dict(item) for item in _expect_list(payload["legal_rules"])]
    spans = [_expect_dict(item) for item in _expect_list(payload["source_spans"])]

    pages_by_id = {page_id: page for page in pages if (page_id := _int_or_none(page.get("id"))) is not None}
    answers_by_question: dict[int, list[dict[str, object]]] = defaultdict(list)
    for answer in answers:
        question_id = answer.get("essay_question_id")
        if isinstance(question_id, int):
            answers_by_question[question_id].append(answer)
    spans_by_entity = _group_span_dicts(spans)

    sections = [
        _document_header(document, pages, questions, answers, rules, spans),
        _quality_table(pages),
        _essay_section(questions, answers_by_question, spans_by_entity, pages_by_id),
        _rule_section(rules, spans_by_entity, pages_by_id),
        _span_section(spans, pages_by_id),
    ]
    return _write_html(output_path, _page_shell(_string(document.get("title"), "Document Review"), sections))


def export_data_browser_html(session: Session, output_path: Path, include_rules: bool = True) -> Path:
    documents = list(session.scalars(select(SourceDocument).order_by(SourceDocument.created_at, SourceDocument.id)).all())
    pages = list(session.scalars(select(DocumentPage)).all())
    pages_by_id = {page.id: page for page in pages}
    questions = list(session.scalars(
        select(EssayQuestion).order_by(
            EssayQuestion.exam_year,
            EssayQuestion.exam_month,
            EssayQuestion.question_number,
            EssayQuestion.id,
        )
    ).all())
    answers = list(session.scalars(
        select(SelectedAnswer).order_by(
            SelectedAnswer.essay_question_id,
            SelectedAnswer.answer_label,
            SelectedAnswer.id,
        )
    ).all())
    answers_by_question: dict[int, list[SelectedAnswer]] = defaultdict(list)
    for answer in answers:
        if answer.essay_question_id is not None:
            answers_by_question[answer.essay_question_id].append(answer)

    spans = list(session.scalars(select(SourceSpan).order_by(SourceSpan.entity_type, SourceSpan.entity_id)).all())
    spans_by_entity = _group_spans(spans)
    source_by_id = {document.id: document for document in documents}

    sections = [
        _browser_header(documents, questions, answers, spans),
        _question_browser_section(questions, answers_by_question, spans_by_entity, pages_by_id, source_by_id),
    ]
    if include_rules:
        rules = list(session.scalars(select(LegalRule).order_by(LegalRule.legal_topic_id, LegalRule.id)).all())
        components = list(
            session.scalars(
                select(RuleComponent).order_by(RuleComponent.legal_rule_id, RuleComponent.display_order)
            ).all()
        )
        components_by_rule: dict[int, list[RuleComponent]] = defaultdict(list)
        for component in components:
            components_by_rule[component.legal_rule_id].append(component)
        topics = list(session.scalars(select(LegalTopic)).all())
        subjects = list(session.scalars(select(LegalSubject)).all())
        topic_by_id = {topic.id: topic for topic in topics}
        subject_by_id = {subject.id: subject for subject in subjects}
        sections.append(
            _rule_browser_section(
                rules,
                components_by_rule,
                spans_by_entity,
                pages_by_id,
                source_by_id,
                topic_by_id,
                subject_by_id,
            )
        )

    sections.append(_document_index_section(documents))
    return _write_html(output_path, _page_shell("Parsed Data Browser", sections))


def _document_header(
    document: dict[str, object],
    pages: list[dict[str, object]],
    questions: list[dict[str, object]],
    answers: list[dict[str, object]],
    rules: list[dict[str, object]],
    spans: list[dict[str, object]],
) -> str:
    title = _escape(document.get("title"))
    meta = [
        ("Source type", document.get("source_type")),
        ("Publisher", document.get("publisher")),
        ("Category", document.get("document_category")),
        ("Subject", document.get("subject")),
        ("License", document.get("license_status")),
        ("Review", document.get("review_status")),
        ("SHA-256", _short_hash(document.get("sha256"))),
    ]
    metrics = [
        ("Pages", len(pages)),
        ("Questions", len(questions)),
        ("Answers", len(answers)),
        ("Rules", len(rules)),
        ("Spans", len(spans)),
    ]
    return f"""
    <section class="hero">
      <div>
        <p class="eyebrow">Source Document</p>
        <h1>{title}</h1>
        <dl class="meta">{''.join(_meta_item(label, value) for label, value in meta)}</dl>
      </div>
      <div class="metrics">{''.join(_metric(label, value) for label, value in metrics)}</div>
    </section>
    """


def _browser_header(
    documents: list[SourceDocument],
    questions: list[EssayQuestion],
    answers: list[SelectedAnswer],
    spans: list[SourceSpan],
) -> str:
    metrics = [
        ("Documents", len(documents)),
        ("Questions", len(questions)),
        ("Answers", len(answers)),
        ("Spans", len(spans)),
    ]
    return f"""
    <section class="hero">
      <div>
        <p class="eyebrow">Review Workbench</p>
        <h1>Parsed Data Browser</h1>
        <p class="subtle">Read-only view of loaded source documents, essay questions, selected answers, rules, and provenance.</p>
      </div>
      <div class="metrics">{''.join(_metric(label, value) for label, value in metrics)}</div>
    </section>
    <section class="toolbar" aria-label="Filters">
      <label class="search-label" for="entity-search">Search</label>
      <input id="entity-search" type="search" placeholder="question, rule, topic, source..." />
      <select id="status-filter" aria-label="Review status">
        <option value="">All statuses</option>
        <option value="AUTO_ACCEPTED">Auto accepted</option>
        <option value="NEEDS_REVIEW">Needs review</option>
        <option value="UNREVIEWED">Unreviewed</option>
        <option value="APPROVED">Approved</option>
      </select>
      <span id="visible-count" class="count"></span>
    </section>
    """


def _quality_table(pages: list[dict[str, object]]) -> str:
    if not pages:
        return ""
    rows = []
    for page in pages:
        score = _float(page.get("extraction_quality_score"))
        rows.append(
            "<tr>"
            f"<td>{_escape(page.get('page_number'))}</td>"
            f"<td><span class='{_confidence_class(score)}'>{score:.2f}</span></td>"
            f"<td>{_escape(page.get('extraction_method'))}</td>"
            f"<td>{_escape(page.get('width'))} x {_escape(page.get('height'))}</td>"
            f"<td>{_escape(_snippet(page.get('normalized_text'), 180))}</td>"
            "</tr>"
        )
    return f"""
    <section id="pages">
      <h2>Page Extraction</h2>
      <table>
        <thead><tr><th>Page</th><th>Quality</th><th>Method</th><th>Size</th><th>Preview</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def _essay_section(
    questions: list[dict[str, object]],
    answers_by_question: dict[int, list[dict[str, object]]],
    spans_by_entity: dict[tuple[str, int], list[dict[str, object]]],
    pages_by_id: dict[int, dict[str, object]],
) -> str:
    if not questions:
        return ""
    articles = []
    for question in sorted(questions, key=lambda item: (item.get("question_number") or 0, item.get("id") or 0)):
        question_id = _int_or_none(question.get("id"))
        if question_id is None:
            continue
        answers = answers_by_question.get(question_id, [])
        text_blob = " ".join(
            [
                _string(question.get("normalized_text")),
                " ".join(_string(answer.get("normalized_text")) for answer in answers),
            ]
        )
        articles.append(
            f"""
            <article class="entity" data-kind="question" data-status="{_escape(question.get('review_status'))}" data-search="{_attr(text_blob)}">
              <header>
                <div>
                  <p class="eyebrow">Question {_escape(question.get('question_number'))}</p>
                  <h3>{_escape(question.get('exam_month'))} {_escape(question.get('exam_year'))}</h3>
                </div>
                {_badges(question.get('review_status'), question.get('parse_confidence'))}
              </header>
              <dl class="meta compact">
                {_meta_item("Pages", f"{question.get('start_page')}-{question.get('end_page')}")}
                {_meta_item("Answers", len(answers))}
              </dl>
              <details open>
                <summary>Exam Prompt</summary>
                {_exam_prompt_html(question.get('normalized_text'), question.get('question_number'))}
              </details>
              {_span_details(spans_by_entity.get(("essay_question", question_id), []), pages_by_id)}
              {_answer_list(answers, spans_by_entity, pages_by_id)}
            </article>
            """
        )
    return f"<section id='questions'><h2>Essay Questions</h2>{''.join(articles)}</section>"


def _question_browser_section(
    questions: list[EssayQuestion],
    answers_by_question: dict[int, list[SelectedAnswer]],
    spans_by_entity: dict[tuple[str, int], list[SourceSpan]],
    pages_by_id: dict[int, DocumentPage],
    source_by_id: dict[int, SourceDocument],
) -> str:
    if not questions:
        return "<section id='questions'><h2>Essay Questions</h2><p class='empty'>No essay questions loaded.</p></section>"
    articles = []
    for question in questions:
        answers = answers_by_question.get(question.id, [])
        source = source_by_id.get(question.source_document_id)
        search_blob = " ".join(
            [
                question.normalized_text,
                source.title if source else "",
                " ".join(answer.normalized_text for answer in answers),
            ]
        )
        articles.append(
            f"""
            <article class="entity" data-kind="question" data-status="{_escape(question.review_status)}" data-search="{_attr(search_blob)}">
              <header>
                <div>
                  <p class="eyebrow">{_escape(source.title if source else 'Source')}</p>
                  <h3>Question {_escape(question.question_number)} · {_escape(question.exam_month)} {_escape(question.exam_year)}</h3>
                </div>
                {_badges(question.review_status, question.parse_confidence)}
              </header>
              <details>
                <summary>Exam Prompt</summary>
                {_exam_prompt_html(question.normalized_text, question.question_number)}
              </details>
              {_object_span_details(spans_by_entity.get(("essay_question", question.id), []), pages_by_id)}
              {_object_answer_list(answers, spans_by_entity, pages_by_id)}
            </article>
            """
        )
    return f"<section id='questions'><h2>Essay Questions</h2>{''.join(articles)}</section>"


def _rule_section(
    rules: list[dict[str, object]],
    spans_by_entity: dict[tuple[str, int], list[dict[str, object]]],
    pages_by_id: dict[int, dict[str, object]],
) -> str:
    if not rules:
        return ""
    articles = []
    for rule in rules:
        rule_id = _int_or_none(rule.get("id"))
        if rule_id is None:
            continue
        components = [_expect_dict(item) for item in _expect_list(rule.get("components", []))]
        search_blob = " ".join([_string(rule.get("rule_statement")), " ".join(_string(c.get("content")) for c in components)])
        articles.append(
            f"""
            <article class="entity" data-kind="rule" data-status="{_escape(rule.get('review_status'))}" data-search="{_attr(search_blob)}">
              <header>
                <div>
                  <p class="eyebrow">{_escape(rule.get('rule_status'))}</p>
                  <h3>{_escape(rule.get('canonical_name'))}</h3>
                </div>
                {_badges(rule.get('review_status'), rule.get('parse_confidence'))}
              </header>
              <pre>{_escape(rule.get('rule_statement'))}</pre>
              {_component_list(components)}
              {_span_details(spans_by_entity.get(("legal_rule", rule_id), []), pages_by_id)}
            </article>
            """
        )
    return f"<section id='rules'><h2>Legal Rules</h2>{''.join(articles)}</section>"


def _rule_browser_section(
    rules: list[LegalRule],
    components_by_rule: dict[int, list[RuleComponent]],
    spans_by_entity: dict[tuple[str, int], list[SourceSpan]],
    pages_by_id: dict[int, DocumentPage],
    source_by_id: dict[int, SourceDocument],
    topic_by_id: dict[int, LegalTopic],
    subject_by_id: dict[int, LegalSubject],
) -> str:
    if not rules:
        return "<section id='rules'><h2>Legal Rules</h2><p class='empty'>No legal rules loaded.</p></section>"
    articles = []
    for rule in rules:
        components = components_by_rule.get(rule.id, [])
        topic = topic_by_id.get(rule.legal_topic_id)
        subject = subject_by_id.get(rule.legal_subject_id)
        source = source_by_id.get(rule.source_document_id or -1)
        topic_label = topic.hierarchy_path.replace("/", " / ") if topic else ""
        search_blob = " ".join(
            [
                rule.canonical_name,
                rule.rule_statement,
                topic_label,
                subject.display_name if subject else "",
                " ".join(component.content for component in components),
            ]
        )
        articles.append(
            f"""
            <article class="entity" data-kind="rule" data-status="{_escape(rule.review_status)}" data-search="{_attr(search_blob)}">
              <header>
                <div>
                  <p class="eyebrow">{_escape(subject.display_name if subject else '')} · {_escape(topic_label)}</p>
                  <h3>{_escape(rule.canonical_name)}</h3>
                </div>
                {_badges(rule.review_status, rule.parse_confidence)}
              </header>
              <dl class="meta compact">
                {_meta_item("Status", rule.rule_status)}
                {_meta_item("Jurisdiction", rule.jurisdiction_scope)}
                {_meta_item("Source", source.title if source else "")}
              </dl>
              <pre>{_escape(rule.rule_statement)}</pre>
              {_object_component_list(components)}
              {_object_span_details(spans_by_entity.get(("legal_rule", rule.id), []), pages_by_id)}
            </article>
            """
        )
    return f"<section id='rules'><h2>Legal Rules</h2>{''.join(articles)}</section>"


def _document_index_section(documents: list[SourceDocument]) -> str:
    rows = []
    for document in documents:
        rows.append(
            "<tr>"
            f"<td>{_escape(document.id)}</td>"
            f"<td>{_escape(document.title)}</td>"
            f"<td>{_escape(document.source_type)}</td>"
            f"<td>{_escape(document.document_category)}</td>"
            f"<td>{_escape(document.page_count)}</td>"
            f"<td>{_escape(document.review_status)}</td>"
            "</tr>"
        )
    return f"""
    <section id="documents">
      <h2>Documents</h2>
      <table>
        <thead><tr><th>ID</th><th>Title</th><th>Type</th><th>Category</th><th>Pages</th><th>Review</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def _answer_list(
    answers: list[dict[str, object]],
    spans_by_entity: dict[tuple[str, int], list[dict[str, object]]],
    pages_by_id: dict[int, dict[str, object]],
) -> str:
    if not answers:
        return "<p class='empty'>No selected answers linked.</p>"
    items = []
    for answer in answers:
        answer_id = _int_or_none(answer.get("id"))
        if answer_id is None:
            continue
        items.append(
            f"""
            <details class="subentity">
              <summary>Selected Answer {_escape(answer.get('answer_label'))} {_badges(answer.get('review_status'), answer.get('parse_confidence'))}</summary>
              <pre>{_escape(answer.get('normalized_text'))}</pre>
              {_span_details(spans_by_entity.get(("selected_answer", answer_id), []), pages_by_id)}
            </details>
            """
        )
    return "".join(items)


def _object_answer_list(
    answers: list[SelectedAnswer],
    spans_by_entity: dict[tuple[str, int], list[SourceSpan]],
    pages_by_id: dict[int, DocumentPage],
) -> str:
    if not answers:
        return "<p class='empty'>No selected answers linked.</p>"
    items = []
    for answer in answers:
        items.append(
            f"""
            <details class="subentity">
              <summary>Selected Answer {_escape(answer.answer_label)} {_badges(answer.review_status, answer.parse_confidence)}</summary>
              <pre>{_escape(answer.normalized_text)}</pre>
              {_object_span_details(spans_by_entity.get(("selected_answer", answer.id), []), pages_by_id)}
            </details>
            """
        )
    return "".join(items)


def _component_list(components: list[dict[str, object]]) -> str:
    if not components:
        return ""
    items = [
        f"<li><strong>{_escape(component.get('component_type'))}</strong> {_escape(component.get('label'))} {_escape(component.get('content'))}</li>"
        for component in components
    ]
    return f"<ul class='components'>{''.join(items)}</ul>"


def _object_component_list(components: list[RuleComponent]) -> str:
    if not components:
        return ""
    items = [
        f"<li><strong>{_escape(component.component_type)}</strong> {_escape(component.label)} {_escape(component.content)}</li>"
        for component in components
    ]
    return f"<ul class='components'>{''.join(items)}</ul>"


def _span_section(spans: list[dict[str, object]], pages_by_id: dict[int, dict[str, object]]) -> str:
    if not spans:
        return ""
    rows = []
    for span in spans:
        page_id = _int_or_none(span.get("document_page_id"))
        page = pages_by_id.get(page_id) if page_id is not None else None
        rows.append(
            "<tr>"
            f"<td>{_escape(span.get('entity_type'))}</td>"
            f"<td>{_escape(span.get('entity_id'))}</td>"
            f"<td>{_escape(page.get('page_number') if page else '')}</td>"
            f"<td>{_escape(_snippet(span.get('quoted_text'), 220))}</td>"
            "</tr>"
        )
    return f"""
    <section id="spans">
      <h2>Source Spans</h2>
      <table>
        <thead><tr><th>Entity</th><th>ID</th><th>Page</th><th>Quoted Text</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def _span_details(spans: list[dict[str, object]], pages_by_id: dict[int, dict[str, object]]) -> str:
    if not spans:
        return "<p class='warning'>No source span found.</p>"
    blocks = []
    for span in spans:
        page_id = _int_or_none(span.get("document_page_id"))
        page = pages_by_id.get(page_id) if page_id is not None else None
        blocks.append(
            f"""
            <details class="span">
              <summary>Source page {_escape(page.get('page_number') if page else '')}</summary>
              <blockquote>{_escape(span.get('quoted_text'))}</blockquote>
            </details>
            """
        )
    return "".join(blocks)


def _object_span_details(spans: list[SourceSpan], pages_by_id: dict[int, DocumentPage]) -> str:
    if not spans:
        return "<p class='warning'>No source span found.</p>"
    blocks = []
    for span in spans:
        page = pages_by_id.get(span.document_page_id or -1)
        blocks.append(
            f"""
            <details class="span">
              <summary>Source page {_escape(page.page_number if page else '')}</summary>
              <blockquote>{_escape(span.quoted_text)}</blockquote>
            </details>
            """
        )
    return "".join(blocks)


def _exam_prompt_html(text: object, question_number: object) -> str:
    heading, facts, calls, instructions = _split_exam_prompt(text, question_number)
    fact_html = _render_fact_paragraphs(facts)
    call_html = _render_calls(calls)
    instruction_html = "".join(
        f"<p class='prompt-instruction'>{_escape(instruction)}</p>" for instruction in instructions
    )
    body = "".join([fact_html, call_html, instruction_html])
    if not body:
        body = _render_fact_paragraphs(_prompt_paragraphs(text))
    return (
        "<div class='exam-prompt'>"
        f"<div class='prompt-heading'><span>{_escape(heading)}</span></div>"
        f"<div class='prompt-body'>{body}</div>"
        "</div>"
    )


def _split_exam_prompt(text: object, question_number: object) -> tuple[str, list[str], list[str], list[str]]:
    paragraphs = _prompt_paragraphs(text)
    heading = f"Question {_string(question_number)}".strip()
    if paragraphs and _is_question_heading(paragraphs[0]):
        heading = paragraphs.pop(0).title()

    facts: list[str] = []
    calls: list[str] = []
    instructions: list[str] = []
    in_call_block = False
    for paragraph in paragraphs:
        if _is_instruction_paragraph(paragraph):
            instructions.append(paragraph)
            in_call_block = False
        elif _is_call_intro(paragraph):
            calls.append(paragraph)
            in_call_block = True
        elif in_call_block and _is_call_item(paragraph):
            calls.append(paragraph)
        elif _is_call_paragraph(paragraph):
            calls.extend(_split_calls(paragraph))
            in_call_block = True
        else:
            facts.append(paragraph)
            in_call_block = False
    return heading, facts, calls, instructions


def _prompt_paragraphs(text: object) -> list[str]:
    normalized = _string(text).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [re.sub(r"[ \t]+", " ", part).strip() for part in re.split(r"\n{2,}", normalized)]
    return [paragraph for paragraph in paragraphs if paragraph]


def _render_fact_paragraphs(paragraphs: list[str]) -> str:
    rendered: list[str] = []
    quote_next = False
    for paragraph in paragraphs:
        if quote_next and len(paragraph) <= 360:
            rendered.append(f"<blockquote class='instrument'>{_escape(paragraph)}</blockquote>")
            quote_next = False
        elif _is_dialogue_paragraph(paragraph):
            rendered.append(f"<p class='dialogue'>{_escape(paragraph)}</p>")
        else:
            rendered.append(f"<p>{_escape(paragraph)}</p>")
            quote_next = paragraph.rstrip().casefold().endswith(("following:", "wrote:", "provided:", "stated:"))
    return "".join(rendered)


def _render_calls(calls: list[str]) -> str:
    if not calls:
        return ""
    return "".join(
        f"<p class='{_call_item_class(item)}'>{_escape(item)}</p>"
        for item in calls
    )


def _call_item_class(item: str) -> str:
    if re.match(r"^[a-z]\)\s+", item.strip(), flags=re.IGNORECASE):
        return "call-item call-subitem"
    return "call-item"


def _split_calls(paragraph: str) -> list[str]:
    if _is_call_item(paragraph):
        return [paragraph]
    parts = [part.strip() for part in re.split(r"\n+|(?<=\.)\s+(?=(?:What|Which|How|Can|Should|Is|Are|Do|Does)\b)", paragraph)]
    return [part for part in parts if part]


def _is_question_heading(paragraph: str) -> bool:
    return bool(re.fullmatch(r"QUESTION\s+\d+", paragraph.strip(), flags=re.IGNORECASE))


def _is_instruction_paragraph(paragraph: str) -> bool:
    lowered = paragraph.casefold()
    return (
        lowered.startswith("answer according")
        or lowered.startswith("unless a question")
        or lowered.startswith("assume all appropriate")
    )


def _is_call_paragraph(paragraph: str) -> bool:
    lowered = paragraph.casefold()
    if "discuss" not in lowered and "explain" not in lowered:
        return False
    return bool(re.search(r"\b(what|which|how|can|should|is|are|do|does|whether)\b", lowered))


def _is_call_intro(paragraph: str) -> bool:
    lowered = paragraph.casefold().strip()
    if not lowered.endswith(":"):
        return False
    return bool(
        re.match(r"^(?:\d+\.\s*)?(what|which|how|can|should|is|are|do|does|did|whether)\b", lowered)
        or lowered.startswith(("admit:", "rule on:", "discuss:"))
    )


def _is_call_item(paragraph: str) -> bool:
    text = paragraph.strip()
    if not re.match(r"^(?:\d+\.|[a-z]\))\s+", text, flags=re.IGNORECASE):
        return False
    lowered = text.casefold()
    return lowered.endswith(":") or "?" in lowered or "discuss" in lowered or "explain" in lowered


def _is_dialogue_paragraph(paragraph: str) -> bool:
    return bool(re.match(r"^[A-Z][A-Za-z .'-]{0,40}:\s+", paragraph.strip()))


def _page_shell(title: str, sections: list[str]) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_escape(title)}</title>
  <style>{_css()}</style>
</head>
<body>
  <nav>
    <a href="#questions">Questions</a>
    <a href="#rules">Rules</a>
    <a href="#pages">Pages</a>
    <a href="#spans">Spans</a>
    <a href="#documents">Documents</a>
  </nav>
  <main>{''.join(sections)}</main>
  <script>{_js()}</script>
</body>
</html>
"""


def _write_html(output_path: Path, html: str) -> Path:
    ensure_parent(output_path)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _css() -> str:
    return """
:root { color-scheme: light; --bg: #f7f9fb; --panel: #ffffff; --line: #d8dee8; --text: #18202b; --muted: #596579; --accent: #0f766e; --warn: #b45309; --bad: #b91c1c; --good: #15803d; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
nav { position: sticky; top: 0; z-index: 2; display: flex; gap: 12px; padding: 10px 24px; border-bottom: 1px solid var(--line); background: rgba(255,255,255,.94); backdrop-filter: blur(8px); }
nav a { color: var(--accent); text-decoration: none; font-weight: 650; }
main { max-width: 1180px; margin: 0 auto; padding: 24px; }
section { margin: 0 0 28px; }
.hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; align-items: start; padding: 24px 0; border-bottom: 1px solid var(--line); }
h1, h2, h3 { margin: 0; line-height: 1.2; letter-spacing: 0; }
h1 { font-size: 32px; }
h2 { margin-bottom: 14px; font-size: 20px; }
h3 { font-size: 17px; }
.eyebrow { margin: 0 0 4px; color: var(--muted); font-size: 12px; font-weight: 760; text-transform: uppercase; letter-spacing: .04em; }
.subtle, .empty { color: var(--muted); }
.metrics { display: grid; grid-template-columns: repeat(2, minmax(104px, 1fr)); gap: 10px; }
.metric, .entity, table, .toolbar { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
.metric { padding: 12px; }
.metric strong { display: block; font-size: 22px; }
.metric span { color: var(--muted); font-size: 12px; }
.meta { display: grid; grid-template-columns: repeat(2, minmax(160px, 1fr)); gap: 8px 18px; margin: 14px 0 0; }
.meta.compact { grid-template-columns: repeat(4, minmax(120px, 1fr)); }
.meta dt { color: var(--muted); font-size: 12px; }
.meta dd { margin: 0; overflow-wrap: anywhere; }
.toolbar { position: sticky; top: 42px; z-index: 1; display: flex; gap: 10px; align-items: center; padding: 12px; margin-bottom: 20px; }
.search-label { font-weight: 700; color: var(--muted); }
input, select { min-height: 36px; border: 1px solid var(--line); border-radius: 6px; background: white; color: var(--text); padding: 6px 10px; }
input { flex: 1; min-width: 220px; }
.count { color: var(--muted); margin-left: auto; }
.entity { padding: 16px; margin: 0 0 14px; }
.entity > header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 10px; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
.badge { border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; font-size: 12px; white-space: nowrap; }
.conf-high { color: var(--good); border-color: #86efac; background: #f0fdf4; }
.conf-mid { color: var(--warn); border-color: #fcd34d; background: #fffbeb; }
.conf-low, .warning { color: var(--bad); }
details { margin-top: 10px; }
summary { cursor: pointer; font-weight: 700; color: #243244; }
pre, blockquote { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0 0; padding: 12px; border-left: 3px solid var(--accent); background: #f8fafc; border-radius: 6px; }
blockquote { color: #263447; }
.exam-prompt { margin-top: 10px; border: 1px solid #cbd5e1; border-radius: 8px; background: #ffffff; overflow: hidden; }
.prompt-heading { display: flex; align-items: center; justify-content: space-between; min-height: 44px; padding: 10px 16px; border-bottom: 1px solid var(--line); background: #f1f5f9; }
.prompt-heading span { font-weight: 780; color: #0f172a; text-transform: uppercase; letter-spacing: .04em; }
.prompt-body { margin: 0; padding: 18px; }
.prompt-body p { margin: 0 0 12px; max-width: 78ch; }
.prompt-body p:last-child { margin-bottom: 0; }
.prompt-body .call-item { margin-left: 0; }
.prompt-body .call-subitem { margin-left: 28px; }
.prompt-body .dialogue { margin-left: 18px; font-family: ui-serif, Georgia, Cambria, "Times New Roman", serif; }
.prompt-body .prompt-instruction { margin-top: 18px; }
blockquote.instrument { margin: 10px 0 14px; max-width: 72ch; border-left-color: #64748b; background: #f8fafc; font-family: ui-serif, Georgia, Cambria, "Times New Roman", serif; }
.subentity { padding: 10px 0 0 14px; border-left: 1px solid var(--line); }
.span summary { color: var(--muted); font-weight: 650; }
.components { margin: 10px 0; padding-left: 20px; }
table { width: 100%; border-collapse: collapse; overflow: hidden; }
th, td { padding: 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
tr:last-child td { border-bottom: 0; }
.hidden { display: none !important; }
@media (max-width: 760px) {
  main { padding: 16px; }
  nav { overflow-x: auto; padding-inline: 16px; }
  .hero, .metrics, .meta, .meta.compact { grid-template-columns: 1fr; }
  .toolbar { top: 41px; flex-wrap: wrap; }
  input { flex-basis: 100%; }
  .entity > header { display: block; }
  .badges { justify-content: flex-start; margin-top: 8px; }
}
"""


def _js() -> str:
    return """
const search = document.querySelector('#entity-search');
const statusFilter = document.querySelector('#status-filter');
const count = document.querySelector('#visible-count');
const entities = Array.from(document.querySelectorAll('.entity'));
function applyFilters() {
  const q = (search?.value || '').trim().toLowerCase();
  const status = statusFilter?.value || '';
  let visible = 0;
  entities.forEach((entity) => {
    const text = entity.dataset.search || '';
    const entityStatus = entity.dataset.status || '';
    const matchesText = !q || text.includes(q);
    const matchesStatus = !status || entityStatus === status;
    const show = matchesText && matchesStatus;
    entity.classList.toggle('hidden', !show);
    if (show) visible += 1;
  });
  if (count) count.textContent = `${visible} visible`;
}
search?.addEventListener('input', applyFilters);
statusFilter?.addEventListener('change', applyFilters);
applyFilters();
"""


def _meta_item(label: str, value: object) -> str:
    return f"<div><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>"


def _metric(label: str, value: object) -> str:
    return f"<div class='metric'><strong>{_escape(value)}</strong><span>{_escape(label)}</span></div>"


def _badges(status: object, confidence: object) -> str:
    score = _float(confidence)
    return (
        "<div class='badges'>"
        f"<span class='badge'>{_escape(status)}</span>"
        f"<span class='badge {_confidence_class(score)}'>{score:.2f}</span>"
        "</div>"
    )


def _confidence_class(score: float) -> str:
    if score >= 0.85:
        return "conf-high"
    if score >= 0.72:
        return "conf-mid"
    return "conf-low"


def _group_span_dicts(spans: Iterable[dict[str, object]]) -> dict[tuple[str, int], list[dict[str, object]]]:
    grouped: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for span in spans:
        entity_type = span.get("entity_type")
        entity_id = span.get("entity_id")
        if isinstance(entity_type, str) and isinstance(entity_id, int):
            grouped[(entity_type, entity_id)].append(span)
    return grouped


def _group_spans(spans: Iterable[SourceSpan]) -> dict[tuple[str, int], list[SourceSpan]]:
    grouped: dict[tuple[str, int], list[SourceSpan]] = defaultdict(list)
    for span in spans:
        grouped[(span.entity_type, span.entity_id)].append(span)
    return grouped


def _snippet(value: object, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", _string(value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rsplit(" ", 1)[0] + "..."


def _short_hash(value: object) -> str:
    text = _string(value)
    return text[:12] if text else ""


def _escape(value: object) -> str:
    return escape(_string(value), quote=False)


def _attr(value: object) -> str:
    return escape(_string(value).casefold(), quote=True)


def _string(value: object, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _float(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _expect_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected dict, got {type(value).__name__}")


def _expect_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    raise TypeError(f"Expected list, got {type(value).__name__}")
