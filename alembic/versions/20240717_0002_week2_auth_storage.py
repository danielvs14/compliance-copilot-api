"""Week 2 auth, tenancy, storage tables"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20240717_0002_week2_auth_storage"
down_revision = "20240710_0001_initial_postgres"
branch_labels = None
depends_on = None


membership_role_enum = postgresql.ENUM(
    "owner", "admin", "member", name="membership_role", create_type=False
)


def upgrade() -> None:
    op.execute("DROP TYPE IF EXISTS membership_role")
    membership_role_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "orgs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_orgs_slug"),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("preferred_locale", sa.String(), nullable=False, server_default="en"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            membership_role_enum,
            nullable=False,
            server_default="member",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
    )

    op.create_table(
        "login_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False, server_default="login"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_login_tokens_token_hash"),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_token_hash", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_token_hash", name="uq_user_sessions_token"),
    )

    op.create_table(
        "permits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("permit_number", sa.String(), nullable=True),
        sa.Column("permit_type", sa.String(), nullable=True),
        sa.Column("jurisdiction", sa.String(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_url", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_permits_org_id", "permits", ["org_id"], unique=False)

    op.create_table(
        "training_certs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_name", sa.String(), nullable=False),
        sa.Column("certification_type", sa.String(), nullable=False),
        sa.Column("authority", sa.String(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_url", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_training_certs_org_id", "training_certs", ["org_id"], unique=False)

    bind = op.get_bind()
    existing_ids: set[str] = set()
    for table_name in ["documents", "requirements", "events", "org_requirement_metrics"]:
        rows = bind.execute(sa.text(f"SELECT DISTINCT org_id FROM {table_name} WHERE org_id IS NOT NULL")).fetchall()
        existing_ids.update(str(row[0]) for row in rows if row[0] is not None)

    for org_id in existing_ids:
        bind.execute(
            sa.text(
                "INSERT INTO orgs (id, name, slug) VALUES (:id, :name, NULL) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": uuid.UUID(org_id), "name": "Legacy Org"},
        )

    # documents
    op.create_index("ix_documents_org_id", "documents", ["org_id"], unique=False)
    op.create_foreign_key(
        "fk_documents_org_id",
        "documents",
        "orgs",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # requirements
    op.create_index("ix_requirements_org_id", "requirements", ["org_id"], unique=False)
    op.drop_constraint("requirements_document_id_fkey", "requirements", type_="foreignkey")
    op.create_foreign_key(
        "fk_requirements_document_id",
        "requirements",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_requirements_org_id",
        "requirements",
        "orgs",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # events
    op.create_index("ix_events_org_id", "events", ["org_id"], unique=False)
    op.add_column(
        "events",
        sa.Column(
            "org_id_temp",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.execute("UPDATE events SET org_id_temp = org_id")
    op.drop_column("events", "org_id")
    op.alter_column("events", "org_id_temp", nullable=False)
    op.alter_column("events", "org_id_temp", new_column_name="org_id")
    op.create_foreign_key(
        "fk_events_org_id",
        "events",
        "orgs",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # org metrics
    op.drop_constraint("org_requirement_metrics_org_id_key", "org_requirement_metrics", type_="unique")
    op.create_unique_constraint(
        "uq_org_requirement_metrics_org_id",
        "org_requirement_metrics",
        ["org_id"],
    )
    op.create_foreign_key(
        "fk_org_requirement_metrics_org_id",
        "org_requirement_metrics",
        "orgs",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_org_requirement_metrics_org_id", "org_requirement_metrics", type_="foreignkey")
    op.drop_constraint("uq_org_requirement_metrics_org_id", "org_requirement_metrics", type_="unique")
    op.create_unique_constraint(
        "org_requirement_metrics_org_id_key",
        "org_requirement_metrics",
        ["org_id"],
    )
    op.drop_constraint("fk_events_org_id", "events", type_="foreignkey")
    op.alter_column("events", "org_id", nullable=True)
    op.drop_index("ix_events_org_id", table_name="events")
    op.drop_constraint("fk_requirements_org_id", "requirements", type_="foreignkey")
    op.drop_constraint("fk_requirements_document_id", "requirements", type_="foreignkey")
    op.create_foreign_key(
        "requirements_document_id_fkey",
        "requirements",
        "documents",
        ["document_id"],
        ["id"],
    )
    op.drop_index("ix_requirements_org_id", table_name="requirements")
    op.drop_constraint("fk_documents_org_id", "documents", type_="foreignkey")
    op.drop_index("ix_documents_org_id", table_name="documents")
    op.drop_index("ix_training_certs_org_id", table_name="training_certs")
    op.drop_table("training_certs")
    op.drop_index("ix_permits_org_id", table_name="permits")
    op.drop_table("permits")
    op.drop_table("user_sessions")
    op.drop_table("login_tokens")
    op.drop_table("memberships")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("orgs")
    membership_role_enum.drop(op.get_bind(), checkfirst=True)
