"""Auth de la Price API — clés API hashées + scopes (phase 4).

Deux surfaces d'authentification, volontairement distinctes :

- **Public / owner** : clés API scopées (`read` | `owner` | `admin`), passées en
  en-tête `X-API-Key`, stockées HASHÉES (SHA-256) dans `api_keys`. Le pote reçoit
  une clé `read` (prix/historique/rentabilité/items) ; le front porte une clé
  `owner` (lecture + écritures suivis/atelier/réglages). Cf. `require()`.
- **Ingest** : secret partagé dédié `X-Ingest-Key` (un seul producteur de
  confiance, le collector) — géré dans `ingest.py`, hors de ce module.

Interrupteur : `KAMAS_REQUIRE_AUTH`. Absent/faux (dev) → ouvert, aucune clé
exigée (le `require()` devient un no-op). Vrai (prod, cf. docker-compose) →
401 sans clé valide, 403 si le scope ne couvre pas l'endpoint.
"""
from __future__ import annotations

import hashlib
import os

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.db import get_conn

API_KEY_HEADER = "X-API-Key"

# Schéma déclaré à OpenAPI : Swagger affiche le bouton "Authorize" et le cadenas
# sur les endpoints qui en dépendent. auto_error=False → on gère nous-mêmes le
# 401 (message FR + mode ouvert en dev).
_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

# Hiérarchie des scopes : ce qu'une clé de scope X est autorisée à atteindre.
_GRANTS = {
    "admin": {"admin", "owner", "read"},
    "owner": {"owner", "read"},
    "read": {"read"},
}


def auth_required() -> bool:
    return os.environ.get("KAMAS_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes", "on")


def hash_key(raw: str) -> str:
    """SHA-256 hex — ce qui est stocké en base (jamais la clé en clair)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scope_for_key(raw_key: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT scope FROM api_keys WHERE key_hash = ? AND revoked = 0",
        (hash_key(raw_key),),
    ).fetchone()
    conn.close()
    return row[0] if row else None


class Principal:
    """Identité résolue d'une requête (le scope effectif de sa clé)."""

    def __init__(self, scope: str, anonymous: bool = False):
        self.scope = scope
        self.anonymous = anonymous  # True = mode ouvert dev (auth désactivée)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Principal(scope={self.scope!r}, anonymous={self.anonymous})"


def require(scope: str):
    """Dépendance FastAPI : exige qu'une clé couvrant `scope` soit fournie.

    En mode ouvert (KAMAS_REQUIRE_AUTH absent/faux), renvoie un Principal
    anonyme sans rien vérifier — pratique en dev/local. En mode strict, lève
    401 (clé absente/invalide/révoquée) ou 403 (scope insuffisant).
    """

    def _dep(api_key: str | None = Depends(_api_key_scheme)) -> Principal:
        if not auth_required():
            return Principal(scope="admin", anonymous=True)
        if not api_key:
            raise HTTPException(status_code=401, detail=f"clé API requise (en-tête {API_KEY_HEADER})")
        granted = _scope_for_key(api_key)
        if granted is None:
            raise HTTPException(status_code=401, detail="clé API invalide ou révoquée")
        if scope not in _GRANTS.get(granted, set()):
            raise HTTPException(status_code=403, detail=f"scope insuffisant : '{granted}' ne couvre pas '{scope}'")
        return Principal(scope=granted)

    return _dep
