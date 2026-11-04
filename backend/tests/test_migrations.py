from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]


def _config() -> Config:
    return Config(str(BACKEND / "alembic.ini"))


def test_single_migration_head() -> None:
    assert len(ScriptDirectory.from_config(_config()).get_heads()) <= 1


def test_upgrade_and_downgrade_run_cleanly() -> None:
    cfg = _config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_models_and_migrations_are_in_sync() -> None:
    cfg = _config()
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if a model change has no migration
