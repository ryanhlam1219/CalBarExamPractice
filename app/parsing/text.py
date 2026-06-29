from __future__ import annotations

import re
import string


def collapse_inline_whitespace(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_extracted_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", "\n", text)
    return text.strip()


def normalize_paragraph_text(text: str) -> str:
    paragraphs = [collapse_inline_whitespace(part.replace("\n", " ")) for part in re.split(r"\n{2,}", text)]
    return "\n\n".join(part for part in paragraphs if part)


def normalized_key(text: str) -> str:
    key = text.casefold()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


def extraction_quality_score(text: str, page_area: float | None = None) -> float:
    if not text.strip():
        return 0.0
    printable = sum(1 for char in text if char in string.printable or char.isprintable())
    alpha = sum(1 for char in text if char.isalpha())
    chars = max(len(text), 1)
    printable_ratio = printable / chars
    alpha_ratio = alpha / chars
    garbage_ratio = text.count("\ufffd") / chars
    density_bonus = min(len(text.strip()) / 1200, 1.0)
    if page_area:
        density_bonus = min(max(len(text.strip()) / (page_area / 350), 0.0), 1.0)
    score = (0.35 * printable_ratio) + (0.35 * min(alpha_ratio / 0.55, 1.0)) + (0.3 * density_bonus)
    return max(0.0, min(1.0, score - garbage_ratio))


def short_preview(text: str, limit: int = 240) -> str:
    normalized = collapse_inline_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."

