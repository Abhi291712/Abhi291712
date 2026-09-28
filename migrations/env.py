"""Alembic environment: connects migrations to the application's models and database.

Alembic runs this file for every migration command. It points Alembic at ``Base.metadata`` (so
``--autogenerate`` can compare the models with the real database) and takes the database URL
from the application's own Settings, so there is only one place to configure it.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.database import Base
from app.models import appointment, call, event  # noqa: F401  (registers the tables)

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: don't silence the application's loggers when migrations
    # run inside an already-running process (e.g. the test suite).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# A URL passed programmatically (e.g. by tests) wins; otherwise use DATABASE_URL.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without a live connection (`alembic upgrade head --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the configured database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # Migrations are one-off; no need to keep connections around.
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the table instead.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
