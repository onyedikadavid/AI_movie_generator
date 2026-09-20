import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# 1. Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

# 2. Import application settings and models
from app.core.config import settings
from app.core.database import Base
import app.models

config = context.config

# 3. Format URL for Alembic:
#    a. Convert async driver to sync driver for migrations
#    b. asyncpg's SSL query param is "ssl=require"; psycopg2 (sync) wants
#       "sslmode=require" instead - matters if DATABASE_URL_OVERRIDE points
#       at a hosted Postgres like Neon that requires TLS.
#    c. Escape `%` as `%%` so ConfigParser won't throw an interpolation error
db_url = str(settings.DATABASE_URL).replace("postgresql+asyncpg://", "postgresql+psycopg2://")
db_url = db_url.replace("ssl=require", "sslmode=require")
escaped_db_url = db_url.replace("%", "%%")

config.set_main_option("sqlalchemy.url", escaped_db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()