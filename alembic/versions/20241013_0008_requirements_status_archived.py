"""Add ARCHIVED status"""

from __future__ import annotations

from alembic import op

revision = "20241013_0008"
down_revision = "20241012_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE requirement_status ADD VALUE IF NOT EXISTS 'ARCHIVED'")


def downgrade() -> None:
    op.execute("ALTER TYPE requirement_status RENAME TO requirement_status_old")
    op.execute("CREATE TYPE requirement_status AS ENUM ('OPEN', 'REVIEW', 'DONE')")
    op.execute(
        "ALTER TABLE requirements ALTER COLUMN status TYPE requirement_status USING "
        "status::text::requirement_status"
    )
    op.execute("DROP TYPE requirement_status_old")
