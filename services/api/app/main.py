"""Price API — endpoints de lecture + câblage transverse (phases 1 & 4).

Service hébergeable, propriétaire de la base. Toute la LOGIQUE de données vit
dans `app/data.py` (extraite du monolithe) ; ici on ne fait que du câblage HTTP
+ résolution du serveur actif. Base via SQLAlchemy (Postgres en prod, repli
SQLite dev — cf. app/db.py). Aucune dépendance vision.

Phase 4 (API publique) : auth par clés scopées (`app/auth.py`, en-tête
X-API-Key), rate-limit léger (`app/ratelimit.py`), CORS configurable, pagination
sur le catalogue `/v1/items`, OpenAPI/Swagger soigné sur `/v1/docs`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for sub in ("src/db", "src/pricing", "packages/shared"):
    sys.path.insert(0, str(REPO / sub))

from fastapi import Depends, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.encoders import jsonable_encoder  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.db import ensure_sqlite_migrations, get_conn as get_connection  # noqa: E402  (src/db)
from profitability import get_config  # noqa: E402  (src/pricing)

from app import data  # noqa: E402  (services/api/app/data.py)
from app import ingest  # noqa: E402  (endpoints d'ingestion, POST depuis le collector)
from app import writes  # noqa: E402  (endpoints d'écriture front)
from app.auth import require  # noqa: E402
from app.ratelimit import RateLimitMiddleware  # noqa: E402

# Dépendances de scope réutilisées (en mode dev auth désactivée, ce sont des no-op).
READ = Depends(require("read"))
OWNER = Depends(require("owner"))

TAGS_METADATA = [
    {"name": "meta", "description": "Fraîcheur des données, réglages, serveurs."},
    {"name": "public", "description": "Prix, historique, rentabilité, catalogue d'objets — scope `read`."},
    {"name": "owner", "description": "Suivi perso (atelier, suivis d'achat/revente) — scope `owner`."},
    {"name": "write", "description": "Écritures front (réglages/serveurs/suivis/atelier) — scope `owner`."},
    {"name": "ingest", "description": "Réception des relevés du Collector — secret partagé X-Ingest-Key."},
]

app = FastAPI(
    title="Kamas Price API",
    version="1.0.0",
    summary="Prix HDV Dofus + rentabilité des crafts et des familiers.",
    description=(
        "API de suivi de prix Dofus. Les prix sont collectés par reconnaissance "
        "d'image sur la machine du joueur (service **Collector**) puis stockés ici.\n\n"
        "**Authentification** (quand activée) : en-tête `X-API-Key`. Une clé `read` "
        "donne accès aux prix/historique/rentabilité/catalogue ; une clé `owner` "
        "ajoute l'atelier et les suivis. L'ingestion du collector utilise un secret "
        "dédié `X-Ingest-Key`.\n\n"
        "`server_id` est optionnel partout — par défaut le serveur actif."
    ),
    openapi_tags=TAGS_METADATA,
    contact={"name": "Kamas", "url": "https://github.com/"},
    license_info={"name": "MIT"},
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    openapi_url="/v1/openapi.json",
)

# CORS : le site tiers (pote) appelle l'API depuis le navigateur avec une clé
# read. Origines autorisées via KAMAS_CORS_ORIGINS (CSV) ; "*" par défaut (dev).
_cors = os.environ.get("KAMAS_CORS_ORIGINS", "*").strip()
_origins = ["*"] if _cors == "*" else [o.strip() for o in _cors.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

# Ingest : auth propre (X-Ingest-Key), cf. ingest.py. Écritures front : scope owner.
app.include_router(ingest.router)
app.include_router(writes.router, dependencies=[OWNER])

# Migrations SQLite légères (colonnes tracked_trades récentes) — cf. db.py.
ensure_sqlite_migrations()

CATEGORIES = ("ressource", "familier", "equipement")


def _conn_sid(server_id: int | None):
    conn = get_connection()
    sid = server_id if server_id is not None else data._active_server_id(conn)
    return conn, sid


def _enc(payload):
    return jsonable_encoder(payload)


# ── méta ────────────────────────────────────────────────────────────────
@app.get("/v1/status", tags=["meta"], dependencies=[READ])
def status(server_id: int | None = None):
    """Fraîcheur : date du dernier relevé de prix (badge à jour/obsolète du front)."""
    conn, sid = _conn_sid(server_id)
    row = conn.execute(
        "SELECT MAX(captured_at) FROM price_snapshots WHERE server_id = ?", (sid,)
    ).fetchone()
    conn.close()
    return {"server_id": sid, "last_capture_at": row[0] if row else None}


@app.get("/v1/settings", tags=["public"], dependencies=[READ])
def settings():
    """Réglages influant sur la rentabilité (marge de sécurité, taxe HDV)."""
    conn = get_connection()
    out = {
        "safety_margin_percent": get_config(conn, "safety_margin_percent", 5.0),
        "hdv_tax_percent": get_config(conn, "hdv_tax_percent", 2.0),
    }
    conn.close()
    return out


@app.get("/v1/servers", tags=["meta"], dependencies=[READ])
def servers():
    """Catégories nommées ("serveurs") + celle active."""
    conn = get_connection()
    rows = conn.execute("SELECT id, name, slug FROM servers ORDER BY id").fetchall()
    active = data._active_server_id(conn)
    conn.close()
    return {"servers": [{"id": r[0], "name": r[1], "slug": r[2]} for r in rows], "active_id": active}


# ── public (rentabilité / prix / historique / catalogue) ──────────────────
@app.get("/v1/dashboard", tags=["public"], dependencies=[READ])
def dashboard(server_id: int | None = None):
    conn, sid = _conn_sid(server_id)
    out = data._dashboard_data(conn, sid)
    conn.close()
    return _enc(out)


@app.get("/v1/crafts", tags=["public"], dependencies=[READ])
def crafts(
    q: str = "",
    job_id: int | None = Query(None, description="filtrer par métier (id de /v1/crafts → jobs)"),
    level_min: int | None = Query(None, ge=0, description="niveau min de l'objet crafté"),
    level_max: int | None = Query(None, ge=0, description="niveau max de l'objet crafté"),
    server_id: int | None = None,
):
    """Recettes calculables + incomplètes, classées par bénéfice mensuel estimé.
    Filtres optionnels : `job_id` (métier), `level_min`/`level_max` (niveau de
    l'objet crafté). La réponse inclut `jobs` (liste pour le sélecteur)."""
    # Garde-fou : bornes >= 0 (déjà via ge=0) et min <= max — on échange plutôt
    # que de rejeter, pour qu'un intervalle inversé donne un résultat sensé.
    if level_min is not None and level_max is not None and level_min > level_max:
        level_min, level_max = level_max, level_min
    conn, sid = _conn_sid(server_id)
    out = data._crafts_data(conn, sid, q, job_id=job_id, level_min=level_min, level_max=level_max)
    conn.close()
    return _enc(out)


@app.get("/v1/pets", tags=["public"], dependencies=[READ])
def pets(server_id: int | None = None):
    """Familiers calculables + en attente."""
    conn, sid = _conn_sid(server_id)
    out = data._pets_data(conn, sid)
    conn.close()
    return _enc(out)


@app.get("/v1/elevage", tags=["public"], dependencies=[READ])
def elevage(
    range_from: int = Query(0, ge=0, le=100000, description="début de la jauge (0–100 000)"),
    range_to: int = Query(100000, ge=0, le=100000, description="fin de la jauge (0–100 000)"),
    server_id: int | None = None,
):
    """Remplissage le moins cher des jauges d'enclos sur l'intervalle demandé
    (par défaut 0→100 000), aux prix HDV. `range_from`/`range_to` sont bornés à
    [0, 100 000] et échangés si inversés."""
    if range_from > range_to:
        range_from, range_to = range_to, range_from
    conn, sid = _conn_sid(server_id)
    out = data._elevage_data(conn, sid, range_from, range_to)
    conn.close()
    return _enc(out)


@app.get("/v1/prices", tags=["public"], dependencies=[READ])
def prices(category: str = "ressource", server_id: int | None = None):
    """Dernier prix connu par objet (par lot pour les ressources/équipements,
    par niveau 0/100 pour les familiers)."""
    if category not in CATEGORIES:
        raise HTTPException(422, f"category doit être l'une de {CATEGORIES}")
    conn, sid = _conn_sid(server_id)
    out = data._resource_prices(conn, sid, category)
    conn.close()
    return _enc(out)


@app.get("/v1/items", tags=["public"], dependencies=[READ])
def items_list(
    q: str = Query("", description="filtre sous-chaîne sur le nom (insensible à la casse)"),
    super_type: str | None = Query(None, description="ex. 'Ressource', 'Familier'"),
    limit: int = Query(50, ge=1, le=200, description="taille de page (1–200)"),
    offset: int = Query(0, ge=0, description="décalage de pagination"),
):
    """Catalogue paginé des objets connus (nom, niveau, type) — le point d'entrée
    d'un intégrateur tiers pour croiser ses objets avec les prix de l'API."""
    where, params = [], []
    if q:
        where.append("LOWER(name) LIKE ?")
        params.append(f"%{q.lower()}%")
    if super_type:
        where.append("super_type_name = ?")
        params.append(super_type)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_connection()
    total = conn.execute(f"SELECT COUNT(*) FROM items {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, name, level, type_name, super_type_name FROM items {clause} "
        "ORDER BY name LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    conn.close()
    payload = [
        {"id": r[0], "name": r[1], "level": r[2], "type_name": r[3], "super_type_name": r[4]}
        for r in rows
    ]
    next_offset = offset + limit if offset + limit < total else None
    return {
        "data": payload,
        "pagination": {"total": total, "limit": limit, "offset": offset, "next_offset": next_offset},
    }


@app.get("/v1/items/{item_id}", tags=["public"], dependencies=[READ])
def item(item_id: int):
    """Métadonnées minimales d'un objet (nom) — pour les vues qui n'ont qu'un id."""
    conn = get_connection()
    row = conn.execute("SELECT id, name, super_type_name FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(404, "item inconnu")
    return {"id": row[0], "name": row[1], "super_type_name": row[2]}


@app.get("/v1/history/available", tags=["public"], dependencies=[READ])
def history_available(category: str = "ressource", server_id: int | None = None):
    """Liste des objets d'une catégorie ayant au moins un relevé (pour le
    sélecteur de /history)."""
    if category not in CATEGORIES:
        raise HTTPException(422, f"category doit être l'une de {CATEGORIES}")
    conn, sid = _conn_sid(server_id)
    out = data._history_available(conn, sid, category)
    conn.close()
    return out


@app.get("/v1/history", tags=["public"], dependencies=[READ])
def history(item_id: int, category: str = "ressource", server_id: int | None = None):
    """Détail d'un objet : prix par lot/niveau + cours du marché (médian/moyen/vendus)."""
    if category not in CATEGORIES:
        raise HTTPException(422, f"category doit être l'une de {CATEGORIES}")
    conn, sid = _conn_sid(server_id)
    out = data._history_item_data(conn, sid, item_id, category)
    conn.close()
    return _enc(out)


# ── owner (suivi perso — scope owner) ─────────────────────────────────────
@app.get("/v1/suivis", tags=["owner"], dependencies=[OWNER])
def suivis(server_id: int | None = None):
    conn, sid = _conn_sid(server_id)
    out = data._tracked_trades_data(conn, sid)
    conn.close()
    return _enc(out)


@app.get("/v1/atelier", tags=["owner"], dependencies=[OWNER])
def atelier(server_id: int | None = None):
    conn, sid = _conn_sid(server_id)
    out = data._atelier_data(conn, sid)
    conn.close()
    return _enc(out)
