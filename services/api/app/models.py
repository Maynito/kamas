"""Schéma de la Price API — SQLAlchemy Core (source de vérité unique).

Traduction dialecte-agnostique de `src/db/schema.sql` (SQLite historique) en
métadonnées SQLAlchemy : Alembic génère la migration initiale à partir d'ICI,
donc le même schéma se matérialise sur SQLite (dev) comme sur Postgres (prod).

Notes de portabilité par rapport au SQLite d'origine :
- `INTEGER PRIMARY KEY` (rowid auto-incrémenté SQLite) -> `Integer, primary_key`
  (SERIAL/IDENTITY côté Postgres). Pour `items`/`recipes`/`servers` on insère
  parfois un id EXPLICITE (id DofusDB, ou serveur seedé id=1) : les deux
  dialectes acceptent l'insert explicite ; le seed Postgres resynchronise la
  séquence (cf. scripts/seed).
- Les `CHECK (... IN (...))` sont conservés tels quels (portables).
- Les FK sont déclarées : Postgres les applique nativement, et db.py force
  `PRAGMA foreign_keys=ON` sur SQLite pour le même comportement.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

servers = Table(
    "servers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False),
    Column("slug", Text, nullable=False, unique=True),
)

items = Table(
    "items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),  # id DofusDB (jamais auto)
    Column("name", Text, nullable=False),
    Column("level", Integer),
    Column("type_id", Integer),
    Column("type_name", Text),
    Column("super_type_id", Integer),
    Column("super_type_name", Text),
    Column("icon_id", Integer),
    Column("icon_url", Text),
    Column("icon_path", Text),
    Column("phash", Text),
    Column("updated_at", Text),
    Index("idx_items_super_type", "super_type_name"),
    Index("idx_items_type", "type_id"),
)

price_snapshots = Table(
    "price_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("server_id", Integer, ForeignKey("servers.id"), nullable=False),
    Column("item_id", Integer, ForeignKey("items.id"), nullable=False),
    Column("lot_size", Integer, nullable=False),
    Column("pet_level", Integer),
    Column("price_kamas", Integer, nullable=False),
    Column("source", Text, nullable=False),
    Column("confidence", Numeric),
    Column("captured_at", Text, nullable=False),
    CheckConstraint("lot_size IN (1,10,100,1000)", name="ck_price_lot_size"),
    CheckConstraint("source IN ('detail_panel','hdv_list','manual')", name="ck_price_source"),
    Index("idx_price_item", "item_id", "captured_at"),
)

market_history_points = Table(
    "market_history_points",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("server_id", Integer, ForeignKey("servers.id"), nullable=False),
    Column("item_id", Integer, ForeignKey("items.id"), nullable=False),
    Column("period", Text, nullable=False),
    Column("point_date", Text),
    Column("price_kamas", Integer),
    Column("quantity_sold", Integer),
    Column("metric", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("captured_at", Text, nullable=False),
    CheckConstraint("period IN ('24h','7j','30j')", name="ck_market_period"),
    CheckConstraint("metric IN ('median','mean')", name="ck_market_metric"),
    CheckConstraint("source IN ('ocr_summary')", name="ck_market_source"),
    Index("idx_history_item", "item_id", "period"),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("name", Text, nullable=False),
    Column("icon_url", Text),
)

recipes = Table(
    "recipes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),  # id DofusDB
    Column("result_item_id", Integer, ForeignKey("items.id"), nullable=False),
    Column("job_id", Integer, ForeignKey("jobs.id")),
    Column("level", Integer),
    Column("xp", Integer),
    Column("updated_at", Text),
)

recipe_ingredients = Table(
    "recipe_ingredients",
    metadata,
    Column("recipe_id", Integer, ForeignKey("recipes.id"), nullable=False),
    # pas de FK stricte (certains ingrédients hors scope manquent de items)
    Column("ingredient_item_id", Integer, nullable=False),
    Column("quantity", Integer, nullable=False),
    UniqueConstraint("recipe_id", "ingredient_item_id", name="pk_recipe_ingredients"),
)

pet_feed_xp = Table(
    "pet_feed_xp",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("resource_item_id", Integer, ForeignKey("items.id"), nullable=False, unique=True),
    Column("xp_value", Integer, nullable=False),
    Column("source", Text, nullable=False, server_default="vision_llm_seed"),
    Column("updated_at", Text),
)

pet_feed_events = Table(
    "pet_feed_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("server_id", Integer, ForeignKey("servers.id"), nullable=False),
    Column("pet_name", Text, nullable=False),
    Column("pet_level", Integer),
    Column("resource_item_id", Integer, ForeignKey("items.id"), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("xp_gained", Integer),
    Column("captured_at", Text, nullable=False),
)

app_config = Table(
    "app_config",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)

tracked_trades = Table(
    "tracked_trades",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("server_id", Integer, ForeignKey("servers.id"), nullable=False),
    Column("item_id", Integer, ForeignKey("items.id"), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("buy_price_kamas", Integer, nullable=False),
    Column("sell_price_kamas", Integer),
    Column("status", Text, nullable=False, server_default="holding"),
    Column("stage", Text, nullable=False, server_default="live"),
    Column("rune_cost_kamas", Integer),
    Column("created_at", Text, nullable=False),
    Column("sold_at", Text),
    CheckConstraint("quantity > 0", name="ck_trade_quantity"),
    CheckConstraint("status IN ('holding','sold')", name="ck_trade_status"),
    Index("idx_tracked_trades_server", "server_id", "status"),
)

atelier_items = Table(
    "atelier_items",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("server_id", Integer, ForeignKey("servers.id"), nullable=False),
    Column("item_id", Integer, ForeignKey("items.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("quantity", Integer, nullable=False, server_default="1"),
    Column("feed_resource_item_id", Integer, ForeignKey("items.id")),
    Column("feed_qty_owned", Integer, nullable=False, server_default="0"),
    Column("created_at", Text, nullable=False),
    CheckConstraint("kind IN ('equipment','pet')", name="ck_atelier_kind"),
    UniqueConstraint("server_id", "item_id", "kind", name="uq_atelier_items"),
    Index("idx_atelier_items_server", "server_id", "kind"),
)

atelier_owned_resources = Table(
    "atelier_owned_resources",
    metadata,
    Column("server_id", Integer, ForeignKey("servers.id"), nullable=False),
    Column("resource_item_id", Integer, ForeignKey("items.id"), nullable=False),
    Column("quantity_owned", Integer, nullable=False, server_default="0"),
    UniqueConstraint("server_id", "resource_item_id", name="pk_atelier_owned_resources"),
)

# API publique (phase 4) : clés hashées + scope. On ne stocke JAMAIS la clé en
# clair — seulement son SHA-256 ; la valeur brute n'est montrée qu'une fois, à
# la création (cf. scripts/manage_keys.py). scope ∈ read|owner|admin (l'ingest
# garde son secret partagé dédié X-Ingest-Key, un seul producteur de confiance).
api_keys = Table(
    "api_keys",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("key_hash", Text, nullable=False, unique=True),
    Column("scope", Text, nullable=False),
    Column("label", Text),
    Column("created_at", Text, nullable=False),
    Column("revoked", Integer, nullable=False, server_default="0"),
    CheckConstraint("scope IN ('read','owner','admin')", name="ck_api_key_scope"),
)
