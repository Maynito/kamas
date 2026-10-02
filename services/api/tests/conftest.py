"""Fixtures des tests d'API (phase 3/4) — HERMÉTIQUES.

Aucune dépendance vision, aucun sync DofusDB, aucune icône : une SQLite temporaire
créée depuis la métadonnée SQLAlchemy (app.models) et seedée avec le strict
minimum. `DATABASE_URL` est fixé AVANT tout import de `app.*` pour que le moteur
de app.db pointe sur cette base jetable. Rapide et fiable en CI.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))
for sub in ("src/db", "src/pricing", "packages/shared"):
    sys.path.insert(0, str(REPO / sub))

# Base jetable, fixée avant les imports app.* (le moteur est créé à l'import).
_TMP = Path(tempfile.mkdtemp(prefix="kamas_apitest_")) / "api_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.as_posix()}"
os.environ.pop("KAMAS_REQUIRE_AUTH", None)  # départ en mode ouvert
os.environ["KAMAS_RATE_LIMIT"] = "0"  # limiteur désactivé par défaut

from app.auth import hash_key  # noqa: E402
from app.db import _engine  # noqa: E402
from app.models import metadata  # noqa: E402


def _seed():
    metadata.create_all(_engine)
    from sqlalchemy import text

    now = "2026-08-21T12:00:00+00:00"
    with _engine.begin() as c:
        c.execute(text("INSERT INTO servers (id, name, slug) VALUES (1, 'Principal', 'main')"))
        for k, v in [("active_server_id", "1"), ("safety_margin_percent", "5"), ("hdv_tax_percent", "2")]:
            c.execute(text("INSERT INTO app_config (key, value) VALUES (:k, :v)"), {"k": k, "v": v})
        # (id, nom, super_type, level) — levels sur les objets craftés (résultats
        # de recette) pour tester le filtre de niveau.
        items = [
            (1, "Ailes de Moskito", "Ressource", None),
            (2, "Bisouglours", "Familier", None),
            (3, "Amulette du Piou Violet", "Amulette", 20),
            (4, "Ble", "Ressource", None),
            (5, "Orge", "Ressource", None),
            (10, "Cape Tailleur", "Cape", 50),
            (11, "Coiffe Tailleur", "Chapeau", 80),
            # carburants d'enclos (jauge Caresseur, un par plafond) pour tester
            # /v1/elevage — super_type volontairement != 'Ressource' pour ne pas
            # fausser les comptes des tests items ci-dessus.
            (33348, "Gigantesque Extrait de Caresseur", "Carburant", 45),
            (33400, "Gigantesque Philtre de Caresseur", "Carburant", 95),
            (33453, "Gigantesque Potion de Caresseur", "Carburant", 145),
            (33499, "Gigantesque Elixir de Caresseur", "Carburant", 195),
        ]
        for iid, name, stype, lvl in items:
            c.execute(
                text("INSERT INTO items (id, name, super_type_name, level) VALUES (:i, :n, :s, :l)"),
                {"i": iid, "n": name, "s": stype, "l": lvl},
            )
        # métiers + recettes (sans prix d'ingrédient -> incomplètes, mais suffit
        # pour tester les filtres métier/niveau qui s'appliquent en amont).
        for jid, jname in [(1, "Tailleur"), (2, "Cordonnier")]:
            c.execute(text("INSERT INTO jobs (id, name) VALUES (:i, :n)"), {"i": jid, "n": jname})
        # recette 200 : le carburant 33348 (Caresseur, HDV 100) est craftable
        # depuis du Blé (item 4, prix 10) -> coût de craft 10 < achat 100, donc
        # la référence Élevage doit basculer sur "craft". job_id NULL pour ne pas
        # perturber les tests de filtre métier des crafts.
        recipes = [(100, 10, 1, 50), (101, 11, 1, 80), (102, 3, 2, 20), (200, 33348, None, None)]
        for rid, res, job, lvl in recipes:
            c.execute(
                text("INSERT INTO recipes (id, result_item_id, job_id, level) VALUES (:i, :r, :j, :l)"),
                {"i": rid, "r": res, "j": job, "l": lvl},
            )
        for rid, ing, qty in [(100, 4, 5), (101, 5, 3), (102, 1, 2), (200, 4, 1)]:
            c.execute(
                text("INSERT INTO recipe_ingredients (recipe_id, ingredient_item_id, quantity) VALUES (:r, :i, :q)"),
                {"r": rid, "i": ing, "q": qty},
            )
        c.execute(
            text(
                "INSERT INTO price_snapshots (server_id, item_id, lot_size, price_kamas, source, confidence, captured_at) "
                "VALUES (1, 1, 1, 100, 'manual', 1.0, :t)"
            ),
            {"t": now},
        )
        # prix du Blé (ingrédient de la recette 100 -> Cape) : la recette a alors
        # TOUS ses ingrédients pricés, mais la Cape (item 10) n'a pas de prix de
        # vente -> doit apparaître en "calculable" (coût connu, marge inconnue).
        c.execute(
            text(
                "INSERT INTO price_snapshots (server_id, item_id, lot_size, price_kamas, source, confidence, captured_at) "
                "VALUES (1, 4, 1, 10, 'manual', 1.0, :t)"
            ),
            {"t": now},
        )
        # prix des carburants Caresseur (plafond 40k/70k/90k/100k, fill 5000)
        for fid, price in [(33348, 100), (33400, 200), (33453, 300), (33499, 400)]:
            c.execute(
                text(
                    "INSERT INTO price_snapshots (server_id, item_id, lot_size, price_kamas, source, confidence, captured_at) "
                    "VALUES (1, :i, 1, :p, 'manual', 1.0, :t)"
                ),
                {"i": fid, "p": price, "t": now},
            )
        # Clés API : une read, une owner (hashées).
        for scope, raw in [("read", "KEY_READ"), ("owner", "KEY_OWNER")]:
            c.execute(
                text("INSERT INTO api_keys (key_hash, scope, label, created_at, revoked) VALUES (:h, :s, :l, :t, 0)"),
                {"h": hash_key(raw), "s": scope, "l": scope, "t": now},
            )


@pytest.fixture(scope="session", autouse=True)
def _db():
    _seed()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("KAMAS_REQUIRE_AUTH", "1")
    yield
