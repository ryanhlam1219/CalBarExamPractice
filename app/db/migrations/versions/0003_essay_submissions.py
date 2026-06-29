"""add essay submission and analysis models

Revision ID: 0003_essay_submissions
Revises: 0002_essay_templates
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_essay_submissions"
down_revision = "0002_essay_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "essay_submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("essay_question_id", sa.Integer(), nullable=False),
        sa.Column("essay_text", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_spent_seconds", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["essay_question_id"], ["essay_questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "essay_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("essay_submission_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("issue_spotting_score", sa.Float(), nullable=False),
        sa.Column("rule_statements_score", sa.Float(), nullable=False),
        sa.Column("fact_application_score", sa.Float(), nullable=False),
        sa.Column("organization_score", sa.Float(), nullable=False),
        sa.Column("feedback_json", sa.JSON(), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["essay_submission_id"], ["essay_submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["essay_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("essay_submission_id", name="uq_essay_analyses_submission"),
    )


def downgrade() -> None:
    op.drop_table("essay_analyses")
    op.drop_table("essay_submissions")
