from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.db.models.essays import EssayQuestion
from app.db.models.templates import EssayTemplate
from app.db.session import SessionLocal
from app.web.routes.data import router as data_router
from app.web.routes.guide import router as guide_router
from app.web.routes.history import router as history_router
from app.web.routes.practice import router as practice_router

TEMPLATE_DIR = Path(__file__).parent / "templates"

logger = logging.getLogger(__name__)


def _clean_rule_display(text: str) -> str:
    """Clean raw template rule text for display — strip embedded headings, bullets, footers."""
    if not text:
        return ""
    lines = text.split("\n")
    cleaned: list[str] = []
    found_content = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "•":
            continue
        if stripped.isupper() and len(stripped) > 3:
            continue
        if "schimmel" in stripped.lower() or "sschimmel" in stripped.lower():
            continue
        if re.match(r"^\d+$", stripped):
            continue
        is_content = (
            len(stripped) > 40
            or re.match(r"^[\(\[]?[0-9ivx]+[\)\].]", stripped, re.IGNORECASE)
            or stripped.startswith("•")
            or not found_content
        )
        if not is_content and found_content:
            break
        cleaned.append(stripped)
        if len(stripped) > 30:
            found_content = True
    return "\n".join(cleaned)


def _format_essay_text(text: str) -> str:
    """Normalize essay text for display: collapse mid-sentence line breaks,
    preserve paragraph structure."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        is_heading = stripped.endswith(":") and len(stripped) < 80
        is_list = re.match(r"^(?:\d+[.)]\s|[a-z][.)]\s|[-•*]\s)", stripped)
        if (is_heading or is_list) and buf:
            paragraphs.append(" ".join(buf))
            buf = []
        if buf and not is_list and stripped[0].islower():
            buf.append(stripped)
        else:
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            buf.append(stripped)
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(paragraphs)


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    application = FastAPI(title="CalBar Exam Tutor", version="0.1.0")
    jinja_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    jinja_templates.env.filters["clean_rule"] = _clean_rule_display
    jinja_templates.env.filters["format_essay"] = _format_essay_text
    application.state.templates = jinja_templates
    application.include_router(practice_router)
    application.include_router(history_router)
    application.include_router(data_router)
    application.include_router(guide_router)

    @application.on_event("startup")
    def _startup_check() -> None:
        with SessionLocal() as session:
            q_count = session.scalar(select(func.count(EssayQuestion.id))) or 0
            t_count = session.scalar(select(func.count(EssayTemplate.id))) or 0
            if q_count == 0:
                logger.warning(
                    "No essay questions in database. "
                    "Run: python -m app.cli run-pipeline"
                )
            if t_count == 0:
                logger.warning(
                    "No essay templates in database. "
                    "Run: python -m app.cli parse-essay-template --file <schimmel.pdf>"
                )
            logger.info("Database: %d questions, %d templates", q_count, t_count)

    return application


app = create_app()
