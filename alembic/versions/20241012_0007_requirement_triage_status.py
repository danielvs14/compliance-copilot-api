"""Add PENDING_REVIEW and READY requirement statuses"""

from __future__ import annotations

from alembic import op

revision = "20241012_0007"
down_revision = "20241011_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE requirement_status ADD VALUE IF NOT EXISTS 'PENDING_REVIEW'")
    op.execute("ALTER TYPE requirement_status ADD VALUE IF NOT EXISTS 'READY'")


def downgrade() -> None:
    # Downgrade drops the added values by recreating the enum.
    op.execute("ALTER TYPE requirement_status RENAME TO requirement_status_old")
    op.execute("CREATE TYPE requirement_status AS ENUM ('OPEN', 'REVIEW', 'DONE')")
    op.execute(
        "ALTER TABLE requirements ALTER COLUMN status TYPE requirement_status USING "
        "status::text::requirement_status"
    )
    op.execute("DROP TYPE requirement_status_old")
