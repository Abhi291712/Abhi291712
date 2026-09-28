"""Tests that the Alembic migrations build exactly the schema the ORM models describe.

If someone changes a model but forgets to add a migration, `alembic check` finds the difference
and this test fails, long before the change reaches a production database.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_upgrade_creates_all_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"

    command.upgrade(_alembic_config(url), "head")

    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"calls", "events", "appointments"} <= tables


def test_models_and_migrations_are_in_sync(tmp_path):
    config = _alembic_config(f"sqlite:///{tmp_path / 'check.db'}")
    command.upgrade(config, "head")

    command.check(config)  # Raises if the models need a migration that does not exist.


def test_downgrade_removes_everything(tmp_path):
    url = f"sqlite:///{tmp_path / 'down.db'}"
    config = _alembic_config(url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}
