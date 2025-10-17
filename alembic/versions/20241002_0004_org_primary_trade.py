"""Add primary trade to orgs

Revision ID: 20241002_0004
Revises: 20240724_0003_week3_reminders
Create Date: 2024-10-02 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20241002_0004"
down_revision = "20240724_0003_week3_reminders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("primary_trade", sa.String(), nullable=False, server_default="electrical"),
    )
    op.execute("UPDATE orgs SET primary_trade = COALESCE(primary_trade, 'electrical')")
    op.alter_column("orgs", "primary_trade", server_default=None)


def downgrade() -> None:
    op.drop_column("orgs", "primary_trade")
