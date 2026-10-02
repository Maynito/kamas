"""schéma initial (13 tables) depuis app.models

Migration initiale : matérialise les 13 tables qui existaient à cette révision,
depuis la métadonnée SQLAlchemy (app/models.py, source de vérité unique —
équivalent dialecte-agnostique de l'ancien src/db/schema.sql). Créer depuis les
objets Table de la métadonnée garantit une parité exacte models <-> DB (un
`alembic check` ne voit aucun écart).

IMPORTANT : la liste des tables est FIGÉE ici (TABLES_0001), pas un
`metadata.create_all()` global — sinon toute table ajoutée plus tard à la
métadonnée (ex. api_keys en 0002) serait rétroactivement créée par cette
migration, cassant l'historique. Chaque migration ne possède que ses propres
tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op

from app.models import metadata

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Les 13 tables telles qu'à la révision 0001 (api_keys arrive en 0002).
TABLES_0001 = (
    "servers",
    "items",
    "price_snapshots",
    "market_history_points",
    "jobs",
    "recipes",
    "recipe_ingredients",
    "pet_feed_xp",
    "pet_feed_events",
    "app_config",
    "tracked_trades",
    "atelier_items",
    "atelier_owned_resources",
)


def _tables():
    # create_all/drop_all ré-ordonnent selon les dépendances FK.
    return [metadata.tables[name] for name in TABLES_0001]


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind(), tables=_tables())


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind(), tables=_tables())
