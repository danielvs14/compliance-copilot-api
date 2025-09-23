"""Initial Postgres schema"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20240710_0001_initial_postgres"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    requirement_status = postgresql.ENUM(
        "OPEN",
        "REVIEW",
        "DONE",
        name="requirement_status",
        create_type=False,
    )
    requirement_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("storage_url", sa.String(), nullable=True),
        sa.Column("text_excerpt", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title_en", sa.String(), nullable=False),
        sa.Column("title_es", sa.String(), nullable=False),
        sa.Column("description_en", sa.String(), nullable=False),
        sa.Column("description_es", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("frequency", sa.String(), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", requirement_status, server_default="OPEN", nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("trade", sa.String(), nullable=False, server_default="electrical"),
        sa.Column("attributes", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("data", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "org_requirement_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("requirements_created_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requirements_completed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_time_histogram", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("org_requirement_metrics")
    op.drop_table("events")
    op.drop_table("requirements")
    op.drop_table("documents")

    requirement_status = sa.Enum("OPEN", "REVIEW", "DONE", name="requirement_status")
    requirement_status.drop(op.get_bind(), checkfirst=True)
