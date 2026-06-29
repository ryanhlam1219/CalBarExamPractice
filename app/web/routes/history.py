from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.submissions import EssaySubmission
from app.db.session import get_session

router = APIRouter(prefix="/history")


@router.get("/", response_class=HTMLResponse)
def history_list(
    request: Request,
    session: Session = Depends(get_session),
):
    submissions = list(session.scalars(
        select(EssaySubmission)
        .options(
            selectinload(EssaySubmission.analysis),
            selectinload(EssaySubmission.essay_question),
        )
        .order_by(EssaySubmission.id.desc())
    ).all())

    entries = []
    score_timeline: list[dict] = []
    subject_scores: dict[str, list[float]] = defaultdict(list)
    sub_score_breakdown: dict[str, list[float]] = defaultdict(list)

    for sub in submissions:
        q = sub.essay_question
        a = sub.analysis
        meta = (a.metadata_json or {}) if a else {}
        ctx = meta if "template_name" in meta else (sub.metadata_json or {}).get("analysis_context", {})
        template_name = ctx.get("template_name", "")
        subject = template_name.replace(" Essay Template", "") if template_name else "Unknown"

        entry = {
            "submission": sub,
            "question": q,
            "analysis": a,
            "score": a.overall_score if a else None,
            "subject": subject,
            "model": a.model_id if a else None,
            "phase": meta.get("phase", "?") if a else "processing",
            "issue_count": len((a.feedback_json or {}).get("issues", [])) if a else 0,
            "word_count": len((sub.essay_text or "").split()),
        }
        entries.append(entry)

        if a and a.overall_score is not None:
            label = ""
            if q:
                label = f"{'Q' + str(q.question_number)}"
                if q.exam_year:
                    label = f"{q.exam_year} {label}"
            score_timeline.append({
                "label": label,
                "overall": round(a.overall_score, 1),
                "issue_spotting": round(a.issue_spotting_score or 0, 1),
                "rule_statements": round(a.rule_statements_score or 0, 1),
                "fact_application": round(a.fact_application_score or 0, 1),
                "organization": round(a.organization_score or 0, 1),
                "id": sub.id,
                "subject": subject,
            })
            subject_scores[subject].append(a.overall_score)

            sub_score_breakdown[subject].append(a.issue_spotting_score or 0)
            sub_score_breakdown[subject].append(a.rule_statements_score or 0)
            sub_score_breakdown[subject].append(a.fact_application_score or 0)
            sub_score_breakdown[subject].append(a.organization_score or 0)

    score_timeline.reverse()

    subject_avg: list[dict] = []
    for subj, scores in sorted(subject_scores.items()):
        avg = sum(scores) / len(scores) if scores else 0
        subject_avg.append({"subject": subj, "avg": round(avg, 1), "count": len(scores)})

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "history.html", {
        "entries": entries,
        "score_timeline": score_timeline,
        "subject_avg": subject_avg,
    })
