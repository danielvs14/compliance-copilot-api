"""Create known document templates"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20241011_0006"
down_revision = "20241010_0005"
branch_labels = None
depends_on = None


requirement_frequency = postgresql.ENUM(
    "ONE_TIME",
    "BEFORE_EACH_USE",
    "DAILY",
    "WEEKLY",
    "MONTHLY",
    "QUARTERLY",
    "ANNUAL",
    "EVERY_N_DAYS",
    "EVERY_N_WEEKS",
    "EVERY_N_MONTHS",
    name="requirement_frequency",
    create_type=False,
)

requirement_anchor_type = postgresql.ENUM(
    "UPLOAD_DATE",
    "ISSUE_DATE",
    "CALENDAR",
    "FIRST_COMPLETION",
    "CUSTOM_DATE",
    name="requirement_anchor_type",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "document_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("trade", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False, unique=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "requirement_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("document_template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title_en", sa.String(), nullable=False),
        sa.Column("title_es", sa.String(), nullable=False),
        sa.Column("description_en", sa.String(), nullable=False),
        sa.Column("description_es", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("frequency", requirement_frequency, nullable=True),
        sa.Column("anchor_type", requirement_anchor_type, nullable=True),
        sa.Column("anchor_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_index(
        "ix_requirement_templates_document_template_id",
        "requirement_templates",
        ["document_template_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_requirement_templates_document_template_id", table_name="requirement_templates")
    op.drop_table("requirement_templates")
    op.drop_table("document_templates")
