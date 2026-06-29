from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.rules import LegalRule, LegalSubject
from app.db.models.templates import EssayTemplate, TemplateNode, TemplateRuleCandidate
from app.db.session import get_session

router = APIRouter(prefix="/data")


@router.get("/", response_class=HTMLResponse)
def data_overview(
    request: Request,
    session: Session = Depends(get_session),
):
    subjects = list(session.scalars(
        select(LegalSubject).order_by(LegalSubject.display_name)
    ).all())

    subject_data = []
    for subject in subjects:
        templates = list(session.scalars(
            select(EssayTemplate).where(EssayTemplate.legal_subject_id == subject.id)
        ).all())

        node_count = 0
        rule_count = 0
        for t in templates:
            nc = session.scalar(
                select(func.count()).where(TemplateNode.essay_template_id == t.id)
            ) or 0
            node_count += nc
            node_ids = session.scalars(
                select(TemplateNode.id).where(TemplateNode.essay_template_id == t.id)
            ).all()
            if node_ids:
                rc = session.scalar(
                    select(func.count()).where(
                        TemplateRuleCandidate.template_node_id.in_(node_ids)
                    )
                ) or 0
                rule_count += rc

        supp_rule_count = session.scalar(
            select(func.count()).where(LegalRule.legal_subject_id == subject.id)
        ) or 0

        subject_data.append({
            "subject": subject,
            "templates": templates,
            "node_count": node_count,
            "rule_count": rule_count,
            "supp_rule_count": supp_rule_count,
        })

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "data.html", {
        "subject_data": subject_data,
    })


@router.get("/template/{template_id}", response_class=HTMLResponse)
def template_detail(
    request: Request,
    template_id: int,
    session: Session = Depends(get_session),
):
    template = session.get(EssayTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    nodes = list(session.scalars(
        select(TemplateNode)
        .where(TemplateNode.essay_template_id == template_id)
        .options(selectinload(TemplateNode.rule_candidates))
        .order_by(TemplateNode.id)
    ).all())

    roots = [n for n in nodes if n.parent_node_id is None]
    roots.sort(key=lambda n: (n.display_order, n.id))
    children_by_parent: dict[int, list[TemplateNode]] = {}
    for node in nodes:
        if node.parent_node_id is not None:
            children_by_parent.setdefault(node.parent_node_id, []).append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda n: (n.display_order, n.id))

    def build_tree(node: TemplateNode) -> dict:
        return {
            "node": node,
            "rules": list(node.rule_candidates),
            "children": [build_tree(c) for c in children_by_parent.get(node.id, [])],
        }

    tree = [build_tree(r) for r in roots]
    rule_count = sum(len(n.rule_candidates) for n in nodes)

    subject = session.get(LegalSubject, template.legal_subject_id)
    supp_rules = list(session.scalars(
        select(LegalRule)
        .where(LegalRule.legal_subject_id == template.legal_subject_id)
        .order_by(LegalRule.id)
        .limit(80)
    ).all())

    jinja = request.app.state.templates
    return jinja.TemplateResponse(request, "data_template.html", {
        "template": template,
        "subject": subject,
        "tree": tree,
        "node_count": len(nodes),
        "rule_count": rule_count,
        "supp_rules": supp_rules,
    })
