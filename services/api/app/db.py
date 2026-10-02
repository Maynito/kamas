"""Couche base de la Price API — SQLAlchemy (Phase 3).

Un moteur SQLAlchemy piloté par `DATABASE_URL` : Postgres en prod
(`postgresql+psycopg://...`), sinon repli sur la SQLite existante MAIS via
SQLAlchemy (même chemin de code). Un fin wrapper `Conn` fait tourner le code
raw-SQL existant (placeholders `?`, .execute/.fetchone/.fetchall/.commit) sur
N'IMPORTE QUEL dialecte : il traduit les `?` positionnels en paramètres nommés
SQLAlchemy `:pN`. Résultat : data.py / writes.py / ingest.py / reading_store /
pricing tournent inchangés sur SQLite comme sur Postgres.

Le schéma n'est plus créé par ce module : il l'est par Alembic (migrations).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool


def _normalize_url(url: str) -> str:
    """Les PaaS (Render/Heroku) exposent `postgres://` ou `postgresql://`, qui
    résolvent vers psycopg2 côté SQLAlchemy — or on installe psycopg (v3). On
    force donc le driver `+psycopg` pour que l'URL de la plateforme marche telle
    quelle, sans la retoucher à la main dans le dashboard."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _make_engine():
    url = os.environ.get("DATABASE_URL")
    if url:
        # Postgres (ou toute URL SQLAlchemy explicite).
        return create_engine(_normalize_url(url), pool_pre_ping=True, future=True)
    # Repli dev : la SQLite existante, servie VIA SQLAlchemy (prouve
    # l'abstraction — le seul changement pour passer à Postgres est l'URL).
    repo = Path(__file__).resolve().parents[3]
    return create_engine(
        f"sqlite:///{(repo / 'data' / 'kamas.db').as_posix()}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
        future=True,
    )


_engine = _make_engine()
DIALECT = _engine.dialect.name  # "sqlite" | "postgresql"
# Chemin du fichier SQLite (dev) : on ouvre alors une VRAIE connexion sqlite3
# native plutôt que de router chaque requête par SQLAlchemy — cf. get_conn().
_SQLITE_PATH = _engine.url.database if DIALECT == "sqlite" else None

if DIALECT == "sqlite":
    # SQLAlchemy n'active PAS les FK sur SQLite par défaut (contrairement à
    # notre connection.py historique) — sans ça, delete_server ne détecterait
    # pas qu'un serveur a des données. Postgres applique les FK nativement.
    @event.listens_for(_engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _rec):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


def _to_named(sql: str, params):
    """`?` positionnels -> `:pN` nommés (SQLAlchemy text). Le code existant n'a
    jamais de `?` littéral (sqlite3 les traite tous comme placeholders)."""
    if not params:
        return sql, {}
    binds, out, idx = {}, [], 0
    for ch in sql:
        if ch == "?":
            binds[f"p{idx}"] = params[idx]
            out.append(f":p{idx}")
            idx += 1
        else:
            out.append(ch)
    return "".join(out), binds


class _Result:
    """Interface DB-API minimale (fetchone/fetchall/iter), en tuples pour
    rester compatible `row[0]`. Les lignes sont matérialisées TOUT DE SUITE :
    le code fait souvent des `conn.execute()` IMBRIQUÉS pendant l'itération d'un
    SELECT (ex. get_unit_price appelé dans une boucle) — matérialiser ferme le
    curseur aussitôt et évite tout conflit de curseur côté SQLAlchemy. Les
    résultats de l'API sont petits (quelques centaines de lignes au plus)."""

    def __init__(self, result):
        self._rows = [tuple(row) for row in result] if result.returns_rows else []
        self._i = 0

    def fetchone(self):
        if self._i < len(self._rows):
            row = self._rows[self._i]
            self._i += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._i:]
        self._i = len(self._rows)
        return rows

    def __iter__(self):
        while self._i < len(self._rows):
            row = self._rows[self._i]
            self._i += 1
            yield row


class Conn:
    """Se comporte comme une sqlite3.Connection pour le code appelant, mais
    exécute via SQLAlchemy sur le dialecte configuré."""

    def __init__(self):
        self._c = _engine.connect()

    def execute(self, sql: str, params=()):
        converted, binds = _to_named(sql, tuple(params))
        return _Result(self._c.execute(text(converted), binds))

    def commit(self):
        self._c.commit()

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


def get_conn():
    """Connexion pour une requête. **SQLite (dev)** : une vraie `sqlite3.Connection`
    native — les pages Dashboard/Crafts enchaînent des MILLIERS de requêtes
    imbriquées (prix par ingrédient sur ~4800 recettes) ; router chacune par
    SQLAlchemy (`text()`, conversion `?`→`:pN`, matérialisation) coûtait ~3s par
    page et saturait le serveur avec le rafraîchissement auto. sqlite3 natif
    fait le même travail en dizaines de ms (comme le monolithe d'origine).
    **Postgres (prod)** : le wrapper `Conn` (même SQL `?`, dialecte adapté).
    Les deux exposent `.execute/.fetchone/.fetchall/.commit/.close` à
    l'identique — le code appelant ne voit aucune différence."""
    if DIALECT == "sqlite":
        conn = sqlite3.connect(_SQLITE_PATH, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    return Conn()


def ensure_sqlite_migrations() -> None:
    """Migrations légères IDEMPOTENTES pour la SQLite de dev (la prod Postgres
    passe par Alembic). Ajoute au fil des versions les colonnes manquantes des
    bases existantes — `ALTER TABLE ADD COLUMN` est NON DESTRUCTIF (préserve
    toutes les lignes) et se saute tout seul si la colonne est déjà là. Appelé
    une fois au démarrage de l'API (cf. app/main.py)."""
    if DIALECT != "sqlite":
        return
    conn = get_conn()
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tracked_trades)").fetchall()}
        if not cols:
            return  # table pas encore créée (base vide) — rien à migrer
        if "stage" not in cols:
            conn.execute("ALTER TABLE tracked_trades ADD COLUMN stage TEXT NOT NULL DEFAULT 'live'")
        if "rune_cost_kamas" not in cols:
            conn.execute("ALTER TABLE tracked_trades ADD COLUMN rune_cost_kamas INTEGER")
        conn.commit()
        _split_existing_lots(conn)
    finally:
        conn.close()


def _split_existing_lots(conn) -> None:
    """Découpe les positions "en cours" de quantité > 1 (ancien modèle : un lot,
    un seul prix) en positions UNITAIRES (une par exemplaire, prix réparti à
    l'unité) — pour que chaque exemplaire ait son propre prix de vente, comme
    les nouvelles positions. IDEMPOTENT : après passage il ne reste plus aucune
    quantité > 1. NON DESTRUCTIF : la somme des prix est préservée exactement,
    en une transaction (tout ou rien)."""

    def split(total, n):
        if total is None:
            return [None] * n
        per, rem = divmod(total, n)
        return [per + (1 if i < rem else 0) for i in range(n)]

    rows = conn.execute(
        "SELECT id, server_id, item_id, quantity, buy_price_kamas, sell_price_kamas, "
        "stage, rune_cost_kamas, created_at FROM tracked_trades "
        "WHERE status = 'holding' AND quantity > 1"
    ).fetchall()
    if not rows:
        return
    for tid, sid, iid, qty, buy, sell, stage, rune, created in rows:
        buys, sells, runes = split(buy, qty), split(sell, qty), split(rune, qty)
        for i in range(qty):
            conn.execute(
                "INSERT INTO tracked_trades (server_id, item_id, quantity, buy_price_kamas, sell_price_kamas, "
                "status, stage, rune_cost_kamas, created_at) VALUES (?, ?, 1, ?, ?, 'holding', ?, ?, ?)",
                (sid, iid, buys[i], sells[i], stage, runes[i], created),
            )
        conn.execute("DELETE FROM tracked_trades WHERE id = ?", (tid,))
    conn.commit()
    print(f"[db] {len(rows)} position(s) lot découpée(s) en positions unitaires")
