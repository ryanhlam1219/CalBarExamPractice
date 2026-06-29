from __future__ import annotations

import logging
import random
import re
import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.essays import EssayQuestion, SelectedAnswer
from app.db.models.rules import LegalRule, LegalSubject
from app.db.models.submissions import EssaySubmission
from app.db.models.templates import EssayTemplate, TemplateNode, TemplateRuleCandidate
from app.db.repositories.submissions import create_submission, get_submission, save_analysis
from app.db.session import SessionLocal, get_session
from app.services.analysis import (
    _extract_selected_answer_headings,
    _format_selected_answer_issue_outline,
    chat_about_analysis,
    get_analysis_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Overridable session factory — tests replace this to use the in-memory DB
_session_factory = SessionLocal
_practice_search_cache_lock = threading.Lock()
_practice_search_cache_signature: tuple[tuple[str, int, int], ...] | None = None
_practice_search_cache_subject_map: dict[int, list[str]] = {}
_practice_search_cache_terms_map: dict[int, list[str]] = {}


@router.get("/", response_class=HTMLResponse)
def practice_home(
    request: Request,
    subject: str | None = None,
    year: str | None = None,
    month: str | None = None,
    session: Session = Depends(get_session),
):
    parsed_year: int | None = None
    if year and year.strip().isdigit():
        parsed_year = int(year)
    clean_month = month.strip().lower() if month and month.strip() else None

    query = select(EssayQuestion).order_by(
        EssayQuestion.exam_year.desc(),
        EssayQuestion.exam_month,
        EssayQuestion.question_number,
    ).options(selectinload(EssayQuestion.selected_answers))
    if parsed_year:
        query = query.where(EssayQuestion.exam_year == parsed_year)
    if clean_month:
        query = query.where(EssayQuestion.exam_month == clean_month)

    questions = list(session.scalars(query).all())
    subjects = list(session.scalars(select(LegalSubject).order_by(LegalSubject.display_name)).all())
    years = sorted({q.exam_year for q in questions if q.exam_year}, reverse=True)

    # Compute practiced subjects
    from app.db.models.submissions import EssayAnalysis
    from app.db.models.templates import EssayTemplate as ET
    practiced_subjects: set[str] = set()
    practiced_rows = session.execute(
        select(LegalSubject.display_name)
        .join(ET, ET.legal_subject_id == LegalSubject.id)
        .join(EssayAnalysis, EssayAnalysis.template_id == ET.id)
        .join(EssaySubmission, EssaySubmission.id == EssayAnalysis.essay_submission_id)
        .distinct()
    ).all()
    for row in practiced_rows:
        practiced_subjects.add(row[0])

    # Map each question to subjects and inferred issue-search terms.
    q_subject_map, q_search_terms_map = _get_practice_search_maps(session)

    # Enrich questions with subjects for client-side filtering
    question_data = []
    for q in questions:
        subjs = q_subject_map.get(q.id, [])
        search_terms = q_search_terms_map.get(q.id, [])
        question_data.append({"question": q, "subjects": subjs, "search_terms": search_terms})

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "practice.html", {
        "question_data": question_data,
        "questions": questions,
        "subjects": subjects,
        "years": years,
        "filter_subject": subject,
        "filter_year": parsed_year,
        "filter_month": clean_month,
        "practiced_subjects": practiced_subjects,
        "all_subjects_list": subjects,
    })


def _get_practice_search_maps(session: Session) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    global _practice_search_cache_signature
    global _practice_search_cache_subject_map
    global _practice_search_cache_terms_map

    signature = _practice_search_signature(session)
    with _practice_search_cache_lock:
        if signature == _practice_search_cache_signature:
            return _practice_search_cache_subject_map, _practice_search_cache_terms_map

        all_questions = list(session.scalars(
            select(EssayQuestion).options(selectinload(EssayQuestion.selected_answers))
        ).all())
        subject_map = _build_question_subject_map(session, all_questions)
        terms_map = _build_question_search_terms_map(session, all_questions, subject_map)

        _practice_search_cache_signature = signature
        _practice_search_cache_subject_map = subject_map
        _practice_search_cache_terms_map = terms_map
        return subject_map, terms_map


def _practice_search_signature(session: Session) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for name, model in (
        ("questions", EssayQuestion),
        ("selected_answers", SelectedAnswer),
        ("subjects", LegalSubject),
        ("templates", EssayTemplate),
        ("template_nodes", TemplateNode),
    ):
        count, max_id = session.execute(select(func.count(model.id), func.max(model.id))).one()
        rows.append((name, count or 0, max_id or 0))
    return tuple(rows)


def _build_question_subject_map(
    session: Session,
    all_questions: list[EssayQuestion] | None = None,
) -> dict[int, list[str]]:
    """Map question IDs to lists of subject names. Handles multi-topic
    questions like 'Contracts/Remedies' by returning both subjects."""
    import re

    from app.services.question_subject_mapper import (
        SUBJECT_LABEL_ALIASES,
        _clean_subject_label,
        _match_subject,
        _official_subject_label_for_question,
    )

    if all_questions is None:
        all_questions = list(session.scalars(select(EssayQuestion)).all())
    all_subjects = list(session.scalars(select(LegalSubject)).all())
    subject_by_name = {s.display_name.casefold(): s for s in all_subjects}
    result: dict[int, list[str]] = {}

    for q in all_questions:
        question_text = "\n".join(t for t in [q.title, q.normalized_text, q.raw_text] if t)
        label = _official_subject_label_for_question(session, q)
        subjects: list[str] = []

        if label:
            parts = [p.strip() for p in re.split(r"[/,]", label) if p.strip()]
            for part in parts:
                cleaned = _clean_subject_label(part).casefold()
                aliases = SUBJECT_LABEL_ALIASES.get(cleaned, [])
                for alias in aliases:
                    if alias not in subjects:
                        subjects.append(alias)
                if not aliases and cleaned in subject_by_name:
                    name = subject_by_name[cleaned].display_name
                    if name not in subjects:
                        subjects.append(name)

        if not subjects:
            matched = _match_subject(question_text, all_subjects)
            if matched:
                subjects.append(matched.display_name)

        result[q.id] = subjects

    return result


_SEARCH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if", "in", "is",
    "it", "may", "of", "on", "or", "the", "to", "under", "where", "whether", "with",
}


def _build_question_search_terms_map(
    session: Session,
    questions: list[EssayQuestion],
    q_subject_map: dict[int, list[str]],
) -> dict[int, list[str]]:
    """Build client-side search terms from selected-answer and Schimmel issue signals."""
    if not questions:
        return {}

    nodes_by_subject = _schimmel_search_nodes_by_subject(session)
    result: dict[int, list[str]] = {}

    for question in questions:
        terms: list[str] = []
        evidence_parts = [question.title or "", question.normalized_text or "", question.raw_text or ""]

        answers = list(question.selected_answers or [])
        if not answers:
            answers = list(session.scalars(
                select(SelectedAnswer).where(SelectedAnswer.essay_question_id == question.id)
            ).all())

        for answer in answers:
            answer_text = answer.normalized_text or answer.raw_text or ""
            evidence_parts.append(answer_text)
            terms.extend(_extract_selected_answer_headings(answer_text))

        evidence_text = _normalize_search_text(" ".join(evidence_parts))
        for subject_name in q_subject_map.get(question.id, []):
            for label, tokens in nodes_by_subject.get(subject_name, []):
                if _template_label_matches_evidence(label, tokens, evidence_text):
                    terms.append(label)

        result[question.id] = _dedupe_terms(terms)

    return result


def _schimmel_search_nodes_by_subject(session: Session) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    subjects = list(session.scalars(select(LegalSubject)).all())
    subject_by_id = {subject.id: subject.display_name for subject in subjects}
    templates = list(session.scalars(
        select(EssayTemplate)
        .where(EssayTemplate.legal_subject_id.in_(subject_by_id))
        .options(selectinload(EssayTemplate.nodes))
    ).all())

    preferred_template_ids: dict[int, int] = {}
    for template in templates:
        current_id = preferred_template_ids.get(template.legal_subject_id)
        if current_id is None:
            preferred_template_ids[template.legal_subject_id] = template.id
            continue
        current = next(t for t in templates if t.id == current_id)
        if (template.metadata_json or {}).get("source") == "schimmel_template_parser" and (
            current.metadata_json or {}
        ).get("source") != "schimmel_template_parser":
            preferred_template_ids[template.legal_subject_id] = template.id

    result: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for template in templates:
        if preferred_template_ids.get(template.legal_subject_id) != template.id:
            continue
        subject_name = subject_by_id.get(template.legal_subject_id)
        if not subject_name:
            continue
        labels: list[tuple[str, tuple[str, ...]]] = []
        for node in template.nodes:
            if node.node_type == "SUBJECT":
                continue
            label = _clean_template_search_label(node.title or node.normalized_text or "")
            tokens = _significant_search_tokens(label)
            if not label or not tokens:
                continue
            labels.append((label, tokens))
        result[subject_name] = labels

    return result


def _template_label_matches_evidence(
    label: str,
    tokens: tuple[str, ...],
    evidence_text: str,
) -> bool:
    normalized_label = _normalize_search_text(label)
    if normalized_label and f" {normalized_label} " in f" {evidence_text} ":
        return True
    if len(tokens) >= 2:
        return all(re.search(rf"\b{re.escape(token)}\b", evidence_text) for token in tokens)
    token = tokens[0]
    return len(token) >= 5 and re.search(rf"\b{re.escape(token)}\b", evidence_text) is not None


def _clean_template_search_label(value: str) -> str:
    value = re.sub(r"[•✷]+", " ", value)
    value = re.sub(r"\([^)]{1,4}\)", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -:;")
    if not value or len(value) > 120:
        return ""
    return value


def _significant_search_tokens(value: str) -> tuple[str, ...]:
    tokens = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z']+", value.casefold()):
        token = token.strip("'")
        if len(token) < 3 or token in _SEARCH_STOPWORDS:
            continue
        tokens.append(token)
    return tuple(dict.fromkeys(tokens))


def _normalize_search_text(value: str) -> str:
    return " ".join(re.findall(r"[a-zA-Z][a-zA-Z']+", value.casefold()))


def _dedupe_terms(terms: list[str], limit: int = 120) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = re.sub(r"\s+", " ", term).strip(" -:;")
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= limit:
            break
    return deduped


@router.get("/random")
def random_question(
    year: str | None = None,
    month: str | None = None,
    session: Session = Depends(get_session),
):
    parsed_year: int | None = None
    if year and year.strip().isdigit():
        parsed_year = int(year)
    clean_month = month.strip().lower() if month and month.strip() else None

    query = select(EssayQuestion.id)
    if parsed_year:
        query = query.where(EssayQuestion.exam_year == parsed_year)
    if clean_month:
        query = query.where(EssayQuestion.exam_month == clean_month)

    question_ids = list(session.scalars(query).all())
    if not question_ids:
        raise HTTPException(status_code=404, detail="No questions found")

    question_id = random.choice(question_ids)
    return RedirectResponse(url=f"/exam/{question_id}", status_code=303)


@router.get("/exam/{question_id}", response_class=HTMLResponse)
def exam_page(
    request: Request,
    question_id: int,
    session: Session = Depends(get_session),
):
    question = session.get(EssayQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "exam.html", {
        "question": question,
    })


@router.post("/exam/{question_id}/submit")
def submit_exam(
    request: Request,
    question_id: int,
    essay_text: str = Form(...),
    started_at: str = Form(""),
    time_spent_seconds: int = Form(0),
    session: Session = Depends(get_session),
):
    question = session.get(EssayQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")

    parsed_started_at = None
    if started_at:
        try:
            parsed_started_at = datetime.fromisoformat(started_at)
        except ValueError:
            pass

    word_count = len(essay_text.split())
    logger.info(
        "Submission received: question_id=%d, words=%d, time_spent=%ss",
        question_id, word_count, time_spent_seconds,
    )

    submission = create_submission(
        session,
        essay_question_id=question_id,
        essay_text=essay_text,
        started_at=parsed_started_at,
        time_spent_seconds=time_spent_seconds or None,
    )
    session.commit()
    logger.info("Submission %d created, starting analysis in background", submission.id)

    # Run analysis in background thread so the user sees the spinner immediately
    threading.Thread(
        target=_run_analysis_background,
        args=(submission.id, question_id, essay_text),
        daemon=True,
    ).start()

    return RedirectResponse(url=f"/results/{submission.id}", status_code=303)


def _run_analysis_background(submission_id: int, question_id: int, essay_text: str) -> None:
    """Run two-phase AI analysis: quick scores first, then deep analysis."""
    import time
    t0 = time.monotonic()
    try:
        with _session_factory() as session:
            question = session.get(EssayQuestion, question_id)
            if question is None:
                logger.error("Question %d not found during analysis", question_id)
                return

            from app.services.question_context_cache import get_question_context
            t1 = time.monotonic()
            template, rule_candidates, supplemental_rules = get_question_context(session, question_id, essay_text=essay_text)
            t2 = time.monotonic()
            logger.info("Template lookup took %.1fs", t2 - t1)

            analysis_context = _analysis_context_summary(
                question, template, rule_candidates, supplemental_rules,
            )
            submission = session.get(EssaySubmission, submission_id)
            if submission:
                submission.metadata_json = {
                    **(submission.metadata_json or {}),
                    "analysis_context": analysis_context,
                }
                session.commit()

            if template:
                logger.info(
                    "Using template: %s (%d Schimmel rules, %d supplemental parsed rules)",
                    template.name, len(rule_candidates), len(supplemental_rules),
                )

            service = get_analysis_service()
            has_phase_methods = hasattr(service, "analyze_phase1")

            # ── Phase 1: Quick scoring ──
            t3 = time.monotonic()
            if has_phase_methods:
                logger.info("Phase 1: scoring with %s...", type(service).__name__)
                phase1 = service.analyze_phase1(essay_text, question, template)
                t4 = time.monotonic()
                logger.info("Phase 1 done in %.1fs: overall=%.1f", t4 - t3, phase1.scores.overall)
                save_analysis(session, submission_id, phase1, metadata={**analysis_context, "phase": "scores_ready"})
                session.commit()
                logger.info("Phase 1 scores saved for submission %d", submission_id)
            else:
                t4 = t3

            # ── Phase 2: Deep analysis ──
            t5 = time.monotonic()
            if has_phase_methods:
                logger.info("Phase 2: deep analysis...")
                phase2 = service.analyze_phase2(
                    essay_text, question, template, rule_candidates, supplemental_rules,
                )
            else:
                logger.info("Running single-pass analysis with %s...", type(service).__name__)
                phase2 = service.analyze(
                    essay_text, question, template, rule_candidates, supplemental_rules,
                )
            t6 = time.monotonic()

            if phase2 is None:
                logger.warning(
                    "Phase 2 failed after %.1fs — keeping Phase 1 scores for submission %d",
                    t6 - t5, submission_id,
                )
                _update_analysis(
                    session, submission_id, phase1,
                    metadata={**analysis_context, "phase": "scores_only"},
                )
                session.commit()
                logger.info(
                    "Submission %d saved with scores only in %.1fs (lookup=%.1fs, phase1=%.1fs)",
                    submission_id, time.monotonic() - t0, t2 - t1, t4 - t3,
                )
            else:
                logger.info(
                    "Phase 2 done in %.1fs: issues=%d, model=%s",
                    t6 - t5, len(phase2.issues), phase2.model_id,
                )
                if has_phase_methods:
                    phase2.scores = phase1.scores
                    phase2.strengths = phase1.strengths or phase2.strengths
                    phase2.areas_for_improvement = phase1.areas_for_improvement or phase2.areas_for_improvement
                    phase2.overall_feedback = phase1.overall_feedback or phase2.overall_feedback

                _update_analysis(session, submission_id, phase2, metadata={**analysis_context, "phase": "complete"})
                session.commit()
                logger.info(
                    "Submission %d fully processed in %.1fs (lookup=%.1fs, phase1=%.1fs, phase2=%.1fs)",
                    submission_id, time.monotonic() - t0, t2 - t1, t4 - t3, t6 - t5,
                )
    except Exception:
        logger.exception(
            "Background analysis failed for submission %d after %.1fs",
            submission_id, time.monotonic() - t0,
        )


def _update_analysis(
    session: Session, submission_id: int, result: Any, metadata: dict | None = None,
) -> None:
    """Update an existing analysis record with Phase 2 results."""
    from app.db.models.submissions import EssayAnalysis
    analysis = session.scalar(
        select(EssayAnalysis).where(EssayAnalysis.essay_submission_id == submission_id)
    )
    if analysis:
        analysis.overall_score = result.scores.overall
        analysis.issue_spotting_score = result.scores.issue_spotting
        analysis.rule_statements_score = result.scores.rule_statements
        analysis.fact_application_score = result.scores.fact_application
        analysis.organization_score = result.scores.organization
        analysis.feedback_json = result.model_dump(mode="json")
        analysis.model_id = result.model_id
        if metadata:
            analysis.metadata_json = metadata
        session.flush()
    else:
        save_analysis(session, submission_id, result, metadata=metadata)


@router.post("/results/{submission_id}/reanalyze")
def reanalyze_submission(
    submission_id: int,
    session: Session = Depends(get_session),
):
    submission = get_submission(session, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Delete existing analysis so the processing page shows again
    if submission.analysis is not None:
        session.delete(submission.analysis)
        session.commit()

    question_id = submission.essay_question_id
    essay_text = submission.essay_text

    threading.Thread(
        target=_run_analysis_background,
        args=(submission_id, question_id, essay_text),
        daemon=True,
    ).start()

    return RedirectResponse(url=f"/results/{submission_id}", status_code=303)


@router.get("/api/analysis-status/{submission_id}")
def analysis_status(
    submission_id: int,
    session: Session = Depends(get_session),
):
    """Poll endpoint — reports processing / scores_ready / complete."""
    session.expire_all()
    submission = get_submission(session, submission_id)
    if submission is None:
        raise HTTPException(status_code=404)
    context = _analysis_context_from_submission(session, submission)
    if submission.analysis is not None:
        phase = (submission.analysis.metadata_json or {}).get("phase", "complete")
        scores = {
            "overall": submission.analysis.overall_score,
            "issue_spotting": submission.analysis.issue_spotting_score,
            "rule_statements": submission.analysis.rule_statements_score,
            "fact_application": submission.analysis.fact_application_score,
            "organization": submission.analysis.organization_score,
        }
        response_data: dict[str, Any] = {
            "status": phase,
            "scores": scores,
            "analysis_context": context,
        }
        if submission.analysis.model_id:
            response_data["model_id"] = submission.analysis.model_id
        return JSONResponse(response_data)
    return JSONResponse({"status": "processing", "analysis_context": context})


@router.post("/api/analysis-chat/{submission_id}")
def analysis_chat(
    submission_id: int,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    submission = get_submission(session, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.analysis is None:
        raise HTTPException(status_code=409, detail="Analysis is still processing")

    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    history = payload.get("history")
    if not isinstance(history, list):
        history = []

    reply = chat_about_analysis(submission, message, history)
    return JSONResponse({"reply": reply})


@router.get("/results/{submission_id}", response_class=HTMLResponse)
def results_page(
    request: Request,
    submission_id: int,
    session: Session = Depends(get_session),
):
    submission = get_submission(session, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    analysis = submission.analysis

    # Analysis still processing — show spinner page
    if analysis is None:
        templates = request.app.state.templates
        return templates.TemplateResponse(request, "processing.html", {
            "submission": submission,
        })

    feedback = analysis.feedback_json
    essay_review = feedback.get("essay_review") if isinstance(feedback, dict) else {}
    essay_highlights = essay_review.get("highlights", []) if isinstance(essay_review, dict) else []

    grading_context = _build_grading_context(session, submission, analysis)

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "results.html", {
        "submission": submission,
        "analysis": analysis,
        "feedback": feedback,
        "essay_highlights": essay_highlights,
        "analysis_context": _analysis_context_from_submission(session, submission),
        "grading_context": grading_context,
    })


def _build_grading_context(
    session: Session,
    submission: EssaySubmission,
    analysis: Any,
) -> dict[str, Any]:
    """Load detailed grading context data for the Analysis Context tab."""
    from sqlalchemy.orm import selectinload

    ctx: dict[str, Any] = {
        "template": None,
        "subject": None,
        "template_nodes": [],
        "template_rules": [],
        "supplemental_rules": [],
        "selected_answer_headings": [],
        "model_id": getattr(analysis, "model_id", None),
    }

    question = submission.essay_question
    if not question:
        return ctx

    template_id = getattr(analysis, "template_id", None)
    if template_id:
        template = session.get(EssayTemplate, template_id)
        if template:
            ctx["template"] = template
            subject = session.get(LegalSubject, template.legal_subject_id)
            ctx["subject"] = subject

            nodes = list(session.scalars(
                select(TemplateNode)
                .where(TemplateNode.essay_template_id == template.id)
                .order_by(TemplateNode.id)
            ).all())
            ctx["template_nodes"] = [
                {"type": n.node_type, "title": n.title.split("\n")[0][:80], "depth": n.depth}
                for n in nodes
                if n.node_type != "SUBJECT"
            ]

            node_ids = [n.id for n in nodes]
            if node_ids:
                rules = list(session.scalars(
                    select(TemplateRuleCandidate)
                    .where(TemplateRuleCandidate.template_node_id.in_(node_ids))
                ).all())
                ctx["template_rules"] = [
                    {
                        "text": (r.normalized_rule_text or r.raw_rule_text or "").split("\n")[0][:120],
                        "confidence": round(r.parse_confidence * 100),
                        "pages": f"{r.start_page}–{r.end_page}",
                    }
                    for r in rules
                ]

            if subject:
                supp = list(session.scalars(
                    select(LegalRule)
                    .where(LegalRule.legal_subject_id == subject.id)
                    .options(selectinload(LegalRule.legal_topic))
                    .order_by(LegalRule.legal_topic_id)
                    .limit(30)
                ).all())
                ctx["supplemental_rules"] = [
                    {
                        "name": r.canonical_name[:60],
                        "topic": getattr(r.legal_topic, "name", "")[:40] if r.legal_topic else "",
                        "confidence": round(r.parse_confidence * 100),
                    }
                    for r in supp
                ]

    answers = sorted(
        list(getattr(question, "selected_answers", []) or []),
        key=lambda a: getattr(a, "answer_label", ""),
    )
    for answer in answers[:2]:
        text = getattr(answer, "normalized_text", "") or getattr(answer, "raw_text", "")
        headings = _extract_selected_answer_headings(text)
        if headings:
            ctx["selected_answer_headings"].append({
                "label": getattr(answer, "answer_label", ""),
                "headings": headings[:16],
            })

    return ctx


def _analysis_context_summary(
    question: EssayQuestion,
    template: Any,
    rule_candidates: list[Any],
    supplemental_rules: list[Any],
) -> dict[str, Any]:
    template_name = getattr(template, "name", None)
    metadata = getattr(template, "metadata_json", None) or {}
    return {
        "schimmel_template_used": bool(template and metadata.get("source") == "schimmel_template_parser"),
        "template_name": template_name,
        "schimmel_rule_candidates_count": len(rule_candidates),
        "supplemental_rules_count": len(supplemental_rules),
        "selected_answer_outline_used": bool(_format_selected_answer_issue_outline(question)),
    }


def _analysis_context_from_submission(session: Session, submission: EssaySubmission) -> dict[str, Any]:
    if submission.analysis and submission.analysis.metadata_json:
        return submission.analysis.metadata_json
    metadata = submission.metadata_json or {}
    context = metadata.get("analysis_context")
    if isinstance(context, dict):
        return context

    if submission.analysis and submission.analysis.template_id:
        template = session.get(EssayTemplate, submission.analysis.template_id)
        if template:
            node_ids = session.scalars(
                select(TemplateNode.id).where(TemplateNode.essay_template_id == template.id)
            ).all()
            rule_count = 0
            if node_ids:
                rule_count = len(session.scalars(
                    select(TemplateRuleCandidate.id).where(
                        TemplateRuleCandidate.template_node_id.in_(node_ids)
                    )
                ).all())
            return {
                "schimmel_template_used": (template.metadata_json or {}).get("source") == "schimmel_template_parser",
                "template_name": template.name,
                "schimmel_rule_candidates_count": rule_count,
                "supplemental_rules_count": 0,
                "selected_answer_outline_used": bool(_format_selected_answer_issue_outline(submission.essay_question)),
                "inferred_from_saved_analysis": True,
            }

    return {}
