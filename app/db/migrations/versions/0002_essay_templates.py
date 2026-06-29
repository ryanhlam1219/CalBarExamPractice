"""add essay template models

Revision ID: 0002_essay_templates
Revises: 0001_initial_schema
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_essay_templates"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_abbreviations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("legal_subject_id", sa.Integer(), nullable=True),
        sa.Column("abbreviation", sa.String(length=100), nullable=False),
        sa.Column("normalized_term", sa.String(length=500), nullable=False),
        sa.Column("context_notes", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["legal_subject_id"], ["legal_subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_document_id", "abbreviation", "legal_subject_id", name="uq_doc_abbreviations_doc_abbr_subj"
        ),
    )
    op.create_table(
        "essay_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("legal_subject_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("jurisdiction_scope", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("parse_confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["legal_subject_id"], ["legal_subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "legal_subject_id", "source_document_id", "version",
            name="uq_essay_templates_subject_doc_version",
        ),
    )
    op.create_table(
        "canonical_issue_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("legal_subject_id", sa.Integer(), nullable=False),
        sa.Column("parent_candidate_id", sa.Integer(), nullable=True),
        sa.Column("source_template_node_id", sa.Integer(), nullable=True),
        sa.Column("proposed_name", sa.String(length=500), nullable=False),
        sa.Column("normalized_name", sa.String(length=500), nullable=False),
        sa.Column("proposed_issue_type", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["legal_subject_id"], ["legal_subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["canonical_issue_candidates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "legal_subject_id", "proposed_name", name="uq_canonical_issue_candidates_subject_name"
        ),
    )
    op.create_table(
        "template_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("essay_template_id", sa.Integer(), nullable=False),
        sa.Column("parent_node_id", sa.Integer(), nullable=True),
        sa.Column("canonical_issue_id", sa.Integer(), nullable=True),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("jurisdiction_scope", sa.String(length=100), nullable=True),
        sa.Column("parse_confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["canonical_issue_id"], ["canonical_issue_candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["essay_template_id"], ["essay_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_node_id"], ["template_nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "essay_template_id", "parent_node_id", "display_order", name="uq_template_nodes_order"
        ),
    )
    op.create_table(
        "template_rule_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_node_id", sa.Integer(), nullable=False),
        sa.Column("legal_subject_id", sa.Integer(), nullable=False),
        sa.Column("canonical_issue_id", sa.Integer(), nullable=True),
        sa.Column("raw_rule_text", sa.Text(), nullable=False),
        sa.Column("normalized_rule_text", sa.Text(), nullable=True),
        sa.Column("jurisdiction_scope", sa.String(length=100), nullable=False),
        sa.Column("rule_variant", sa.String(length=64), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("start_page", sa.Integer(), nullable=False),
        sa.Column("end_page", sa.Integer(), nullable=False),
        sa.Column("parse_confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["canonical_issue_id"], ["canonical_issue_candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["legal_subject_id"], ["legal_subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_node_id"], ["template_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_node_id", "rule_variant", "parser_version",
            name="uq_template_rule_candidates_node_variant",
        ),
    )
    op.create_table(
        "template_cross_references",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_template_node_id", sa.Integer(), nullable=False),
        sa.Column("target_template_node_id", sa.Integer(), nullable=True),
        sa.Column("target_subject_id", sa.Integer(), nullable=True),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("resolution_status", sa.String(length=64), nullable=False),
        sa.Column("parse_confidence", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["source_template_node_id"], ["template_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_template_node_id"], ["template_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_subject_id"], ["legal_subjects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key(
        "fk_canonical_issue_source_template_node",
        "canonical_issue_candidates", "template_nodes",
        ["source_template_node_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_canonical_issue_source_template_node", "canonical_issue_candidates", type_="foreignkey")
    op.drop_table("template_cross_references")
    op.drop_table("template_rule_candidates")
    op.drop_table("template_nodes")
    op.drop_table("canonical_issue_candidates")
    op.drop_table("essay_templates")
    op.drop_table("document_abbreviations")