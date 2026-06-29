from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/guide", response_class=HTMLResponse)
def user_guide(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "guide.html", {})
