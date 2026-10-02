"""Synchronise les données statiques (items, recettes, métiers) depuis l'API
communautaire DofusDB (https://api.dofusdb.fr). Licence non-commerciale,
attribution requise : "Data sourced from DofusDB".

Ce script ne touche jamais aux prix HDV (données dynamiques, hors scope de
DofusDB) : uniquement les données stables (niveaux, recettes, icônes).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.connection import DB_PATH, get_connection  # noqa: E402

API_BASE = "https://api.dofusdb.fr"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
ICONS_DIR = Path(__file__).resolve().parents[2] / "data" / "icons"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"

SUPER_TYPE_RESSOURCE = 9
SUPER_TYPE_FAMILIER = 12
PAGE_SIZE = 100

# Mis à True par --refresh : ignore le cache disque et refait tous les appels
# API. À utiliser uniquement après une mise à jour du jeu (nouveaux items,
# recettes, etc.) ; sinon les runs suivants réutilisent le cache et ne
# retapent jamais l'API.
FORCE_REFRESH = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_CONFIG = {
    "hdv_tax_percent": "2",       # taxe de vente HDV, fixe
    "safety_margin_percent": "5",  # marge de sécurité, ajustable par l'utilisateur
    "active_server_id": "1",
}


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(
        "INSERT OR IGNORE INTO servers (id, name, slug) VALUES (1, 'Serveur principal', 'main')"
    )
    for key, value in DEFAULT_CONFIG.items():
        conn.execute("INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def _cache_path(path: str, params: list[tuple] | None) -> Path:
    raw = path + "?" + "&".join(f"{k}={v}" for k, v in (params or []))
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    safe_path = path.strip("/").replace("/", "_")
    return CACHE_DIR / f"{safe_path}_{digest}.json"


def get_json(path: str, params: dict | list[tuple] | None = None) -> dict:
    cache_file = _cache_path(path, params if isinstance(params, list) else None)
    if not FORCE_REFRESH and cache_file.exists():
        return json.loads(cache_file.read_text())

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
        except requests.exceptions.RequestException as exc:
            # Hoquet réseau (timeout, connexion refusée, DNS...) : on retente
            # comme pour un statut HTTP en erreur, plutôt que de planter direct.
            last_exc = exc
            time.sleep(1 + attempt)
            continue
        if resp.status_code == 200:
            data = resp.json()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))
            return data
        last_exc = None
        time.sleep(1 + attempt)
    if last_exc is not None:
        raise last_exc
    resp.raise_for_status()
    return {}


def paginate(path: str, extra_params: list[tuple]) -> list[dict]:
    """Parcourt toutes les pages d'un endpoint Feathers.js ($limit/$skip).

    L'API plafonne silencieusement $limit à 50 par page (elle ignore une
    valeur plus haute sans erreur) : on avance donc le curseur du nombre
    d'éléments réellement reçus, jamais de PAGE_SIZE, sous peine de sauter
    la moitié des résultats.
    """
    results: list[dict] = []
    skip = 0
    while True:
        params = list(extra_params) + [("$limit", PAGE_SIZE), ("$skip", skip)]
        page = get_json(path, params=params)
        data = page.get("data", [])
        results.extend(data)
        if not data:
            break
        skip += len(data)
        if skip >= page.get("total", 0):
            break
    return results


def fetch_type_ids_for_super_type(super_type_id: int) -> list[int]:
    types = paginate("/item-types", [("superTypeId", super_type_id)])
    return [t["id"] for t in types]


def upsert_item(conn: sqlite3.Connection, item: dict) -> None:
    type_obj = item.get("type") or {}
    super_type_obj = type_obj.get("superType") or {}
    conn.execute(
        """
        INSERT INTO items (id, name, level, type_id, type_name, super_type_id,
                            super_type_name, icon_id, icon_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, level=excluded.level, type_id=excluded.type_id,
            type_name=excluded.type_name, super_type_id=excluded.super_type_id,
            super_type_name=excluded.super_type_name, icon_id=excluded.icon_id,
            icon_url=excluded.icon_url, updated_at=excluded.updated_at
        """,
        (
            item["id"],
            (item.get("name") or {}).get("fr", f"item-{item['id']}"),
            item.get("level"),
            item.get("typeId"),
            (type_obj.get("name") or {}).get("fr"),
            type_obj.get("superTypeId"),
            (super_type_obj.get("name") or {}).get("fr"),
            item.get("iconId"),
            item.get("img"),
            now_iso(),
        ),
    )


def sync_resources_and_pets(conn: sqlite3.Connection) -> set[int]:
    """Sync les ressources (superType=9) et familiers (superType=12).
    Retourne l'ensemble des ids déjà synchronisés."""
    type_ids = fetch_type_ids_for_super_type(SUPER_TYPE_RESSOURCE) + fetch_type_ids_for_super_type(
        SUPER_TYPE_FAMILIER
    )
    in_filter = [("typeId[$in][]", tid) for tid in type_ids]
    items = paginate("/items", in_filter)
    for item in items:
        upsert_item(conn, item)
    conn.commit()
    print(f"[sync] {len(items)} ressources/familiers synchronisés ({len(type_ids)} catégories)")
    return {i["id"] for i in items}


def sync_jobs(conn: sqlite3.Connection) -> None:
    jobs = paginate("/jobs", [])
    for job in jobs:
        conn.execute(
            "INSERT INTO jobs (id, name, icon_url) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, icon_url=excluded.icon_url",
            (job["id"], (job.get("name") or {}).get("fr", f"job-{job['id']}"), job.get("img")),
        )
    conn.commit()
    print(f"[sync] {len(jobs)} métiers synchronisés")


def sync_recipes(conn: sqlite3.Connection, known_item_ids: set[int]) -> None:
    """Synchronise les recettes. L'ordre compte : recipes.result_item_id est
    une clé étrangère vers items(id), donc tout item résultat/ingrédient
    référencé (équipements notamment, absents des ressources/familiers déjà
    synchronisés) doit être inséré dans items AVANT les lignes recipes qui
    le référencent — sous peine de violer la contrainte FK."""
    recipes = paginate("/recipes", [])

    missing_item_ids: set[int] = set()
    for recipe in recipes:
        result_id = recipe.get("resultId")
        if result_id is None:
            continue
        if result_id not in known_item_ids:
            missing_item_ids.add(result_id)
        missing_item_ids.update(i for i in recipe.get("ingredientIds", []) if i not in known_item_ids)

    # Passe 1 : compléter items avec tout ce que les recettes référencent.
    missing_list = list(missing_item_ids)
    batch = 50
    fetched = 0
    known_item_ids = set(known_item_ids)
    for i in range(0, len(missing_list), batch):
        chunk = missing_list[i : i + batch]
        in_filter = [("id[$in][]", cid) for cid in chunk]
        items = paginate("/items", in_filter)
        for item in items:
            upsert_item(conn, item)
            known_item_ids.add(item["id"])
        fetched += len(items)
    conn.commit()
    print(f"[sync] {fetched}/{len(missing_list)} items résultat/ingrédient (équipements, etc.) complétés")

    # Passe 2 : insérer les recettes, maintenant que leurs items existent.
    skipped = 0
    for recipe in recipes:
        result_id = recipe.get("resultId")
        ingredient_ids = recipe.get("ingredientIds", [])
        quantities = recipe.get("quantities", [])
        if result_id is None or result_id not in known_item_ids:
            skipped += 1
            continue

        conn.execute(
            """
            INSERT INTO recipes (id, result_item_id, job_id, level, xp, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                result_item_id=excluded.result_item_id, job_id=excluded.job_id,
                level=excluded.level, updated_at=excluded.updated_at
            """,
            (recipe["id"], result_id, recipe.get("jobId"), recipe.get("resultLevel"), None, now_iso()),
        )
        conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe["id"],))
        # Certaines recettes DofusDB listent deux fois le même ingrédient
        # (par ex. deux entrées séparées de "1 Farine" au lieu d'une entrée
        # "2 Farine") — on agrège par ingredient_item_id pour respecter la
        # clé primaire composite (recipe_id, ingredient_item_id) plutôt que
        # de planter toute la synchro sur un IntegrityError.
        quantity_by_ingredient: dict[int, int] = {}
        for ing_id, qty in zip(ingredient_ids, quantities):
            quantity_by_ingredient[ing_id] = quantity_by_ingredient.get(ing_id, 0) + qty
        for ing_id, qty in quantity_by_ingredient.items():
            conn.execute(
                "INSERT INTO recipe_ingredients (recipe_id, ingredient_item_id, quantity) VALUES (?, ?, ?)",
                (recipe["id"], ing_id, qty),
            )
    conn.commit()
    print(f"[sync] {len(recipes) - skipped} recettes synchronisées ({skipped} ignorées, item résultat introuvable)")


def download_icons(conn: sqlite3.Connection) -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT id, icon_url FROM items WHERE icon_url IS NOT NULL AND icon_path IS NULL"
    ).fetchall()
    downloaded = 0
    for item_id, icon_url in rows:
        dest = ICONS_DIR / f"{item_id}.png"
        if not dest.exists():
            try:
                resp = requests.get(icon_url, timeout=15)
            except requests.exceptions.RequestException:
                continue  # icône réessayée au prochain run (icon_path reste NULL)
            if resp.status_code == 200:
                dest.write_bytes(resp.content)
            else:
                continue
        conn.execute("UPDATE items SET icon_path = ? WHERE id = ?", (str(dest), item_id))
        downloaded += 1
        if downloaded % 200 == 0:
            conn.commit()
            print(f"[sync] {downloaded} icônes téléchargées...")
    conn.commit()
    print(f"[sync] {downloaded} icônes téléchargées au total")


def main() -> None:
    global FORCE_REFRESH
    FORCE_REFRESH = "--refresh" in sys.argv
    if FORCE_REFRESH:
        print("[sync] --refresh : cache ignoré, tous les appels API sont refaits")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        init_db(conn)
        known_ids = sync_resources_and_pets(conn)
        sync_jobs(conn)
        sync_recipes(conn, known_ids)
        if "--skip-icons" not in sys.argv:
            download_icons(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
