"""Week 3 reminders scheduler tables"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20240724_0003_week3_reminders"
down_revision = "20240717_0002_week2_auth_storage"
branch_labels = None
depends_on = None


reminder_status_enum = postgresql.ENUM(
    "PENDING", "SENT", "FAILED", name="reminder_status", create_type=False
)


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("next_due", sa.DateTime(timezone=True), nullable=True),
    )

    reminder_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "reminder_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_offset_days", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient_email", sa.String(), nullable=False),
        sa.Column("recipient_locale", sa.String(), nullable=False, server_default="en"),
        sa.Column(
            "status",
            reminder_status_enum,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "target_type",
            "target_id",
            "recipient_email",
            "reminder_offset_days",
            "target_due_at",
            name="uq_reminder_jobs_target_offset",
        ),
    )
    op.create_index("ix_reminder_jobs_org_id", "reminder_jobs", ["org_id"], unique=False)
    op.create_index("ix_reminder_jobs_run_at", "reminder_jobs", ["run_at"], unique=False)

    op.add_column(
        "org_requirement_metrics",
        sa.Column(
            "reminders_scheduled_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "org_requirement_metrics",
        sa.Column(
            "reminders_sent_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "org_requirement_metrics",
        sa.Column(
            "reminders_failed_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "org_requirement_metrics",
        sa.Column(
            "overdue_completion_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "org_requirement_metrics",
        sa.Column(
            "post_reminder_completion_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "org_requirement_metrics",
        sa.Column(
            "overdue_completion_histogram",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "org_requirement_metrics",
        sa.Column(
            "post_reminder_completion_histogram",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("org_requirement_metrics", "post_reminder_completion_histogram")
    op.drop_column("org_requirement_metrics", "overdue_completion_histogram")
    op.drop_column("org_requirement_metrics", "post_reminder_completion_total")
    op.drop_column("org_requirement_metrics", "overdue_completion_total")
    op.drop_column("org_requirement_metrics", "reminders_failed_total")
    op.drop_column("org_requirement_metrics", "reminders_sent_total")
    op.drop_column("org_requirement_metrics", "reminders_scheduled_total")

    op.drop_index("ix_reminder_jobs_run_at", table_name="reminder_jobs")
    op.drop_index("ix_reminder_jobs_org_id", table_name="reminder_jobs")
    op.drop_table("reminder_jobs")

    op.drop_column("requirements", "next_due")

    op.execute("DROP TYPE IF EXISTS reminder_status")
