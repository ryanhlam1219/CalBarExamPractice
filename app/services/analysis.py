from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from app.db.models.essays import EssayQuestion
from app.db.models.rules import LegalRule
from app.db.models.templates import EssayTemplate, TemplateNode, TemplateRuleCandidate
from app.schemas.submissions import AnalysisResult, AnalysisScores, IssueAnalysis, RuleFunnel, RuleFunnelElement

logger = logging.getLogger(__name__)


class AnalysisService(Protocol):
    def analyze(
        self,
        essay_text: str,
        question: EssayQuestion,
        template: EssayTemplate | None,
        rule_candidates: list[TemplateRuleCandidate],
        supplemental_rules: Sequence[LegalRule] | None = None,
    ) -> AnalysisResult: ...


# ---------------------------------------------------------------------------
# Ollama-powered analysis
# ---------------------------------------------------------------------------

PHASE1_SYSTEM_PROMPT = """\
You are an expert California Bar Exam essay grader. Score the student's essay
and provide high-level feedback. Use the Schimmel template topics to identify
what issues should be addressed. Compare against the selected-answer passages
when available to calibrate your scoring.

SCORING RUBRIC:
- issue_spotting (0-35): 30-35 = all major issues identified; 20-29 = most issues
  found but 1-2 missed; 10-19 = several key issues missed; 0-9 = fundamental issues missed
- rule_statements (0-25): 20-25 = precise rules with correct elements stated;
  12-19 = rules stated but imprecise or incomplete; 0-11 = rules missing or incorrect
- fact_application (0-30): 25-30 = facts systematically applied to each element;
  15-24 = some fact application but gaps; 0-14 = conclusory or missing application
- organization (0-10): 8-10 = clear IRAC structure with headings; 5-7 = identifiable
  structure but could be clearer; 0-4 = disorganized or stream-of-consciousness
- overall (0-100): weighted combination; 75+ is a passing-quality essay

Respond with valid JSON ONLY (no markdown):
{
  "scores": {
    "overall": <0-100>,
    "issue_spotting": <0-35>,
    "rule_statements": <0-25>,
    "fact_application": <0-30>,
    "organization": <0-10>
  },
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>"],
  "areas_for_improvement": ["<area 1>", "<area 2>", "<area 3>"],
  "overall_feedback": "<3-4 sentence summary with specific observations>"
}
"""

PHASE2_SYSTEM_PROMPT = """\
You are an expert California Bar Exam essay grader performing deep analysis.

GRADING APPROACH:
1. Use the Schimmel template as the controlling issue hierarchy
2. Use supplemental rules for precise rule statements and elements
3. Compare the student's analysis against the selected-answer passages to gauge
   quality — the selected answers represent passing-quality work
4. Use the essay structure summary (if provided) to quickly identify what the
   student attempted vs missed

Respond with valid JSON ONLY (no markdown):
{
  "issues": [
    {
      "issue_name": "<legal issue>",
      "spotted": true/false,
      "rule_stated": true/false,
      "facts_applied": true/false,
      "feedback": "<2-3 sentence feedback: what was done, what's missing, how the selected answer handled it differently if relevant>",
      "rule_funnel": {
        "issue_name": "<same issue name>",
        "rule_statement": "<the precise legal rule — use supplemental rules for accuracy>",
        "elements": [
          {
            "label": "<element name>",
            "met": true/false,
            "quote": "<exact quote from the QUESTION PROMPT that triggers this element, or empty>"
          }
        ],
        "essay_quotes": ["<key passage from QUESTION PROMPT relevant to this issue>"]
      }
    }
  ],
  "essay_review": {
    "highlights": [
      {
        "type": "strength|improvement|missing|structure",
        "quote": "<exact quote from the STUDENT ESSAY, or empty for missing-issue notes>",
        "issue_name": "<related issue>",
        "feedback": "<detailed feedback with reference to how a passing answer would handle this>",
        "suggested_rewrite": "<for 'improvement' type: a model sentence showing how to rewrite>"
      }
    ]
  }
}

IMPORTANT for rule_funnel:
- Break each issue into its required legal elements
- "quote" is from the QUESTION PROMPT (the fact pattern), not the essay
- "met" means the student addressed this element in their essay
- Use supplemental rules for precise element labels and rule statements

IMPORTANT for essay_review:
- Provide 8-15 highlights covering the full essay
- type="strength": passages with strong rule statements or fact application
- type="improvement": passages needing better precision; include suggested_rewrite
  modeled after how the selected answer stated the rule or applied the facts
- type="missing": issues the student failed to address; explain what a passing
  answer would have discussed (reference the selected-answer passages)
- type="structure": IRAC structure, headings, transitions, conclusory statements
- essay_review.highlights must quote the STUDENT ESSAY, not the question prompt
"""

SYSTEM_PROMPT = PHASE2_SYSTEM_PROMPT


def _build_user_prompt(
    essay_text: str,
    question: EssayQuestion,
    template: EssayTemplate | None,
    rule_candidates: list[TemplateRuleCandidate],
    supplemental_rules: Sequence[LegalRule] | None = None,
) -> str:
    parts: list[str] = []

    parts.append("## ESSAY QUESTION\n")
    parts.append(question.normalized_text or question.raw_text)

    if template and template.nodes:
        q_text = question.normalized_text or question.raw_text or ""
        parts.append(_format_schimmel_template(template, rule_candidates, q_text))
    elif rule_candidates:
        parts.append(_format_schimmel_rule_candidates(rule_candidates))
    else:
        parts.append(
            "\n\n## SCHIMMEL TEMPLATE STATUS\n"
            "No Schimmel template was available for this question. If a template exists for "
            "this subject, this analysis request should be treated as missing context."
        )

    if supplemental_rules:
        parts.append(_format_supplemental_rules(supplemental_rules))

    selected_answer_section = _format_selected_answer_passages(question, essay_text, max_passages=6)
    if selected_answer_section:
        parts.append(selected_answer_section)

    essay_structure = _detect_essay_structure(essay_text, question)
    if essay_structure:
        parts.append(essay_structure)

    parts.append(f"\n\n## STUDENT ESSAY ({len(essay_text.split())} words)\n")
    parts.append(essay_text)

    parts.append(
        "\n\nAnalyze how well the essay addresses each issue in the Schimmel template. "
        "Compare the student's rule statements and analysis against the selected-answer "
        "passages to gauge quality. Use the essay structure summary to identify gaps. "
        "Respond ONLY with the JSON object described in your instructions."
    )
    return "\n".join(parts)


def _build_phase1_prompt(
    essay_text: str,
    question: EssayQuestion,
    template: EssayTemplate | None,
) -> str:
    """Lighter prompt for Phase 1 scoring — no supplemental rules, no rule funnels."""
    parts: list[str] = []
    parts.append("## ESSAY QUESTION\n")
    parts.append(question.normalized_text or question.raw_text)

    if template and template.nodes:
        parts.append("\n\n## SCHIMMEL TEMPLATE TOPICS\n")
        parts.append(f"Subject: {template.name}")
        for node in template.nodes:
            if node.node_type in ("SUBJECT",):
                continue
            if node.node_type in ("MAJOR_TOPIC", "TOPIC", "ISSUE") and node.depth <= 3:
                indent = "  " * max(0, node.depth - 1)
                parts.append(f"{indent}- {node.title.split(chr(10))[0][:80]}")

    selected_answer_section = _format_selected_answer_passages(question, essay_text, max_passages=2)
    if selected_answer_section:
        parts.append(selected_answer_section)

    essay_structure = _detect_essay_structure(essay_text, question)
    if essay_structure:
        parts.append(essay_structure)

    parts.append(f"\n\n## STUDENT ESSAY ({len(essay_text.split())} words)\n")
    parts.append(essay_text)
    parts.append("\n\nScore this essay using the rubric. Compare against the selected-answer passages for calibration. Respond ONLY with JSON.")
    return "\n".join(parts)


_MAX_TEMPLATE_NODES = 30


def _format_schimmel_template(
    template: EssayTemplate,
    rule_candidates: list[TemplateRuleCandidate],
    question_text: str = "",
) -> str:
    nodes = list(template.nodes)
    rule_by_node: dict[int, list[TemplateRuleCandidate]] = {}
    for rc in rule_candidates:
        rule_by_node.setdefault(rc.template_node_id, []).append(rc)

    content_nodes = [n for n in nodes if n.node_type != "SUBJECT"]
    if len(content_nodes) > _MAX_TEMPLATE_NODES and question_text:
        relevant_ids = _rank_template_nodes(content_nodes, rule_by_node, question_text)
    else:
        relevant_ids = None

    lines: list[str] = [
        "\n\n## SCHIMMEL ESSAY TEMPLATE — Controlling Issue Breakdown\n",
        f"Template: {template.name}",
        "Use this hierarchy to break down the question prompt and evaluate the student's essay.",
        "",
    ]

    roots = sorted(
        [node for node in nodes if node.parent_node_id is None],
        key=lambda n: (n.display_order, n.id),
    )
    children_by_parent: dict[int, list[TemplateNode]] = {}
    for node in nodes:
        if node.parent_node_id is not None:
            children_by_parent.setdefault(node.parent_node_id, []).append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda n: (n.display_order, n.id))

    def walk(node: TemplateNode) -> None:
        if node.node_type == "SUBJECT":
            for child in children_by_parent.get(node.id, []):
                walk(child)
            return
        if relevant_ids is not None and node.id not in relevant_ids:
            if node.node_type not in ("MAJOR_TOPIC", "TOPIC") or node.depth > 2:
                return
        _append_template_node(lines, node, rule_by_node)
        for child in children_by_parent.get(node.id, []):
            walk(child)

    for root in roots or nodes:
        walk(root)

    return "\n".join(lines).rstrip()


def _rank_template_nodes(
    nodes: list[TemplateNode],
    rule_by_node: dict[int, list[TemplateRuleCandidate]],
    question_text: str,
) -> set[int]:
    """BM25-rank template nodes by relevance to the question, return top N node IDs."""
    from app.services.rule_retriever import tokenize
    from rank_bm25 import BM25Okapi

    corpus: list[list[str]] = []
    for node in nodes:
        doc = node.title or ""
        if node.raw_text:
            doc += " " + node.raw_text
        rules = rule_by_node.get(node.id, [])
        for r in rules:
            doc += " " + (r.normalized_rule_text or r.raw_rule_text or "")
        corpus.append(tokenize(doc))

    corpus = [tokens if tokens else ["empty"] for tokens in corpus]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(tokenize(question_text))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

    keep: set[int] = set()
    for idx in ranked[:_MAX_TEMPLATE_NODES]:
        node = nodes[idx]
        keep.add(node.id)
        if node.parent_node_id:
            keep.add(node.parent_node_id)
    return keep


def _format_selected_answer_issue_outline(question: EssayQuestion) -> str:
    answers = sorted(
        list(getattr(question, "selected_answers", []) or []),
        key=lambda answer: getattr(answer, "answer_label", ""),
    )
    if not answers:
        return ""

    lines = [
        "\n\n## OFFICIAL SELECTED-ANSWER ISSUE OUTLINE — Additional Calibration\n",
        "These are concise issue headings extracted from official selected answers for this question.",
        "Use them to catch question-specific issues missing from the subject-wide Schimmel template; do not quote or grade against the selected answers as student text.",
        "",
    ]
    added = 0
    for answer in answers[:2]:
        headings = _extract_selected_answer_headings(
            getattr(answer, "normalized_text", "") or getattr(answer, "raw_text", "")
        )
        if not headings:
            continue
        label = getattr(answer, "answer_label", "")
        lines.append(f"Selected Answer {label}:")
        for heading in headings[:16]:
            lines.append(f"- {heading}")
            added += 1
        lines.append("")

    return "\n".join(lines).rstrip() if added else ""


def _extract_selected_answer_headings(answer_text: str) -> list[str]:
    headings: list[str] = []
    seen: set[str] = set()
    for raw_line in answer_text.splitlines():
        line = _clean_line(raw_line).strip(":- ")
        if not _looks_like_selected_answer_heading(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        headings.append(line)
        if len(headings) >= 24:
            break
    return headings


def _looks_like_selected_answer_heading(line: str) -> bool:
    if not line or len(line) > 90:
        return False
    low = line.casefold()
    if low.startswith("question ") or low.startswith("answer "):
        return False
    if re.match(r"^\(?\d+\)?[.)]", line):
        return False
    if line.endswith(".") or line.endswith("?"):
        return False
    words = line.split()
    if len(words) > 12:
        return False
    if len(words) <= 2 and len(line) < 6:
        return False
    if line.endswith(":"):
        return True
    alpha_words = [word for word in words if any(char.isalpha() for char in word)]
    if not alpha_words:
        return False
    titleish = sum(1 for word in alpha_words if word[:1].isupper())
    return titleish / len(alpha_words) >= 0.5


def _format_selected_answer_passages(
    question: EssayQuestion, essay_text: str, max_passages: int = 8,
) -> str:
    """Extract the most relevant passages from official selected answers using BM25.

    Instead of just headings, this pulls actual rule statements and analysis
    paragraphs from the model answers, ranked by relevance to the student's essay.
    """
    answers = sorted(
        list(getattr(question, "selected_answers", []) or []),
        key=lambda a: getattr(a, "answer_label", ""),
    )
    if not answers:
        return ""

    all_passages: list[tuple[str, str]] = []
    for answer in answers[:2]:
        label = getattr(answer, "answer_label", "")
        text = getattr(answer, "normalized_text", "") or getattr(answer, "raw_text", "")
        for passage in _split_into_passages(text):
            all_passages.append((label, passage))

    if not all_passages:
        return ""

    from app.services.rule_retriever import tokenize
    from rank_bm25 import BM25Okapi

    question_text = getattr(question, "normalized_text", "") or getattr(question, "raw_text", "")
    query = f"{question_text}\n{essay_text}"
    query_tokens = tokenize(query)
    if not query_tokens:
        return ""

    corpus = [tokenize(p[1]) for p in all_passages]
    corpus = [tokens if tokens else ["empty"] for tokens in corpus]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

    lines = [
        "\n\n## SELECTED-ANSWER PASSAGES — Calibration Reference\n",
        "These passages are from official passing CalBar essays for this exact question.",
        "Use them to calibrate rule precision and analysis depth. Do NOT quote or copy",
        "them — they show the expected quality level for a passing response.",
        "",
    ]
    seen_content: set[str] = set()
    added = 0
    for idx in ranked:
        if added >= max_passages:
            break
        label, passage = all_passages[idx]
        key = passage[:50].lower()
        if key in seen_content:
            continue
        seen_content.add(key)
        trimmed = passage if len(passage.split()) <= 80 else " ".join(passage.split()[:80]) + "..."
        lines.append(f"[Answer {label}] {trimmed}")
        lines.append("")
        added += 1

    return "\n".join(lines).rstrip() if added else ""


def _split_into_passages(answer_text: str) -> list[str]:
    """Split a selected answer into section-based passages (heading + all body until next heading)."""
    lines = answer_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    passages: list[str] = []
    current_heading = ""
    current_body: list[str] = []

    def flush() -> None:
        if not current_body:
            return
        body = " ".join(w for line in current_body for w in line.split())
        if len(body) > 60:
            prefix = f"{current_heading}: " if current_heading else ""
            passages.append(f"{prefix}{body}")

    for raw_line in lines:
        line = _clean_line(raw_line).strip()
        if not line:
            continue
        if _looks_like_selected_answer_heading(line):
            flush()
            current_body = []
            current_heading = line.rstrip(":")
        else:
            current_body.append(line)

    flush()
    return passages


def _detect_essay_structure(essay_text: str, question: EssayQuestion | None = None) -> str:
    """Pre-process the student's essay to identify structure and gaps.

    Detects headings, IRAC patterns, and compares against selected-answer
    headings to identify issues the student may have missed.
    """
    if not essay_text or len(essay_text.split()) < 30:
        return ""

    normalized = essay_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    headings: list[str] = []
    has_rule_keywords = 0
    has_application_keywords = 0
    has_conclusion_keywords = 0
    paragraph_count = 0
    prev_was_empty = True

    for line in lines:
        stripped = line.strip()
        if not stripped:
            prev_was_empty = True
            continue
        if prev_was_empty:
            paragraph_count += 1
        prev_was_empty = False

        if _looks_like_student_heading(stripped):
            headings.append(stripped.rstrip(":"))

        lower = stripped.lower()
        if any(cue in lower for cue in ["the rule is", "the law provides", "under the", "pursuant to", "is defined as", "requires that"]):
            has_rule_keywords += 1
        if any(cue in lower for cue in ["here,", "in this case", "applying", "the facts show", "because"]):
            has_application_keywords += 1
        if any(cue in lower for cue in ["therefore,", "thus,", "accordingly,", "in conclusion", "consequently"]):
            has_conclusion_keywords += 1

    if not headings and has_rule_keywords == 0:
        return ""

    parts = ["\n\n## ESSAY STRUCTURE SUMMARY\n"]
    word_count = len(essay_text.split())
    parts.append(f"Word count: {word_count}")
    parts.append(f"Paragraphs: {paragraph_count}")

    if headings:
        parts.append(f"Student's headings ({len(headings)}):")
        for h in headings[:20]:
            parts.append(f"- {h}")

    irac = []
    if headings:
        irac.append("Issues identified via headings")
    if has_rule_keywords:
        irac.append(f"Rule statements detected ({has_rule_keywords})")
    if has_application_keywords:
        irac.append(f"Fact application phrases ({has_application_keywords})")
    if has_conclusion_keywords:
        irac.append(f"Conclusions ({has_conclusion_keywords})")
    if irac:
        parts.append("IRAC signals: " + "; ".join(irac))

    if question:
        missed = _find_missed_issues(headings, question)
        if missed:
            parts.append(f"Potentially missed issues ({len(missed)}):")
            for issue in missed[:8]:
                parts.append(f"- {issue}")

    return "\n".join(parts)


def _find_missed_issues(student_headings: list[str], question: EssayQuestion) -> list[str]:
    """Compare student headings against selected-answer headings to find potential gaps."""
    answers = list(getattr(question, "selected_answers", []) or [])
    if not answers:
        return []

    sa_headings: list[str] = []
    for answer in answers[:2]:
        text = getattr(answer, "normalized_text", "") or getattr(answer, "raw_text", "")
        sa_headings.extend(_extract_selected_answer_headings(text))

    if not sa_headings:
        return []

    student_lower = set()
    for h in student_headings:
        student_lower.add(h.lower())
        for word in h.lower().split():
            if len(word) > 4:
                student_lower.add(word)

    essay_words = student_lower

    missed: list[str] = []
    seen: set[str] = set()
    for sa_h in sa_headings:
        key = sa_h.lower()
        if key in seen:
            continue
        seen.add(key)
        sa_words = set(w for w in key.split() if len(w) > 4)
        if not sa_words & essay_words:
            missed.append(sa_h)

    return missed


def _looks_like_student_heading(line: str) -> bool:
    """Detect if a line in the student's essay is a section heading."""
    if not line or len(line) > 100:
        return False
    if line.endswith(".") or line.endswith("?"):
        return False
    if line.endswith(":"):
        return True
    words = line.split()
    if len(words) > 10 or len(words) < 2:
        return False
    alpha = [w for w in words if any(c.isalpha() for c in w)]
    if not alpha:
        return False
    caps = sum(1 for w in alpha if w[0].isupper())
    return caps / len(alpha) >= 0.6 and len(line) < 60


def _append_template_node(
    lines: list[str],
    node: TemplateNode,
    rule_by_node: dict[int, list[TemplateRuleCandidate]],
) -> None:
    title = _clean_line(node.title.split("\n")[0] if node.title else "")
    if not title or len(title) < 3 or _is_template_noise_title(title):
        return

    indent = "  " * max(0, node.depth - 1)
    type_label = node.node_type.replace("_", " ").title()
    lines.append(f"{indent}### {title}  [{type_label}]")

    node_rules = rule_by_node.get(node.id, [])
    if node_rules:
        for rc in node_rules:
            for rule_line in _rule_candidate_lines(rc, title):
                lines.append(f"{indent}- {rule_line}")
    elif node.raw_text and "\n" in node.raw_text:
        raw_lines = [_clean_line(ln) for ln in node.raw_text.splitlines()]
        raw_lines = [ln for ln in raw_lines if ln and ln != "•"]
        if raw_lines and raw_lines[0].lower() == title.lower():
            raw_lines = raw_lines[1:]
        for line in raw_lines[:8]:
            lines.append(f"{indent}- {line}")
    lines.append("")


def _is_template_noise_title(title: str) -> bool:
    title_lower = title.casefold()
    if "sschimmel@swlaw.edu" in title_lower:
        return True
    if title_lower.startswith("prof. sarah schimmel"):
        return True
    if title_lower.startswith("sarah schimmel"):
        return True
    return False


def _format_schimmel_rule_candidates(rule_candidates: list[TemplateRuleCandidate]) -> str:
    lines = [
        "\n\n## SCHIMMEL TEMPLATE RULE CANDIDATES\n",
        "No full Schimmel template hierarchy was loaded, but these Schimmel rule candidates are available.",
        "",
    ]
    for rc in rule_candidates[:40]:
        text = " ".join(_rule_candidate_lines(rc, ""))
        if len(text) > 280:
            text = f"{text[:277]}..."
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines).rstrip()


def _format_supplemental_rules(supplemental_rules: Sequence[LegalRule]) -> str:
    lines = [
        "\n\n## SUPPLEMENTAL RULES — Precision Context\n",
        "Use these rules for precise rule statements and elements. Each is a condensed legal rule.",
        "",
    ]
    for rule in supplemental_rules[:15]:
        topic = getattr(rule, "legal_topic", None)
        topic_name = getattr(topic, "name", "") if topic else ""
        label = f"{topic_name} > {rule.canonical_name}" if topic_name else rule.canonical_name
        statement = _clean_multiline(rule.rule_statement)
        if statement and len(statement) > 200:
            statement = statement[:197].rsplit(" ", 1)[0] + "..."
        meta = ", ".join(
            str(item)
            for item in [rule.jurisdiction_scope, rule.rule_status]
            if item and item != "GENERAL"
        )
        if meta:
            label += f" [{meta}]"
        lines.append(f"- **{label}**: {statement}" if statement else f"- **{label}**")
    return "\n".join(lines).rstrip()


def _format_rule_component(component: Any) -> str:
    label = _clean_line(getattr(component, "label", "") or "")
    content = _clean_multiline(getattr(component, "content", "") or "")
    component_type = _clean_line(getattr(component, "component_type", "") or "")
    if label and content:
        return f"{label}: {content}"
    if content:
        return f"{component_type}: {content}" if component_type else content
    return ""


def _rule_candidate_lines(rc: TemplateRuleCandidate, title: str) -> list[str]:
    rule_text = (rc.normalized_rule_text or rc.raw_rule_text or "").strip()
    lines = [_clean_line(ln) for ln in rule_text.splitlines()]
    lines = [line for line in lines if line and line != "•"]
    if title and lines and lines[0].lower() == title.lower():
        lines = lines[1:]
    return lines


def _clean_multiline(text: str) -> str:
    return " ".join(_clean_line(line) for line in text.splitlines() if _clean_line(line))


def _clean_line(text: str) -> str:
    return " ".join(text.replace("\n•\n", "\n• ").replace("\n•", "\n• ").split())


class OllamaAnalysisService:
    """Sends the essay + template to a local Ollama model for grading."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "gemma4:31b-cloud") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def analyze(
        self,
        essay_text: str,
        question: EssayQuestion,
        template: EssayTemplate | None,
        rule_candidates: list[TemplateRuleCandidate],
        supplemental_rules: Sequence[LegalRule] | None = None,
    ) -> AnalysisResult:
        user_prompt = _build_user_prompt(
            essay_text, question, template, rule_candidates, supplemental_rules
        )
        prompt_words = len(user_prompt.split())
        logger.info(
            "Prompt: %d words (%d template rules, %d supp rules)",
            prompt_words, len(rule_candidates), len(supplemental_rules or []),
        )

        try:
            raw = self._call_ollama(user_prompt)
            return self._parse_response(raw, template)
        except Exception:
            logger.exception("Ollama analysis failed — falling back to mock")
            return MockAnalysisService().analyze(
                essay_text, question, template, rule_candidates, supplemental_rules
            )

    def analyze_phase1(
        self,
        essay_text: str,
        question: EssayQuestion,
        template: EssayTemplate | None,
    ) -> AnalysisResult:
        """Fast scoring pass — scores + high-level feedback, no rule funnels."""
        user_prompt = _build_phase1_prompt(essay_text, question, template)
        logger.info("Phase 1 prompt: %d words", len(user_prompt.split()))
        try:
            raw = self._call_ollama(user_prompt, PHASE1_SYSTEM_PROMPT, temperature=0.0)
            data = self._extract_json(raw)
            scores_raw = data.get("scores", {})
            return AnalysisResult(
                scores=AnalysisScores(
                    overall=_clamp(scores_raw.get("overall", 50), 0, 100),
                    issue_spotting=_clamp(scores_raw.get("issue_spotting", 15), 0, 35),
                    rule_statements=_clamp(scores_raw.get("rule_statements", 10), 0, 25),
                    fact_application=_clamp(scores_raw.get("fact_application", 15), 0, 30),
                    organization=_clamp(scores_raw.get("organization", 5), 0, 10),
                ),
                issues=[],
                essay_review={"highlights": []},
                strengths=data.get("strengths", []),
                areas_for_improvement=data.get("areas_for_improvement", []),
                overall_feedback=data.get("overall_feedback", ""),
                template_id=template.id if template else None,
                model_id=f"ollama/{self.model}",
            )
        except Exception:
            logger.exception("Phase 1 failed — using mock scores")
            mock = MockAnalysisService().analyze(essay_text, question, template, [])
            mock.issues = []
            mock.essay_review = {"highlights": []}
            return mock

    def analyze_phase2(
        self,
        essay_text: str,
        question: EssayQuestion,
        template: EssayTemplate | None,
        rule_candidates: list[TemplateRuleCandidate],
        supplemental_rules: Sequence[LegalRule] | None = None,
    ) -> AnalysisResult | None:
        """Deep analysis pass — issues, rule funnels, extended essay review.
        Returns None if the call fails so the caller can keep Phase 1 results."""
        user_prompt = _build_user_prompt(
            essay_text, question, template, rule_candidates, supplemental_rules
        )
        logger.info("Phase 2 prompt: %d words", len(user_prompt.split()))
        try:
            raw = self._call_ollama(user_prompt, PHASE2_SYSTEM_PROMPT, timeout=600.0)
            return self._parse_response(raw, template)
        except Exception:
            logger.exception("Phase 2 failed — Phase 1 results will be kept")
            return None

    def _call_ollama(
        self, user_prompt: str, system_prompt: str = SYSTEM_PROMPT,
        timeout: float = 300.0, temperature: float = 0.3,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        resp = httpx.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    def _extract_json(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return json.loads(text)

    def _parse_response(self, raw: str, template: EssayTemplate | None) -> AnalysisResult:
        data = self._extract_json(raw)
        scores_raw = data.get("scores", {})

        scores = AnalysisScores(
            overall=_clamp(scores_raw.get("overall", 50), 0, 100),
            issue_spotting=_clamp(scores_raw.get("issue_spotting", 15), 0, 35),
            rule_statements=_clamp(scores_raw.get("rule_statements", 10), 0, 25),
            fact_application=_clamp(scores_raw.get("fact_application", 15), 0, 30),
            organization=_clamp(scores_raw.get("organization", 5), 0, 10),
        )

        issues = []
        for i in data.get("issues", []):
            funnel_data = i.get("rule_funnel")
            rule_funnel = None
            if funnel_data and isinstance(funnel_data, dict):
                elements = [
                    RuleFunnelElement(
                        label=str(e.get("label", "")),
                        met=bool(e.get("met", False)),
                        quote=str(e.get("quote", "")),
                    )
                    for e in funnel_data.get("elements", [])
                    if isinstance(e, dict)
                ]
                rule_funnel = RuleFunnel(
                    issue_name=str(funnel_data.get("issue_name", i.get("issue_name", ""))),
                    rule_statement=str(funnel_data.get("rule_statement", "")),
                    elements=elements,
                    essay_quotes=funnel_data.get("essay_quotes", []),
                )

            issues.append(IssueAnalysis(
                issue_name=i.get("issue_name", "Unknown"),
                spotted=bool(i.get("spotted", False)),
                rule_stated=bool(i.get("rule_stated", False)),
                facts_applied=bool(i.get("facts_applied", False)),
                feedback=str(i.get("feedback", "")),
                rule_funnel=rule_funnel,
            ))

        return AnalysisResult(
            scores=scores,
            issues=issues,
            essay_review=_normalize_essay_review(data.get("essay_review")),
            strengths=data.get("strengths", []),
            areas_for_improvement=data.get("areas_for_improvement", []),
            overall_feedback=data.get("overall_feedback", ""),
            template_id=template.id if template else None,
            model_id=f"ollama/{self.model}",
        )


def _clamp(value: Any, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return (low + high) / 2


_VALID_HIGHLIGHT_TYPES = {"strength", "improvement", "missing", "structure"}


def _normalize_essay_review(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"highlights": []}

    highlights = []
    for item in raw.get("highlights", []):
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "")).strip()
        highlight_type = str(item.get("type", "improvement")).strip().lower()
        if highlight_type not in _VALID_HIGHLIGHT_TYPES:
            highlight_type = "improvement"
        if not quote and highlight_type not in {"missing", "structure"}:
            continue
        entry: dict[str, str] = {
            "type": highlight_type,
            "quote": quote,
            "issue_name": str(item.get("issue_name", "")).strip(),
            "feedback": str(item.get("feedback", "")).strip(),
        }
        rewrite = str(item.get("suggested_rewrite", "")).strip()
        if rewrite:
            entry["suggested_rewrite"] = rewrite
        highlights.append(entry)
        if len(highlights) >= 15:
            break
    return {"highlights": highlights}


# ---------------------------------------------------------------------------
# Mock analysis (fallback / testing)
# ---------------------------------------------------------------------------

class MockAnalysisService:
    """Produces deterministic mock analysis scores based on essay length and content."""

    def analyze(
        self,
        essay_text: str,
        question: EssayQuestion,
        template: EssayTemplate | None,
        rule_candidates: list[TemplateRuleCandidate],
        supplemental_rules: Sequence[LegalRule] | None = None,
    ) -> AnalysisResult:
        seed = int(hashlib.md5(essay_text.encode()).hexdigest()[:8], 16)
        word_count = len(essay_text.split())

        base = min(85.0, 30.0 + word_count * 0.08)
        variation = ((seed % 20) - 10) / 10.0
        overall = max(0.0, min(100.0, base + variation * 5))

        issue_spotting = round(overall / 100 * 35, 1)
        rule_statements = round(overall / 100 * 25, 1)
        fact_application = round(overall / 100 * 30, 1)
        organization = round(overall / 100 * 10, 1)

        issues = self._generate_issues(essay_text, template, seed)

        spotted_count = sum(1 for i in issues if i.spotted)
        total_issues = len(issues)

        strengths = [
            "Demonstrates understanding of core legal principles",
            "Essay is structured with identifiable sections",
        ]
        if word_count > 500:
            strengths.append("Thorough analysis with detailed fact application")

        improvements = [
            "Consider addressing additional sub-issues",
            "Strengthen rule statements with precise legal language",
        ]
        if word_count < 300:
            improvements.append("Expand analysis — essay is significantly shorter than expected")

        feedback = (
            f"You identified {spotted_count} of {total_issues} key issues. "
            f"Your essay is {word_count} words. "
            "Focus on stating rules precisely and applying facts to each element."
        )

        return AnalysisResult(
            scores=AnalysisScores(
                overall=round(overall, 1),
                issue_spotting=issue_spotting,
                rule_statements=rule_statements,
                fact_application=fact_application,
                organization=organization,
            ),
            issues=issues,
            essay_review=self._essay_review(essay_text, issues),
            strengths=strengths,
            areas_for_improvement=improvements,
            overall_feedback=feedback,
            template_id=template.id if template else None,
            model_id="mock-v1",
        )

    def _generate_issues(
        self,
        essay_text: str,
        template: EssayTemplate | None,
        seed: int,
    ) -> list[IssueAnalysis]:
        issue_names = self._extract_issue_names(template)
        text_lower = essay_text.lower()
        issues: list[IssueAnalysis] = []

        for i, name in enumerate(issue_names):
            keyword = name.lower().split()[0] if name.split() else ""
            spotted = keyword in text_lower or (seed + i) % 3 != 0
            rule_stated = spotted and (seed + i) % 4 != 0
            facts_applied = rule_stated and (seed + i) % 5 != 0

            if spotted and rule_stated and facts_applied:
                feedback = f"Good analysis of {name}. Rule stated and facts applied."
            elif spotted and rule_stated:
                feedback = f"You identified {name} and stated the rule, but apply more facts."
            elif spotted:
                feedback = f"You identified {name} but did not clearly state the rule."
            else:
                feedback = f"You missed {name}. This is a key issue to address."

            funnel = RuleFunnel(
                issue_name=name,
                rule_statement=f"The rule for {name} requires specific elements to be satisfied.",
                elements=[
                    RuleFunnelElement(label="Element 1", met=spotted, quote="(mock quote)" if spotted else ""),
                    RuleFunnelElement(label="Element 2", met=rule_stated, quote="(mock quote)" if rule_stated else ""),
                ],
                essay_quotes=["(mock essay passage)"] if spotted else [],
            )

            issues.append(IssueAnalysis(
                issue_name=name,
                spotted=spotted,
                rule_stated=rule_stated,
                facts_applied=facts_applied,
                feedback=feedback,
                rule_funnel=funnel,
            ))

        return issues

    def _essay_review(self, essay_text: str, issues: list[IssueAnalysis]) -> dict[str, Any]:
        sentences = _sentence_candidates(essay_text)
        highlights: list[dict[str, str]] = []
        if sentences:
            highlights.append({
                "type": "strength",
                "quote": sentences[0],
                "issue_name": issues[0].issue_name if issues else "",
                "feedback": "This passage gives the grader an identifiable legal point to evaluate.",
            })
        if len(sentences) > 1:
            issue_name = next((issue.issue_name for issue in issues if not issue.facts_applied), "")
            highlights.append({
                "type": "improvement",
                "quote": sentences[-1],
                "issue_name": issue_name,
                "feedback": "Push this passage further by tying the rule to specific facts and a clear conclusion.",
            })
        return {"highlights": highlights}

    def _extract_issue_names(self, template: EssayTemplate | None) -> list[str]:
        if template is None:
            return [f"Issue {i}" for i in range(1, 8)]

        names: list[str] = []
        for node in template.nodes:
            if node.node_type in ("MAJOR_TOPIC", "TOPIC", "ISSUE") and node.depth <= 3:
                names.append(node.title)
            if len(names) >= 12:
                break

        return names or [f"Issue {i}" for i in range(1, 8)]


def _sentence_candidates(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    candidates = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized)
        if len(sentence.strip()) >= 20
    ]
    if candidates:
        return candidates[:8]
    return [normalized[:220]]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_analysis_service() -> AnalysisService:
    from app.config import get_settings
    settings = get_settings()

    if settings.analysis_provider == "ollama":
        try:
            resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3.0)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            if any(settings.ollama_model in m for m in models):
                logger.info("Using Ollama model: %s", settings.ollama_model)
                return OllamaAnalysisService(
                    base_url=settings.ollama_base_url,
                    model=settings.ollama_model,
                )
            logger.warning(
                "Ollama model '%s' not found (available: %s). Falling back to mock.",
                settings.ollama_model, ", ".join(models) or "none",
            )
        except Exception:
            logger.warning("Ollama not reachable at %s. Falling back to mock.", settings.ollama_base_url)

    return MockAnalysisService()


# ---------------------------------------------------------------------------
# Follow-up chat
# ---------------------------------------------------------------------------

def chat_about_analysis(submission: Any, message: str, history: list[dict[str, str]] | None = None) -> str:
    from app.config import get_settings

    settings = get_settings()
    context = _chat_context(submission)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a California bar essay tutor. Answer follow-up questions using only the "
                "provided essay question, student's essay, and saved analysis. Be concrete, concise, "
                "and point to the student's own wording when helpful. If the saved analysis does not "
                "contain enough information, say what is missing and give a careful next step. "
                "Format every answer in Markdown with short headings, bullets, bold emphasis, "
                "and fenced code blocks only when they genuinely help."
            ),
        },
        {"role": "user", "content": context},
    ]

    for item in (history or [])[-6:]:
        role = item.get("role") if isinstance(item, dict) else None
        content = item.get("content") if isinstance(item, dict) else None
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": str(content)[:1200]})
    messages.append({"role": "user", "content": message[:2000]})

    if settings.analysis_provider == "ollama":
        try:
            payload = {
                "model": settings.ollama_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2},
            }
            resp = httpx.post(
                f"{settings.ollama_base_url}/api/chat",
                json=payload,
                timeout=120.0,
            )
            resp.raise_for_status()
            return str(resp.json()["message"]["content"]).strip()
        except Exception:
            logger.exception("Analysis chat failed; returning fallback response")

    return _fallback_chat_response(submission, message)


def _chat_context(submission: Any) -> str:
    question = getattr(submission, "essay_question", None)
    analysis = getattr(submission, "analysis", None)
    feedback = getattr(analysis, "feedback_json", None) or {}
    scores = {
        "overall": getattr(analysis, "overall_score", None),
        "issue_spotting": getattr(analysis, "issue_spotting_score", None),
        "rule_statements": getattr(analysis, "rule_statements_score", None),
        "fact_application": getattr(analysis, "fact_application_score", None),
        "organization": getattr(analysis, "organization_score", None),
    }
    compact_feedback = {
        "scores": scores,
        "issues": [
            {
                "issue_name": issue.get("issue_name"),
                "spotted": issue.get("spotted"),
                "rule_stated": issue.get("rule_stated"),
                "facts_applied": issue.get("facts_applied"),
                "feedback": issue.get("feedback"),
            }
            for issue in feedback.get("issues", [])[:12]
            if isinstance(issue, dict)
        ],
        "strengths": feedback.get("strengths", [])[:6],
        "areas_for_improvement": feedback.get("areas_for_improvement", [])[:6],
        "essay_review": (feedback.get("essay_review") or {}).get("highlights", [])[:8]
        if isinstance(feedback.get("essay_review"), dict)
        else [],
        "overall_feedback": feedback.get("overall_feedback", ""),
    }
    question_text = getattr(question, "normalized_text", "") or getattr(question, "raw_text", "")
    essay_text = getattr(submission, "essay_text", "")
    return (
        "SAVED ANALYSIS CONTEXT\n\n"
        f"QUESTION:\n{_clip(question_text, 5000)}\n\n"
        f"STUDENT ESSAY:\n{_clip(essay_text, 5000)}\n\n"
        f"ANALYSIS JSON SUMMARY:\n{json.dumps(compact_feedback, ensure_ascii=False)}"
    )


def _fallback_chat_response(submission: Any, message: str) -> str:
    analysis = getattr(submission, "analysis", None)
    feedback = getattr(analysis, "feedback_json", None) or {}
    improvements = feedback.get("areas_for_improvement", []) or []
    issues = feedback.get("issues", []) or []
    missed = [
        issue.get("issue_name")
        for issue in issues
        if isinstance(issue, dict) and not issue.get("facts_applied")
    ]
    parts = ["I could not reach the AI chat model, but the saved analysis still gives us a useful starting point."]
    if improvements:
        parts.append(f"**Main improvement:** {improvements[0]}")
    if missed:
        parts.append(f"**First issue to revisit:** {missed[0]}.")
    parts.append("Try asking again once the local model is available for a more detailed answer.")
    return "\n\n".join(parts)


def _clip(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else f"{text[:max_chars - 3]}..."
