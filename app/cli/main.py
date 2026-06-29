from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import Base
from app.db.models import SourceDocument
from app.db.models.enums import DocumentCategory, LicenseStatus, SourceType
from app.db.repositories.documents import register_source_document, replace_document_pages
from app.db.repositories.essays import dedupe_essay_questions, replace_essay_parse
from app.db.repositories.rules import replace_rule_parse
from app.db.session import SessionLocal, engine
from app.ingestion.calbar.discovery import DEFAULT_ESSAY_CATEGORIES, CalBarCrawler
from app.ingestion.calbar.downloader import CalBarDownloader, ManifestStore, filter_discovered_items
from app.parsing.essays.parser import EssayParser
from app.parsing.pdf.extractor import PDFExtractor
from app.parsing.rules.parser import RuleOutlineParser
from app.parsing.schimmel.parser import SchimmelTemplateParser
from app.schemas.calbar import CalBarDiscoveryItem, DownloadManifestEntry
from app.services.export import export_document_review
from app.services.files import write_json
from app.services.html_export import export_data_browser_html, export_document_review_html
from app.validation.reports import document_validation_summary
from app.db.repositories.templates import (
    get_subject_templates,
    get_template_counts,
    replace_essay_template_parse,
)
from app.db.models.templates import EssayTemplate, TemplateCrossReference, TemplateNode, TemplateRuleCandidate
from app.parsing.schimmel.models import SchimmelDocumentCandidate
from sqlalchemy import select

app = typer.Typer(no_args_is_help=True)


@app.command("init-db")
def init_db() -> None:
    """Create database tables directly from SQLAlchemy metadata."""
    Base.metadata.create_all(engine)
    typer.echo("Database tables are ready.")


@app.command("load-seed")
def load_seed(
    parsed_dir: Annotated[Path, typer.Option(help="Directory with parsed JSON files.")] = Path("data/parsed"),
) -> None:
    """Load pre-parsed essay questions, Schimmel templates, and rules from JSON files.

    Use this on a fresh install to populate the database without needing
    the original PDF files. The parsed JSONs are included in the git repo.
    """
    Base.metadata.create_all(engine)
    settings = get_settings()
    totals = {"questions": 0, "answers": 0, "templates": 0, "rules": 0}

    with SessionLocal() as session:
        # ── Load essay questions ──
        essay_files = sorted(parsed_dir.glob("*.essays.json"))
        if essay_files:
            typer.echo(f"Loading {len(essay_files)} essay JSON files...")
            for ef in essay_files:
                try:
                    from app.schemas.essays import EssayParseResult
                    data = json.loads(ef.read_text())
                    year, month = _extract_year_month(ef.stem)
                    for q in data.get("questions", []):
                        if not q.get("exam_year"):
                            q["exam_year"] = year
                        if not q.get("exam_month"):
                            q["exam_month"] = month
                    parse_result = EssayParseResult(**data)
                    doc = _get_or_create_seed_document(
                        session, ef.stem.replace(".essays", ""), "essay_questions",
                    )
                    doc.exam_year = year
                    doc.exam_month = month
                    from app.db.repositories.essays import replace_essay_parse
                    replace_essay_parse(session, doc, parse_result)
                    totals["questions"] += len(parse_result.questions)
                    totals["answers"] += len(parse_result.selected_answers)
                except Exception as exc:
                    typer.echo(f"  Warning: {ef.name}: {exc}")
            session.commit()
            typer.echo(f"  Loaded {totals['questions']} questions, {totals['answers']} selected answers")

        # ── Load Schimmel templates ──
        schimmel_seed = parsed_dir / "schimmel_seed.json"
        if schimmel_seed.exists():
            typer.echo("Loading Schimmel templates...")
            try:
                seed_data = json.loads(schimmel_seed.read_text())
                totals["templates"] = _load_schimmel_seed(session, seed_data, settings.parser_version)
                session.commit()
            except Exception as exc:
                typer.echo(f"  Warning: Schimmel templates: {exc}")

        # ── Load supplemental rules ──
        rule_files = sorted(parsed_dir.glob("*.rules.json"))
        if rule_files:
            typer.echo(f"Loading {len(rule_files)} rule JSON files...")
            for rf in rule_files:
                try:
                    from app.schemas.rules import RuleParseResult
                    data = json.loads(rf.read_text())
                    parse_result = RuleParseResult(**data)
                    doc = _get_or_create_seed_document(
                        session, rf.stem.replace(".rules", ""), "rule_outline",
                    )
                    parse_result.source_document_id = doc.id
                    counts = replace_rule_parse(session, doc, parse_result)
                    totals["rules"] += counts.get("rules", 0)
                except Exception as exc:
                    typer.echo(f"  Warning: {rf.name}: {exc}")
            session.commit()
            typer.echo(f"  Loaded {totals['rules']} supplemental rules")

        from app.db.repositories.essays import dedupe_essay_questions
        deduped = dedupe_essay_questions(session)
        if deduped:
            typer.echo(f"  Deduped {deduped} duplicate questions")
        session.commit()

    typer.echo(f"\nSeed complete: {totals['questions']} questions, {totals['templates']} templates, {totals['rules']} rules")


def _load_schimmel_seed(session: Session, seed_data: dict, parser_version: str) -> int:
    """Load Schimmel templates directly from the seed JSON (bypasses parser)."""
    from app.db.models.templates import EssayTemplate, TemplateNode, TemplateRuleCandidate
    from app.db.models.rules import LegalSubject
    from app.db.models.enums import ReviewStatus

    templates_data = seed_data.get("templates", [])
    doc = _get_or_create_seed_document(session, "Schimmel Templates_Bullet Version", "essay_templates")
    total_nodes = 0
    total_rules = 0

    for td in templates_data:
        subject_name = td["subject_name"]
        canonical = subject_name.lower().replace(" ", "_")
        subject = session.scalar(select(LegalSubject).where(LegalSubject.canonical_name == canonical))
        if not subject:
            subject = LegalSubject(canonical_name=canonical, display_name=subject_name)
            session.add(subject)
            session.flush()

        template = EssayTemplate(
            legal_subject_id=subject.id,
            source_document_id=doc.id,
            name=td["name"],
            jurisdiction_scope="GENERAL",
            version="1",
            parse_confidence=td.get("parse_confidence", 0.9),
            review_status=ReviewStatus.UNREVIEWED.value,
            parser_version=parser_version,
            metadata_json={"source": "schimmel_template_parser"},
        )
        session.add(template)
        session.flush()

        node_id_map: dict[int, int] = {}
        for idx, nd in enumerate(td.get("nodes", [])):
            parent_db_id = None
            parent_idx = nd.get("parent_index")
            if parent_idx is not None and parent_idx in node_id_map:
                parent_db_id = node_id_map[parent_idx]

            node = TemplateNode(
                essay_template_id=template.id,
                parent_node_id=parent_db_id,
                node_type=nd["node_type"],
                title=nd["title"],
                raw_text=nd.get("raw_text"),
                normalized_text=nd.get("normalized_text"),
                display_order=nd.get("display_order", idx),
                depth=nd.get("depth", 0),
                jurisdiction_scope=nd.get("jurisdiction_scope"),
                parse_confidence=nd.get("parse_confidence", 0.9),
                review_status=ReviewStatus.UNREVIEWED.value,
                parser_version=parser_version,
                metadata_json={},
            )
            session.add(node)
            session.flush()
            node_id_map[idx] = node.id
            total_nodes += 1

        for rc in td.get("rule_candidates", []):
            node_title = rc.get("node_title", "")
            node_id = None
            for idx, nd in enumerate(td.get("nodes", [])):
                if nd["title"].split('\n')[0][:80] == node_title:
                    node_id = node_id_map.get(idx)
                    break
            if not node_id:
                node_id = node_id_map.get(0)
            if not node_id:
                continue

            rule = TemplateRuleCandidate(
                template_node_id=node_id,
                legal_subject_id=subject.id,
                raw_rule_text=rc["raw_rule_text"],
                normalized_rule_text=rc.get("normalized_rule_text"),
                jurisdiction_scope=rc.get("jurisdiction_scope", "GENERAL"),
                rule_variant=rc.get("rule_variant"),
                source_document_id=doc.id,
                start_page=rc.get("start_page", 1),
                end_page=rc.get("end_page", 1),
                parse_confidence=rc.get("parse_confidence", 0.9),
                review_status=ReviewStatus.UNREVIEWED.value,
                parser_version=parser_version,
            )
            session.add(rule)
            total_rules += 1

        session.flush()

    typer.echo(f"  Loaded {len(templates_data)} templates, {total_nodes} nodes, {total_rules} rule candidates")
    return len(templates_data)


_MONTH_MAP = {
    "jan": "january", "feb": "february", "mar": "march", "apr": "april",
    "may": "may", "jun": "june", "jul": "july", "aug": "august",
    "sep": "september", "oct": "october", "nov": "november", "dec": "december",
    "january": "january", "february": "february", "march": "march",
    "april": "april", "july": "july", "august": "august",
    "september": "september", "october": "october", "november": "november",
    "december": "december",
}


def _extract_year_month(filename: str) -> tuple[int | None, str | None]:
    """Extract exam year and month from a CalBar PDF filename."""
    import re as _re
    name = filename.lower()
    year_match = _re.search(r"(20\d{2})", name)
    year = int(year_match.group(1)) if year_match else None
    month = None
    for abbr, full in _MONTH_MAP.items():
        if abbr in name:
            month = full
            break
    return year, month


def _get_or_create_seed_document(session: Session, name: str, doc_type: str) -> SourceDocument:
    """Get or create a minimal source document for seeded data.
    Creates the record directly without accessing any file on disk."""
    from sqlalchemy import select as sa_select
    existing = session.scalar(
        sa_select(SourceDocument).where(
            SourceDocument.original_filename == name,
        )
    )
    if existing:
        return existing
    doc = SourceDocument(
        source_type=SourceType.BAR_REVIEW_OUTLINE.value,
        publisher="Seeded from parsed JSON",
        title=name,
        subject=doc_type,
        original_filename=name,
        local_path=f"seed://{name}",
        sha256=f"seed-{name}",
        file_size_bytes=0,
        mime_type="application/json",
        license_status=LicenseStatus.PRIVATE_USE_ONLY.value,
        redistribution_allowed=False,
        usage_notes="Loaded from pre-parsed data in git repo.",
        ingestion_status="PARSED",
        review_status="UNREVIEWED",
        metadata_json={"seeded": True},
    )
    session.add(doc)
    session.flush()
    return doc


@app.command("discover-calbar")
def discover_calbar(
    dry_run: Annotated[bool, typer.Option(help="Print the manifest without downloading.")] = True,
    output: Annotated[Path | None, typer.Option(help="Optional JSON output path.")] = None,
    year: Annotated[int | None, typer.Option(help="Filter by exam year.")] = None,
    month: Annotated[str | None, typer.Option(help="Filter by exam month, e.g. february.")] = None,
    category: Annotated[
        DocumentCategory | None,
        typer.Option(help="Filter by document category."),
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Limit discovered records.")] = None,
) -> None:
    """Discover essay-related California Bar PDF links."""
    crawler = CalBarCrawler()
    items = crawler.discover(include_categories=DEFAULT_ESSAY_CATEGORIES)
    filtered = filter_discovered_items(items, year=year, month=month, category=category, limit=limit)
    payload = [item.model_dump(mode="json") for item in filtered]
    if output:
        write_json(output, payload)
    if dry_run or not output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))
    typer.echo(f"Links discovered: {len(items)}")
    typer.echo(f"Eligible essay documents after filters: {len(filtered)}")


@app.command("download-calbar")
def download_calbar(
    year: Annotated[int | None, typer.Option(help="Filter by exam year.")] = None,
    month: Annotated[str | None, typer.Option(help="Filter by exam month.")] = None,
    category: Annotated[
        DocumentCategory | None,
        typer.Option(help="Filter by document category."),
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Limit downloads.")] = None,
    force: Annotated[bool, typer.Option(help="Force re-download even if a manifest entry exists.")] = False,
) -> None:
    """Download discovered California Bar PDFs safely and record a JSONL manifest."""
    settings = get_settings()
    items = filter_discovered_items(
        CalBarCrawler().discover(include_categories=DEFAULT_ESSAY_CATEGORIES),
        year=year,
        month=month,
        category=category,
        limit=limit,
    )
    manifest = ManifestStore(settings.manifest_dir / "calbar-downloads.jsonl")
    downloader = CalBarDownloader(data_dir=settings.data_dir)
    try:
        results = [downloader.download(item, manifest_store=manifest, force=force) for item in items]
    finally:
        downloader.close()
    _print_download_report(len(items), results)


@app.command("extract-pdf")
def extract_pdf(
    document_id: Annotated[int | None, typer.Option(help="Existing source document ID.")] = None,
    file: Annotated[Path | None, typer.Option(help="PDF file to extract without database lookup.")] = None,
    load_db: Annotated[bool, typer.Option(help="Replace document_pages/page_blocks for document-id.")] = True,
    output: Annotated[Path | None, typer.Option(help="Optional extraction JSON path.")] = None,
) -> None:
    """Extract page text and layout blocks from a PDF."""
    pdf_path, source_document = _resolve_pdf(document_id, file)
    extractor = PDFExtractor()
    extraction = extractor.extract_to_json(pdf_path, output)
    if source_document and load_db:
        with SessionLocal() as session:
            document = session.get(SourceDocument, source_document.id)
            if document is None:
                raise typer.BadParameter(f"Source document {source_document.id} was not found")
            replace_document_pages(session, document, extraction)
            session.commit()
    typer.echo(f"Pages extracted: {extraction.page_count}")


@app.command("parse-essays")
def parse_essays(
    document_id: Annotated[int | None, typer.Option(help="Existing source document ID to parse and load.")] = None,
    file: Annotated[Path | None, typer.Option(help="PDF file to parse without loading.")] = None,
    output: Annotated[Path | None, typer.Option(help="Optional parsed JSON path.")] = None,
    load_db: Annotated[bool, typer.Option(help="Load parsed records when document-id is provided.")] = True,
) -> None:
    """Parse essay questions and selected answers."""
    pdf_path, source_document = _resolve_pdf(document_id, file)
    extraction = PDFExtractor().extract(pdf_path)
    result = EssayParser().parse(
        extraction,
        source_document_id=source_document.id if source_document else None,
        exam_year=source_document.exam_year if source_document else None,
        exam_month=source_document.exam_month if source_document else None,
    )
    target = output or get_settings().parsed_dir / f"{pdf_path.stem}.essays.json"
    write_json(target, result.model_dump(mode="json"))
    if source_document and load_db:
        with SessionLocal() as session:
            document = session.get(SourceDocument, source_document.id)
            if document is None:
                raise typer.BadParameter(f"Source document {source_document.id} was not found")
            replace_document_pages(session, document, extraction)
            replace_essay_parse(session, document, result)
            session.commit()
    typer.echo(f"Essay questions parsed: {len(result.questions)}")
    typer.echo(f"Selected answers parsed: {len(result.selected_answers)}")
    typer.echo(f"Validation issues: {len(result.issues)}")


@app.command("dedupe-essay-questions")
def dedupe_essay_questions_command() -> None:
    """Remove duplicate parsed essay-question rows across source PDFs."""
    with SessionLocal() as session:
        counts = dedupe_essay_questions(session)
        session.commit()
    typer.echo(f"Duplicate groups found: {counts['duplicate_groups']}")
    typer.echo(f"Duplicate questions deleted: {counts['deleted_questions']}")
    typer.echo(f"Questions skipped for safety: {counts['skipped_questions']}")


@app.command("parse-rules")
def parse_rules(
    file: Annotated[Path, typer.Option(help="Local Trusts outline PDF.")],
    load_db: Annotated[bool, typer.Option(help="Register the source document and load parsed records.")] = True,
    output: Annotated[Path | None, typer.Option(help="Optional parsed JSON path.")] = None,
) -> None:
    """Parse a local Trusts rule-outline PDF into topics, rules, components, and spans."""
    extraction = PDFExtractor().extract(file)
    result = RuleOutlineParser().parse(extraction)
    target = output or get_settings().parsed_dir / f"{file.stem}.rules.json"
    write_json(target, result.model_dump(mode="json"))
    counts = {"topics": len(result.topics), "rules": len(result.rules), "rule_components": sum(len(r.components) for r in result.rules)}
    if load_db:
        with SessionLocal() as session:
            document = _register_outline_document(session, file, extraction.page_count)
            result.source_document_id = document.id
            replace_document_pages(session, document, extraction)
            counts = replace_rule_parse(session, document, result)
            session.commit()
            typer.echo(f"Source document ID: {document.id}")
    typer.echo(f"Rule topics parsed: {counts['topics']}")
    typer.echo(f"Rules parsed: {counts['rules']}")
    typer.echo(f"Rule components parsed: {counts['rule_components']}")


_CALBAR_RULES_SUBJECT_MAP: dict[str, str] = {
    "Agency": "Agency",
    "CA Civil": "Civil Procedure",
    "CA Evidence ": "Evidence",
    "Community Property": "Community Property",
    "CONSTITUTIONAL LAW (CA)": "Constitutional Law",
    "CONTRACTS AND SALES (CA)": "Contracts",
    "CORPORATIONS": "Corporations",
    "CRIMINAL LAW (CA)": "Criminal Law",
    "CRIMINAL PROCEDURE (CA)": "Criminal Procedure",
    "PARTNERSHIPS": "Partnerships",
    "PROFESSIONAL RESPONSIBILITY": "Professional Responsibility",
    "REAL PROPERTY (CA)": "Real Property",
    "REMEDIES": "Legal Remedies",
    "TORTS (CA)": "Torts",
    "Trust": "Trusts",
    "WILLS": "Wills",
}


@app.command("parse-all-rules")
def parse_all_rules(
    rules_dir: Annotated[Path, typer.Option(help="CalBarRules directory.")] = Path("CalBarRules"),
    dry_run: Annotated[bool, typer.Option(help="Print what would be parsed without writing to DB.")] = False,
) -> None:
    """Parse all rule-outline PDFs from CalBarRules/ into the database."""
    Base.metadata.create_all(engine)
    settings = get_settings()
    total = {"subjects": 0, "topics": 0, "rules": 0, "components": 0}

    for folder_name, subject_name in sorted(_CALBAR_RULES_SUBJECT_MAP.items()):
        folder = rules_dir / folder_name
        pdfs = sorted(folder.glob("*.pdf")) if folder.is_dir() else []
        if not pdfs:
            typer.echo(f"  SKIP {subject_name}: no PDFs in {folder}")
            continue
        pdf_path = pdfs[0]
        typer.echo(f"  Parsing {subject_name} from {pdf_path.name}...")

        if dry_run:
            extraction = PDFExtractor().extract(pdf_path)
            result = RuleOutlineParser().parse(extraction, subject_hint=subject_name)
            typer.echo(f"    -> {len(result.topics)} topics, {len(result.rules)} rules")
            total["subjects"] += 1
            total["topics"] += len(result.topics)
            total["rules"] += len(result.rules)
            total["components"] += sum(len(r.components) for r in result.rules)
            continue

        extraction = PDFExtractor().extract(pdf_path)
        result = RuleOutlineParser().parse(extraction, subject_hint=subject_name)
        json_out = settings.parsed_dir / f"{pdf_path.stem}.rules.json"
        write_json(json_out, result.model_dump(mode="json"))

        with SessionLocal() as session:
            document = _register_outline_document(session, pdf_path, extraction.page_count, subject=subject_name)
            result.source_document_id = document.id
            replace_document_pages(session, document, extraction)
            counts = replace_rule_parse(session, document, result)
            session.commit()
            typer.echo(f"    -> {counts['topics']} topics, {counts['rules']} rules, {counts['rule_components']} components")
            total["subjects"] += 1
            total["topics"] += counts["topics"]
            total["rules"] += counts["rules"]
            total["components"] += counts["rule_components"]

    typer.echo(f"\nDone: {total['subjects']} subjects, {total['topics']} topics, {total['rules']} rules, {total['components']} components")


@app.command("validate-document")
def validate_document(document_id: Annotated[int, typer.Option(help="Source document ID.")]) -> None:
    """Print a validation summary for one source document."""
    with SessionLocal() as session:
        typer.echo(json.dumps(document_validation_summary(session, document_id), indent=2, sort_keys=True, default=str))


@app.command("export-review")
def export_review(
    document_id: Annotated[int, typer.Option(help="Source document ID.")],
    output: Annotated[Path | None, typer.Option(help="Output JSON path.")] = None,
) -> None:
    """Export source, parsed records, and source spans to JSON for human review."""
    target = output or get_settings().parsed_dir / f"source-document-{document_id}.review.json"
    with SessionLocal() as session:
        export_document_review(session, document_id, target)
    typer.echo(f"Review export written: {target}")


@app.command("export-review-html")
def export_review_html(
    document_id: Annotated[int, typer.Option(help="Source document ID.")],
    output: Annotated[Path | None, typer.Option(help="Output HTML path.")] = None,
) -> None:
    """Export one source document as a local read-only HTML review report."""
    target = output or get_settings().parsed_dir / f"source-document-{document_id}.review.html"
    with SessionLocal() as session:
        export_document_review_html(session, document_id, target)
    typer.echo(f"HTML review export written: {target}")


@app.command("export-data-browser-html")
def export_data_browser_html_command(
    output: Annotated[Path | None, typer.Option(help="Output HTML path.")] = None,
    include_rules: Annotated[bool, typer.Option(help="Include parsed legal rules in the browser.")] = True,
) -> None:
    """Export a local read-only browser for loaded questions, answers, rules, and sources."""
    target = output or get_settings().parsed_dir / "parsed-data-browser.html"
    with SessionLocal() as session:
        export_data_browser_html(session, target, include_rules=include_rules)
    typer.echo(f"Parsed data browser written: {target}")


@app.command("run-pipeline")
def run_pipeline(
    year: Annotated[int, typer.Option(help="Exam year.")] = 2017,
    month: Annotated[str, typer.Option(help="Exam month.")] = "february",
    limit: Annotated[int, typer.Option(help="Download limit for matching official PDFs.")] = 1,
    trusts_file: Annotated[Path | None, typer.Option(help="Local Trusts outline PDF.")] = None,
) -> None:
    """Run the first vertical slice: Feb 2017 official PDF plus one Trusts outline."""
    Base.metadata.create_all(engine)
    settings = get_settings()
    crawler = CalBarCrawler()
    discovered = crawler.discover(include_categories={DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS})
    official_items = filter_discovered_items(
        discovered,
        year=year,
        month=month,
        category=DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS,
        limit=limit,
    )
    manifest = ManifestStore(settings.manifest_dir / "calbar-downloads.jsonl")
    downloader = CalBarDownloader(data_dir=settings.data_dir)
    official_results = []
    try:
        for item in official_items:
            official_results.append(downloader.download(item, manifest_store=manifest))
    finally:
        downloader.close()

    pages_extracted = 0
    questions_parsed = 0
    answers_parsed = 0
    low_confidence_records = 0
    rule_counts = {"topics": 0, "rules": 0, "rule_components": 0}
    document_ids: list[int] = []

    with SessionLocal() as session:
        for item, result in zip(official_items, official_results, strict=False):
            if result.status == "failed" or result.local_path is None:
                continue
            document = _register_official_document(session, item, result.local_path)
            document_ids.append(document.id)
            extraction = PDFExtractor().extract(result.local_path)
            write_json(settings.extracted_dir / f"{result.local_path.stem}.pages.json", extraction.model_dump(mode="json"))
            replace_document_pages(session, document, extraction)
            parse_result = EssayParser().parse(
                extraction,
                source_document_id=document.id,
                exam_year=item.year,
                exam_month=item.month,
            )
            write_json(settings.parsed_dir / f"{result.local_path.stem}.essays.json", parse_result.model_dump(mode="json"))
            replace_essay_parse(session, document, parse_result)
            pages_extracted += extraction.page_count
            questions_parsed += len(parse_result.questions)
            answers_parsed += len(parse_result.selected_answers)
            low_confidence_records += len(
                [q for q in parse_result.questions if q.parse_confidence < 0.8]
            ) + len([a for a in parse_result.selected_answers if a.parse_confidence < 0.8])

        outline_path = trusts_file or _find_default_trusts_outline()
        if outline_path:
            extraction = PDFExtractor().extract(outline_path)
            outline_document = _register_outline_document(session, outline_path, extraction.page_count)
            document_ids.append(outline_document.id)
            write_json(settings.extracted_dir / f"{outline_path.stem}.pages.json", extraction.model_dump(mode="json"))
            replace_document_pages(session, outline_document, extraction)
            rules = RuleOutlineParser().parse(extraction, source_document_id=outline_document.id)
            write_json(settings.parsed_dir / f"{outline_path.stem}.rules.json", rules.model_dump(mode="json"))
            rule_counts = replace_rule_parse(session, outline_document, rules)
            pages_extracted += extraction.page_count
            low_confidence_records += len([rule for rule in rules.rules if rule.parse_confidence < 0.75])
        session.commit()

    _print_download_report(len(official_items), official_results)
    typer.echo(f"Pages extracted: {pages_extracted}")
    typer.echo(f"Essay questions parsed: {questions_parsed}")
    typer.echo(f"Selected answers parsed: {answers_parsed}")
    typer.echo("Unmatched answers: see parsed JSON validation issues")
    typer.echo(f"Low-confidence records: {low_confidence_records}")
    typer.echo(f"Rule topics parsed: {rule_counts['topics']}")
    typer.echo(f"Rules parsed: {rule_counts['rules']}")
    typer.echo(f"Rule components parsed: {rule_counts['rule_components']}")
    typer.echo(f"Source document IDs: {document_ids}")


@app.command("parse-essay-template")
def parse_essay_template(
    file: Annotated[Path | None, typer.Option(help="Schimmel PDF file to parse.")] = None,
    document_id: Annotated[int | None, typer.Option(help="Existing source document ID.")] = None,
    output: Annotated[Path | None, typer.Option(help="Output directory for parsed JSON files.")] = None,
    load_db: Annotated[bool, typer.Option(help="Register source document and load parsed records.")] = True,
    dry_run: Annotated[bool, typer.Option(help="Parse without storing to database.")] = False,
    subject_filter: Annotated[str | None, typer.Option(help="Only parse specific subject (e.g. Contracts).")] = None,
    page_start: Annotated[int | None, typer.Option(help="Start page (1-based).")] = None,
    page_end: Annotated[int | None, typer.Option(help="End page (inclusive).")] = None,
    force: Annotated[bool, typer.Option(help="Force reprocessing even if already parsed.")] = False,
) -> None:
    """Parse a Schimmel essay-template PDF into structured template hierarchy."""
    settings = get_settings()
    pdf_path, source_document = _resolve_pdf(document_id, file)

    parser = SchimmelTemplateParser()
    output_dir = output or settings.parsed_dir / "schimmel"
    output_dir.mkdir(parents=True, exist_ok=True)

    document = parser.parse_from_pdf(pdf_path, output_dir=output_dir, dry_run=dry_run)

    # Save full document candidate
    candidate_path = output_dir / f"{pdf_path.stem}.candidate.json"
    _write_candidate_json(document, candidate_path)

    # Print summary
    summary = parser.validator.produce_summary(document, document.validation_findings)
    typer.echo(f"\nDocument: {pdf_path.name}")
    typer.echo(f"Subjects detected: {summary['subjects_detected']}")
    typer.echo(f"Template nodes created: {summary['template_nodes_created']}")
    typer.echo(f"Rule candidates created: {summary['rule_candidates_created']}")
    typer.echo(f"Cross-references found: {summary['cross_references_found']}")
    typer.echo(f"Abbreviations detected: {summary['abbreviations_detected']}")
    typer.echo(f"Low-confidence nodes: {summary['low_confidence_nodes']}")
    typer.echo(f"Validation errors: {summary['validation_errors']}")
    typer.echo(f"Review warnings: {summary['review_warnings']}")

    if summary["validation_errors"] > 0:
        for err in summary["errors"]:
            typer.echo(f"  ERROR [{err['code']}]: {err['message']}")
    if summary["review_warnings"] > 0:
        for warn in summary["warnings"]:
            typer.echo(f"  WARNING [{warn['code']}]: {warn['message']}")

    # Load to database
    if load_db and not dry_run:
        with SessionLocal() as session:
            if source_document is None:
                source_document = _register_schimmel_document(session, pdf_path, document.page_count)
            else:
                doc = session.get(SourceDocument, source_document.id)
                if doc is None:
                    raise typer.BadParameter(f"Source document {source_document.id} was not found")
                source_document = doc

            extraction = PDFExtractor().extract(pdf_path)
            replace_document_pages(session, source_document, extraction)
            counts = replace_essay_template_parse(
                session, source_document, document, parser.parser_version
            )
            session.commit()
            typer.echo(f"\nDatabase load complete:")
            typer.echo(f"  Source document ID: {source_document.id}")
            typer.echo(f"  Templates stored: {counts['templates']}")
            typer.echo(f"  Nodes stored: {counts['nodes']}")
            typer.echo(f"  Rule candidates stored: {counts['rule_candidates']}")
            typer.echo(f"  Cross-references stored: {counts['cross_references']}")
            typer.echo(f"  Abbreviations stored: {counts['abbreviations']}")


@app.command("validate-essay-template")
def validate_essay_template(
    document_id: Annotated[int, typer.Option(help="Source document ID.")],
) -> None:
    """Validate essay template data for a source document."""
    with SessionLocal() as session:
        counts = get_template_counts(session, document_id)
        typer.echo(json.dumps(counts, indent=2))


@app.command("export-essay-template")
def export_essay_template(
    subject: Annotated[str, typer.Option(help="Subject name (e.g. Contracts).")],
    format: Annotated[str, typer.Option(help="Output format: json or text.")] = "json",
    output: Annotated[Path | None, typer.Option(help="Output path.")] = None,
) -> None:
    """Export an essay template for a subject."""
    with SessionLocal() as session:
        templates = get_subject_templates(session, subject)
        if not templates:
            typer.echo(f"No templates found for subject: {subject}")
            raise typer.Exit(1)

        template = templates[0]
        nodes = session.scalars(
            select(TemplateNode).where(
                TemplateNode.essay_template_id == template.id,
                TemplateNode.parent_node_id.is_(None),
            ).order_by(TemplateNode.display_order)
        ).all()

        if format == "json":
            payload = _build_export_json(template, nodes, session)
            target = output or get_settings().parsed_dir / "schimmel" / f"{subject.lower().replace(' ', '_')}_template.json"
            write_json(target, payload)
            typer.echo(f"Exported to: {target}")
        else:
            text = _build_export_text(template, nodes, session)
            if output:
                output.write_text(text)
            else:
                typer.echo(text)


@app.command("inspect-template-tree")
def inspect_template_tree(
    subject: Annotated[str, typer.Option(help="Subject name (e.g. Contracts).")],
) -> None:
    """Print a readable tree view of a subject's essay template."""
    with SessionLocal() as session:
        templates = get_subject_templates(session, subject)
        if not templates:
            typer.echo(f"No templates found for subject: {subject}")
            raise typer.Exit(1)

        template = templates[0]
        nodes = session.scalars(
            select(TemplateNode).where(
                TemplateNode.essay_template_id == template.id,
                TemplateNode.parent_node_id.is_(None),
            ).order_by(TemplateNode.display_order)
        ).all()

        text = _build_export_text(template, nodes, session)
        typer.echo(text)


@app.command("resolve-template-cross-references")
def resolve_template_cross_references(
    document_id: Annotated[int, typer.Option(help="Source document ID.")],
) -> None:
    """Attempt to resolve cross-references for a document's templates."""
    with SessionLocal() as session:
        cross_refs = session.scalars(
            select(TemplateCrossReference).where(
                TemplateCrossReference.source_template_node_id.in_(
                    select(TemplateNode.id).where(
                        TemplateNode.essay_template_id.in_(
                            select(EssayTemplate.id).where(
                                EssayTemplate.source_document_id == document_id
                            )
                        )
                    )
                )
            )
        ).all()

        resolved = 0
        for cr in cross_refs:
            if cr.resolution_status == "UNRESOLVED":
                cr.resolution_status = "NEEDS_REVIEW"
                resolved += 1

        session.commit()
        typer.echo(f"Cross-references: {len(cross_refs)} total, {resolved} marked for review")


def _resolve_pdf(document_id: int | None, file: Path | None) -> tuple[Path, SourceDocument | None]:
    if file is not None:
        return file, None
    if document_id is None:
        raise typer.BadParameter("Provide either --document-id or --file")
    with SessionLocal() as session:
        document = session.get(SourceDocument, document_id)
        if document is None:
            raise typer.BadParameter(f"Source document {document_id} was not found")
        session.expunge(document)
        return Path(document.local_path), document


def _register_official_document(session: Session, item: CalBarDiscoveryItem, local_path: Path) -> SourceDocument:
    source_type = (
        SourceType.OFFICIAL_SELECTED_ANSWERS.value
        if item.document_category == DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS
        else SourceType.OFFICIAL_EXAM.value
    )
    category_title = item.document_category.value.replace("_", " ").title()
    title = f"{item.administration_label or item.link_text} {category_title}"
    return register_source_document(
        session,
        local_path=local_path,
        source_type=source_type,
        publisher="State Bar of California",
        title=title,
        source_url=str(item.source_url),
        jurisdiction=item.jurisdiction,
        exam_year=item.year,
        exam_month=item.month,
        document_category=item.document_category.value,
        license_status=LicenseStatus.OFFICIAL_PUBLIC.value,
        redistribution_allowed=False,
        usage_notes="Official public exam material; redistribution status not legally determined by this app.",
        metadata_json=item.model_dump(mode="json"),
    )


def _register_outline_document(
    session: Session, file: Path, page_count: int | None = None, subject: str = "Trusts",
) -> SourceDocument:
    return register_source_document(
        session,
        local_path=file,
        source_type=SourceType.BAR_REVIEW_OUTLINE.value,
        publisher="Local bar review outline",
        title=f"{subject} outline",
        subject=subject,
        original_filename=file.name,
        page_count=page_count,
        license_status=LicenseStatus.PRIVATE_USE_ONLY.value,
        redistribution_allowed=False,
        usage_notes="Local commercial/user-provided outline. Keep source PDF and extracted text private.",
        metadata_json={"input_path": str(file)},
    )


def _find_default_trusts_outline() -> Path | None:
    candidates = [
        *Path("data/input").glob("*[Tt]rust*.pdf"),
        *Path("CalBarRules/FINAL REVIEW OUTLINES & FREQUENCY CHARTS").glob("*[Tt]rust*.pdf"),
        *Path("CalBarRules/Trust").glob("*.pdf"),
    ]
    return candidates[0] if candidates else None


def _print_download_report(requested: int, results: Sequence[DownloadManifestEntry]) -> None:
    statuses = [getattr(result, "status", "") for result in results]
    typer.echo(f"Eligible essay documents: {requested}")
    typer.echo(f"Documents downloaded: {statuses.count('downloaded') + statuses.count('changed')}")
    typer.echo(f"Documents unchanged: {statuses.count('unchanged')}")
    typer.echo(f"Download failures: {statuses.count('failed')}")


def _register_schimmel_document(session: Session, file: Path, page_count: int | None = None) -> SourceDocument:
    """Register the Schimmel PDF as a private source document."""
    return register_source_document(
        session,
        local_path=file,
        source_type=SourceType.BAR_REVIEW_OUTLINE.value,
        publisher="Prof. Sarah Schimmel",
        title="Schimmel Templates - Bullet Version",
        subject="Multi-Subject",
        original_filename=file.name,
        page_count=page_count,
        license_status=LicenseStatus.PRIVATE_USE_ONLY.value,
        redistribution_allowed=False,
        usage_notes="Schimmel essay template PDF. Keep source PDF and extracted text private.",
        metadata_json={"input_path": str(file), "parser": "schimmel_template_parser"},
    )


def _write_candidate_json(document: SchimmelDocumentCandidate, path: Path) -> None:
    """Write a SchimmelDocumentCandidate to JSON for review."""
    def node_to_dict(node):
        return {
            "title": node.title,
            "type": node.node_type,
            "depth": node.depth,
            "page": node.page_number,
            "confidence": node.parse_confidence,
            "jurisdiction": node.jurisdiction_scope,
            "rule_candidates": [
                {
                    "raw_text": r.raw_rule_text[:200],
                    "elements": r.elements,
                    "exceptions": r.exceptions,
                }
                for r in node.rule_candidates
            ],
            "cross_references": [
                {"target": cr.target_text, "resolution": cr.resolution_status}
                for cr in node.cross_references
            ],
            "children": [node_to_dict(c) for c in node.children],
        }

    payload = {
        "source_path": document.source_path,
        "sha256": document.sha256,
        "page_count": document.page_count,
        "subjects": [
            {
                "name": s.normalized_name,
                "pages": f"{s.start_page}-{s.end_page}",
                "nodes": [node_to_dict(n) for n in s.candidates] if s.candidates else [],
            }
            for s in document.subjects
        ],
        "abbreviations": [
            {"abbr": a.abbreviation, "term": a.normalized_term, "confidence": a.confidence}
            for a in document.abbreviations
        ],
    }
    write_json(path, payload)


def _build_export_json(template: EssayTemplate, root_nodes: list[TemplateNode], session: Session) -> dict:
    """Build a review-friendly JSON export of a template tree."""
    def node_to_dict(node: TemplateNode) -> dict:
        children = session.scalars(
            select(TemplateNode).where(
                TemplateNode.parent_node_id == node.id
            ).order_by(TemplateNode.display_order)
        ).all()
        rules = session.scalars(
            select(TemplateRuleCandidate).where(
                TemplateRuleCandidate.template_node_id == node.id
            )
        ).all()
        result = {
            "title": node.title,
            "type": node.node_type,
            "depth": node.depth,
        }
        if rules:
            result["rule_candidates"] = [
                {
                    "raw_text": r.raw_rule_text,
                    "normalized_text": r.normalized_rule_text,
                }
                for r in rules
            ]
        if children:
            result["children"] = [node_to_dict(c) for c in children]
        return result

    return {
        "subject": template.name.replace(" Essay Template", ""),
        "template": {
            "title": template.name,
            "children": [node_to_dict(n) for n in root_nodes],
        },
    }


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Bind host.")] = "0.0.0.0",
    port: Annotated[int, typer.Option(help="Bind port.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Enable auto-reload for development.")] = False,
) -> None:
    """Start the web application server."""
    import uvicorn

    Base.metadata.create_all(engine)
    uvicorn.run("app.web.app:app", host=host, port=port, reload=reload)


def _build_export_text(template: EssayTemplate, root_nodes: list[TemplateNode], session: Session) -> str:
    """Build a readable tree view of a template."""
    lines: list[str] = [f"\n{template.name}\n{'=' * len(template.name)}\n"]

    def walk(node: TemplateNode, prefix: str = "", is_last: bool = True) -> None:
        connector = "└── " if is_last else "├── "
        node_prefix = f"{prefix}{connector}" if node.depth > 0 else ""
        label = node.title
        if len(label) > 100:
            label = label[:97] + "..."
        lines.append(f"{node_prefix}{label}")

        rules = session.scalars(
            select(TemplateRuleCandidate).where(
                TemplateRuleCandidate.template_node_id == node.id
            )
        ).all()
        for rule in rules:
            rule_text = rule.raw_rule_text[:80] + "..." if len(rule.raw_rule_text) > 80 else rule.raw_rule_text
            child_prefix = f"{prefix}    " if is_last else f"{prefix}│   "
            lines.append(f"{child_prefix}  • {rule_text}")

        children = session.scalars(
            select(TemplateNode).where(
                TemplateNode.parent_node_id == node.id
            ).order_by(TemplateNode.display_order)
        ).all()
        for i, child in enumerate(children):
            child_is_last = i == len(children) - 1
            child_prefix = f"{prefix}    " if is_last else f"{prefix}│   "
            walk(child, child_prefix, child_is_last)

    for node in root_nodes:
        walk(node)

    return "\n".join(lines)
