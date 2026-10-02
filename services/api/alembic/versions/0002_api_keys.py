"""table api_keys (auth par clés + scopes, phase 4)

Créée directement depuis la définition de la métadonnée (app.models.api_keys),
comme la migration initiale — garantit une parité exacte models <-> DB (un
`alembic check` ne détecte aucun écart, y compris sur le nommage des contraintes).

Revision ID: 0002_api_keys
Revises: 0001_initial
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op

from app.models import api_keys

revision = "0002_api_keys"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    api_keys.create(bind=op.get_bind())


def downgrade() -> None:
    api_keys.drop(bind=op.get_bind())
