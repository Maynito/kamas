"""Seed la base de la Price API depuis la SQLite existante (data/kamas.db).

Copie les données statiques + relevés de la SQLite de dev vers la base cible
(`DATABASE_URL` : Postgres en prod, ou une SQLite via SQLAlchemy). Dialecte-
agnostique : lit la source avec sqlite3, écrit la cible via SQLAlchemy. Sert à
peupler le Postgres de démo du portfolio à partir des vraies données déjà
collectées.

Pré-requis : la cible a déjà son schéma (`alembic upgrade head`). Ce script
n'insère que des lignes — il ne crée aucune table.

Usage :
    DATABASE_URL=postgresql+psycopg://... python scripts/seed_from_sqlite.py [source.db]

Sans argument, la source est data/kamas.db à la racine du repo.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# Ordre de copie : parents (FK) avant enfants.
TABLE_ORDER = [
    "servers",
    "jobs",
    "items",
    "recipes",
    "recipe_ingredients",
    "price_snapshots",
    "market_history_points",
    "pet_feed_xp",
    "pet_feed_events",
    "app_config",
    "tracked_trades",
    "atelier_items",
    "atelier_owned_resources",
]

# Tables à PK entière auto (séquence à resynchroniser sur Postgres après des
# inserts d'id explicites). items/jobs/recipes ont des ids DofusDB explicites
# non séquentiels — pas de séquence à resync.
SERIAL_PK_TABLES = [
    "servers",
    "price_snapshots",
    "market_history_points",
    "pet_feed_xp",
    "pet_feed_events",
    "tracked_trades",
    "atelier_items",
]


def _target_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    repo = Path(__file__).resolve().parents[3]
    return f"sqlite:///{(repo / 'data' / 'kamas.db').as_posix()}"


def main() -> None:
    repo = Path(__file__).resolve().parents[3]
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else repo / "data" / "kamas.db"
    if not source_path.exists():
        raise SystemExit(f"source introuvable : {source_path}")

    src = sqlite3.connect(str(source_path))
    src.row_factory = sqlite3.Row
    engine = create_engine(_target_url(), future=True)
    dialect = engine.dialect.name

    with engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(text("PRAGMA foreign_keys=OFF"))  # copie en vrac
        for table in TABLE_ORDER:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"  {table}: 0 (vide)")
                continue
            cols = rows[0].keys()
            col_list = ", ".join(cols)
            placeholders = ", ".join(f":{c}" for c in cols)
            stmt = text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})")
            conn.execute(stmt, [dict(r) for r in rows])
            print(f"  {table}: {len(rows)}")

        if dialect == "postgresql":
            # Resync des séquences après inserts d'ids explicites.
            for table in SERIAL_PK_TABLES:
                conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
                    )
                )
            print("  séquences Postgres resynchronisées")

    src.close()
    print("seed terminé.")


if __name__ == "__main__":
    main()
