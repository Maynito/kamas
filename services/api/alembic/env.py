"""Environnement Alembic de la Price API.

L'URL de la base est résolue au runtime (même logique que app/db.py) :
`DATABASE_URL` en prod (Postgres), sinon repli SQLite dev — donc
`alembic upgrade head` cible automatiquement le bon dialecte selon
l'environnement, sans édition d'alembic.ini. `target_metadata` pointe sur la
source de vérité unique (app/models.py) pour l'autogenerate des futures
migrations.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

# services/api sur le path pour `import app.*`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import metadata  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        # Même normalisation que app/db.py (PaaS postgres:// -> postgresql+psycopg://).
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    repo = Path(__file__).resolve().parents[4]  # .../services/api/alembic -> repo
    return f"sqlite:///{(repo / 'data' / 'kamas.db').as_posix()}"


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ne sait pas ALTER en place : batch mode
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), future=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
