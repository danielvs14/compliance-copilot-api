from __future__ import annotations

import os
from logging.config import fileConfig
import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))


from alembic import context
from sqlalchemy import engine_from_config, pool
from dotenv import load_dotenv

from api.models.base import Base  # noqa: F401
from api.models import (
    documents,
    events,
    login_tokens,
    memberships,
    org_metrics,
    orgs,
    permits,
    requirements,
    training_certs,
    user_sessions,
    users,
)  # noqa: F401

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)

        with context.begin_transaction():
            context.run_migrations()


def run_migrations() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()


run_migrations()
