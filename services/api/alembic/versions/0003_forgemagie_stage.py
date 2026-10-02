"""tracked_trades : étape forgemagie (stage + rune_cost_kamas)

Ajoute deux colonnes additives à tracked_trades pour l'étape "En forgemagie" des
équipements (saisie du coût en runes avant mise en vente) — cf. suivis. Aucune
contrainte modifiée (ajout non destructif).

IDEMPOTENT : la migration initiale (0001) matérialise tracked_trades depuis la
métadonnée courante (app.models), qui porte DÉJÀ ces colonnes — une base fraîche
les a donc à l'arrivée en 0003. On ne les (r)ajoute que si elles manquent (base
antérieure à cette version). Évite un "duplicate column" sur base neuve.

Revision ID: 0003_forgemagie_stage
Revises: 0002_api_keys
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_forgemagie_stage"
down_revision = "0002_api_keys"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("tracked_trades")}


def upgrade() -> None:
    cols = _columns()
    if "stage" not in cols:
        op.add_column("tracked_trades", sa.Column("stage", sa.Text(), nullable=False, server_default="live"))
    if "rune_cost_kamas" not in cols:
        op.add_column("tracked_trades", sa.Column("rune_cost_kamas", sa.Integer()))


def downgrade() -> None:
    cols = _columns()
    if "rune_cost_kamas" in cols:
        op.drop_column("tracked_trades", "rune_cost_kamas")
    if "stage" in cols:
        op.drop_column("tracked_trades", "stage")
