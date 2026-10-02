"""Rate-limit léger, en mémoire, sans dépendance (phase 4).

Fenêtre glissante par appelant (clé API si présente, sinon IP client). Suffisant
pour une petite API publique mono-instance (le but portfolio : montrer un
garde-fou d'abus, pas un système distribué). Pour plusieurs instances, on
brancherait Redis — noté comme limite assumée.

Config par env :
  KAMAS_RATE_LIMIT    requêtes autorisées par fenêtre  (défaut 120)
  KAMAS_RATE_WINDOW   longueur de la fenêtre, secondes (défaut 60)
  KAMAS_RATE_LIMIT=0  désactive complètement (dev)

Les chemins de doc (/v1/docs, /v1/openapi.json, /v1/redoc) sont exemptés.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_EXEMPT_PREFIXES = ("/v1/docs", "/v1/redoc", "/v1/openapi.json")


def _limit() -> int:
    try:
        return int(os.environ.get("KAMAS_RATE_LIMIT", "120"))
    except ValueError:
        return 120


def _window() -> float:
    try:
        return float(os.environ.get("KAMAS_RATE_WINDOW", "60"))
    except ValueError:
        return 60.0


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        # appelant -> deque des timestamps (monotone) dans la fenêtre courante.
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _caller(self, request: Request) -> str:
        key = request.headers.get("x-api-key") or request.headers.get("x-ingest-key")
        if key:
            return f"key:{key[:12]}"  # préfixe : ne pas garder la clé entière en mémoire
        client = request.client.host if request.client else "unknown"
        return f"ip:{client}"

    async def dispatch(self, request: Request, call_next):
        limit = _limit()
        if limit <= 0 or request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        window = _window()
        now = time.monotonic()
        bucket = self._hits[self._caller(request)]
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(window - (now - bucket[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": "trop de requêtes — réessaie plus tard"},
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(bucket)))
        return response
