"""Introduce recurrence fields and requirement history

Revision ID: 20241010_0005
Revises: 20241002_0004_org_primary_trade
Create Date: 2024-10-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20241010_0005"
down_revision = "20241002_0004"
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


FREQUENCY_MIGRATION_MAP: dict[str, str] = {
    "before each use": "BEFORE_EACH_USE",
    "daily": "DAILY",
    "weekly": "WEEKLY",
    "monthly": "MONTHLY",
    "quarterly": "QUARTERLY",
    "annual": "ANNUAL",
    "yearly": "ANNUAL",
    "one time": "ONE_TIME",
    "one-time": "ONE_TIME",
}


def upgrade() -> None:
    bind = op.get_bind()
    requirement_frequency.create(bind, checkfirst=True)
    requirement_anchor_type.create(bind, checkfirst=True)

    op.add_column(
        "requirements",
        sa.Column("anchor_type", requirement_anchor_type, nullable=True),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "anchor_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.add_column(
        "requirements",
        sa.Column("frequency_new", requirement_frequency, nullable=True),
    )

    migration_case = "CASE\n"
    for legacy, enum_value in FREQUENCY_MIGRATION_MAP.items():
        migration_case += (
            "        WHEN lower(frequency) = '{legacy}' THEN '{enum}'::requirement_frequency\n".format(
                legacy=legacy,
                enum=enum_value,
            )
        )
    migration_case += "        ELSE NULL\n    END"

    op.execute(
        sa.text(
            """
            UPDATE requirements
            SET frequency_new = {case}
            """.format(case=migration_case)
        )
    )

    op.alter_column("requirements", "frequency", new_column_name="frequency_old")
    op.alter_column("requirements", "frequency_new", new_column_name="frequency")
    op.drop_column("requirements", "frequency_old")

    op.create_table(
        "requirement_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("completed_by", sa.String(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("photo_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    op.create_index(
        "ix_requirement_history_requirement_id",
        "requirement_history",
        ["requirement_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_requirement_history_requirement_id", table_name="requirement_history")
    op.drop_table("requirement_history")

    op.add_column(
        "requirements",
        sa.Column("frequency_old", sa.String(), nullable=True),
    )

    op.execute(
        sa.text(
            """
            UPDATE requirements
            SET frequency_old = CASE
                WHEN frequency = 'BEFORE_EACH_USE' THEN 'before each use'
                WHEN frequency = 'DAILY' THEN 'daily'
                WHEN frequency = 'WEEKLY' THEN 'weekly'
                WHEN frequency = 'MONTHLY' THEN 'monthly'
                WHEN frequency = 'QUARTERLY' THEN 'quarterly'
                WHEN frequency = 'ANNUAL' THEN 'annual'
                WHEN frequency = 'EVERY_N_DAYS' THEN 'every_n_days'
                WHEN frequency = 'EVERY_N_WEEKS' THEN 'every_n_weeks'
                WHEN frequency = 'EVERY_N_MONTHS' THEN 'every_n_months'
                WHEN frequency = 'ONE_TIME' THEN 'one time'
                ELSE NULL
            END
            """
        )
    )

    op.alter_column("requirements", "frequency", new_column_name="frequency_enum")
    op.alter_column("requirements", "frequency_old", new_column_name="frequency")
    op.drop_column("requirements", "frequency_enum")

    op.drop_column("requirements", "anchor_value")
    op.drop_column("requirements", "anchor_type")

    requirement_frequency.drop(bind, checkfirst=True)
    requirement_anchor_type.drop(bind, checkfirst=True)
