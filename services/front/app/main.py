"""Front Kamas — présentation seule (Phase 1).

FastAPI + Jinja qui RÉUTILISE les templates/static existants mais ne touche
JAMAIS la base : toute la donnée vient de la Price API via httpx. Les routes
reproduisent 1:1 celles du monolithe (mêmes chemins, mêmes réponses
redirect/JSON attendues par les templates) — seule la SOURCE change (API au
lieu de la DB). Les templates/static sont référencés depuis src/web/ pour
l'instant (déplacement physique = étape ultérieure, sans duplication).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
ICONS = REPO / "data" / "icons"
API_URL = os.environ.get("KAMAS_API_URL", "http://127.0.0.1:8100")
# Render expose le lien interservice sous forme host:port (sans schéma) — httpx
# exige un schéma pour base_url. On préfixe http:// si absent (trafic interne).
if not API_URL.startswith(("http://", "https://")):
    API_URL = "http://" + API_URL
# Le front est un client de confiance : il porte une clé `owner` (lecture +
# écritures) côté SERVEUR, jamais exposée au navigateur. Absente en dev (API en
# mode ouvert). Cf. services/api/app/auth.py.
API_KEY = os.environ.get("KAMAS_API_KEY", "").strip()

app = FastAPI(title="Kamas Front")
app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")
app.mount("/icons", StaticFiles(directory=str(ICONS)), name="icons")
templates = Jinja2Templates(directory=str(WEB / "templates"))
templates.env.globals["static_version"] = str(int((WEB / "static" / "theme.css").stat().st_mtime))

_client = httpx.Client(
    base_url=API_URL,
    timeout=15,
    headers={"X-API-Key": API_KEY} if API_KEY else {},
)
CATEGORIES = ("ressource", "familier", "equipement")

ERROR_MESSAGES = {
    "invalid_input": "Le formulaire contient une valeur manquante ou invalide — vérifie les champs et réessaie.",
    "empty_name": "Le nom ne peut pas être vide.",
    "has_data": "Impossible de supprimer ce serveur : il a déjà des prix ou des suivis enregistrés. Renomme-le si besoin, ou supprime d'abord ses données.",
    "last_server": "Impossible de supprimer le dernier serveur restant — il en faut toujours au moins un.",
    "missing_price": "Renseigne un prix (supérieur à 0) avant de valider.",
    "invalid_quantity_or_price": "La quantité et le prix doivent être des nombres positifs.",
    "unknown_item": "Cet objet est introuvable en base — recharge la page et réessaie.",
}


def _error_message(code: str) -> str | None:
    return ERROR_MESSAGES.get(code) if code else None


def _redirect_with_error(url: str, code: str) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep}error={code}", status_code=303)


def _get(path: str, **params):
    return _client.get(path, params={k: v for k, v in params.items() if v is not None}).json()


def _post(path: str, data: dict):
    return _client.post(path, data={k: v for k, v in data.items() if v is not None}).json()


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(request: Request, exc: RequestValidationError):
    return _redirect_with_error(request.headers.get("referer") or "/", "invalid_input")


def _tpl(request, name, ctx):
    return templates.TemplateResponse(request, name, ctx)


# ── pages ────────────────────────────────────────────────────────────────
@app.get("/")
def dashboard(request: Request):
    return _tpl(request, "dashboard.html", {"active": "dashboard", **_get("/v1/dashboard")})


@app.get("/crafts")
def crafts_page(
    request: Request, q: str = "", job_id: int | None = None,
    level_min: int | None = None, level_max: int | None = None,
):
    payload = _get("/v1/crafts", q=q, job_id=job_id, level_min=level_min, level_max=level_max)
    return _tpl(request, "crafts.html", {
        "active": "crafts", "query": q,
        "job_id": job_id, "level_min": level_min, "level_max": level_max,
        **payload,
    })


@app.get("/pets")
def pets_page(request: Request):
    return _tpl(request, "pets.html", {"active": "pets", **_get("/v1/pets")})


@app.get("/elevage")
def elevage_page(request: Request, range_from: int = 0, range_to: int = 100000):
    payload = _get("/v1/elevage", range_from=range_from, range_to=range_to)
    return _tpl(request, "elevage.html", {"active": "elevage", **payload})


@app.get("/prices")
def prices_page(request: Request, category: str = "ressource"):
    if category not in CATEGORIES:
        category = "ressource"
    return _tpl(request, "prices.html", {"active": "prices", "category": category, "resources": _get("/v1/prices", category=category)})


@app.get("/settings")
def settings_page(request: Request):
    cfg = _get("/v1/settings")
    return _tpl(request, "settings.html", {"active": "settings", "safety_margin": cfg["safety_margin_percent"], "hdv_tax": cfg["hdv_tax_percent"]})


@app.get("/servers")
def servers_page(request: Request, error: str = ""):
    data = _get("/v1/servers")
    servers = [(s["id"], s["name"], s["slug"]) for s in data["servers"]]  # servers.html attend des tuples
    return _tpl(request, "servers.html", {"active": "servers", "servers": servers, "active_id": data["active_id"], "error_message": _error_message(error)})


@app.get("/suivis")
def suivis_page(request: Request, error: str = ""):
    return _tpl(request, "suivis.html", {"active": "suivis", "error_message": _error_message(error), **_get("/v1/suivis")})


@app.get("/suivis/new")
def suivis_new_page(
    request: Request,
    item_id: int,
    category: str = "ressource",
    suggested_price: int | None = None,
    quantity: int = 1,
    buy_price_kamas: int | None = None,
    sell_price_kamas: int | None = None,
    atelier_id: int | None = None,
    error: str = "",
):
    if category not in CATEGORIES:
        category = "ressource"
    try:
        item = _get(f"/v1/items/{item_id}")
    except Exception:
        return _redirect_with_error("/suivis", "unknown_item")
    if not item or "name" not in item:
        return _redirect_with_error("/suivis", "unknown_item")
    return _tpl(request, "suivis_new.html", {
        "active": "suivis", "item_id": item_id, "item_name": item["name"], "category": category,
        "atelier_id": atelier_id, "quantity": quantity,
        "buy_price_kamas": buy_price_kamas if buy_price_kamas is not None else suggested_price,
        "sell_price_kamas": sell_price_kamas, "error_message": _error_message(error),
    })


@app.get("/atelier")
def atelier_page(request: Request):
    return _tpl(request, "atelier.html", {"active": "atelier", **_get("/v1/atelier")})


_DEFAULT_HISTORY_ITEM = {
    "median": None, "mean": None, "quantity_sold": None,
    "lot_prices": {1: None, 10: None, 100: None, 1000: None},
    "pet_level_prices": {0: None, 100: None}, "xp_value": None, "price_per_xp": None,
}


@app.get("/history")
def history_page(request: Request, item_id: int | None = None, category: str = "ressource"):
    if category not in CATEGORIES:
        category = "ressource"
    available = _get("/v1/history/available", category=category)
    selected_id = item_id or (available[0]["id"] if available else None)
    selected_name = next((a["name"] for a in available if a["id"] == selected_id), None)
    if selected_name is None and selected_id is not None:
        try:
            selected_name = _get(f"/v1/items/{selected_id}").get("name")
        except Exception:
            selected_name = None
    if selected_id is not None:
        item_data = _get("/v1/history", item_id=selected_id, category=category)
        # l'API JSON-ise les clés int en str : on les reconvertit pour l'accès
        # Jinja lot_prices[1] / pet_level_prices[0].
        item_data["lot_prices"] = {int(k): v for k, v in item_data["lot_prices"].items()}
        item_data["pet_level_prices"] = {int(k): v for k, v in item_data["pet_level_prices"].items()}
    else:
        item_data = dict(_DEFAULT_HISTORY_ITEM)
    return _tpl(request, "history.html", {
        "active": "history", "category": category, "available": available,
        "resources_json": json.dumps([{"id": a["id"], "name": a["name"]} for a in available]),
        "selected_id": selected_id, "selected_name": selected_name, **item_data,
    })


# ── proxies JSON (rafraîchissement auto des templates) ───────────────────
@app.get("/api/dashboard")
def api_dashboard():
    return JSONResponse(_get("/v1/dashboard"))


@app.get("/api/crafts")
def api_crafts(q: str = "", job_id: int | None = None, level_min: int | None = None, level_max: int | None = None):
    return JSONResponse(_get("/v1/crafts", q=q, job_id=job_id, level_min=level_min, level_max=level_max))


@app.get("/api/pets")
def api_pets():
    return JSONResponse(_get("/v1/pets"))


@app.get("/api/elevage")
def api_elevage(range_from: int = 0, range_to: int = 100000):
    return JSONResponse(_get("/v1/elevage", range_from=range_from, range_to=range_to))


@app.get("/api/prices")
def api_prices(category: str = "ressource"):
    return JSONResponse(_get("/v1/prices", category=category))


@app.get("/api/atelier")
def api_atelier():
    return JSONResponse(_get("/v1/atelier"))


@app.get("/api/history")
def api_history(item_id: int | None = None, category: str = "ressource"):
    if item_id is None:
        return JSONResponse(dict(_DEFAULT_HISTORY_ITEM))
    return JSONResponse(_get("/v1/history", item_id=item_id, category=category))


@app.get("/api/servers")
def api_servers():
    return JSONResponse(_get("/v1/servers"))


@app.get("/api/status")
def api_status():
    """Fraîcheur des données (dernier relevé) — alimente le badge de la sidebar."""
    try:
        return JSONResponse(_get("/v1/status"))
    except Exception:
        return JSONResponse({"last_capture_at": None}, status_code=503)


# ── actions POST : réglages / serveurs (form -> redirect) ────────────────
@app.post("/settings")
def update_settings(safety_margin_percent: float = Form(...)):
    _post("/v1/settings", {"safety_margin_percent": safety_margin_percent})
    return RedirectResponse("/settings", status_code=303)


@app.post("/servers/active")
def set_active_server(request: Request, server_id: int = Form(...)):
    _post("/v1/servers/active", {"server_id": server_id})
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@app.post("/servers")
def create_server(name: str = Form("")):
    res = _post("/v1/servers", {"name": name})
    return RedirectResponse("/servers", 303) if res.get("ok") else _redirect_with_error("/servers", res.get("error", "invalid_input"))


@app.post("/servers/{server_id}/rename")
def rename_server(server_id: int, name: str = Form("")):
    res = _post(f"/v1/servers/{server_id}/rename", {"name": name})
    return RedirectResponse("/servers", 303) if res.get("ok") else _redirect_with_error("/servers", res.get("error", "invalid_input"))


@app.post("/servers/{server_id}/delete")
def delete_server(server_id: int):
    res = _post(f"/v1/servers/{server_id}/delete", {})
    return RedirectResponse("/servers", 303) if res.get("ok") else _redirect_with_error("/servers", res.get("error", "invalid_input"))


# ── actions POST : prix / xp manuels (form -> redirect) ──────────────────
@app.post("/history/price")
def update_manual_price(
    item_id: int = Form(...), price_kamas: int = Form(...),
    lot_size: int | None = Form(None), pet_level: int | None = Form(None),
    category: str = Form("ressource"),
):
    _post("/v1/history/price", {"item_id": item_id, "price_kamas": price_kamas, "lot_size": lot_size, "pet_level": pet_level})
    return RedirectResponse(f"/history?item_id={item_id}&category={category}", status_code=303)


@app.post("/history/xp")
def update_manual_xp(item_id: int = Form(...), xp_value: int = Form(...)):
    _post("/v1/history/xp", {"item_id": item_id, "xp_value": xp_value})
    return RedirectResponse(f"/history?item_id={item_id}&category=ressource", status_code=303)


# ── actions POST : suivis (form -> redirect) ─────────────────────────────
@app.post("/suivis/add")
def add_tracked_trade(
    request: Request,
    item_id: int = Form(...), category: str = Form("ressource"),
    quantity: str = Form(""), buy_price_kamas: str = Form(""),
    sell_price_kamas: str = Form(""), atelier_id: str = Form(""),
):
    res = _post("/v1/suivis", {"item_id": item_id, "quantity": quantity, "buy_price_kamas": buy_price_kamas,
                               "sell_price_kamas": sell_price_kamas, "atelier_id": atelier_id})
    # Bouton "Crafté → Suivis" de l'Atelier : appel fetch -> JSON, l'utilisateur
    # RESTE sur l'Atelier (toast de réussite, pas de redirection).
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(res)
    if res.get("ok"):
        # venu de l'Atelier (bouton "Crafté → Suivis") -> on y retourne pour
        # enchaîner les crafts ; sinon on va sur Suivis.
        return RedirectResponse("/atelier" if atelier_id else "/suivis", status_code=303)
    retry = f"/suivis/new?item_id={item_id}&category={category}&quantity={quantity or ''}&buy_price_kamas={buy_price_kamas or ''}"
    if atelier_id:
        retry += f"&atelier_id={atelier_id}"
    return _redirect_with_error(retry, res.get("error", "invalid_input"))


@app.post("/suivis/{trade_id}/forge-confirm")
def confirm_forgemagie(trade_id: int, rune_cost_kamas: str = Form(""), sell_price_kamas: str = Form("")):
    _post(f"/v1/suivis/{trade_id}/forge-confirm",
          {"rune_cost_kamas": rune_cost_kamas, "sell_price_kamas": sell_price_kamas})
    return RedirectResponse("/suivis", status_code=303)


@app.post("/suivis/{trade_id}/buy-total")
def update_buy_total(request: Request, trade_id: int, buy_price_kamas: str = Form("")):
    res = _post(f"/v1/suivis/{trade_id}/buy-total", {"buy_price_kamas": buy_price_kamas})
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(res)
    return RedirectResponse("/suivis", 303)


@app.post("/suivis/{trade_id}/buy-price")
def update_buy_price(request: Request, trade_id: int, buy_price_kamas: str = Form("")):
    res = _post(f"/v1/suivis/{trade_id}/buy-price", {"buy_price_kamas": buy_price_kamas})
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(res)
    return RedirectResponse("/suivis", 303)


@app.post("/suivis/{trade_id}/rune-cost")
def update_rune_cost(request: Request, trade_id: int, rune_cost_kamas: str = Form("")):
    res = _post(f"/v1/suivis/{trade_id}/rune-cost", {"rune_cost_kamas": rune_cost_kamas})
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(res)
    return RedirectResponse("/suivis", 303)


@app.post("/suivis/{trade_id}/price")
def update_tracked_trade_price(request: Request, trade_id: int, sell_price_kamas: str = Form("")):
    res = _post(f"/v1/suivis/{trade_id}/price", {"sell_price_kamas": sell_price_kamas})
    # appel auto-save (fetch) -> JSON silencieux ; sinon repli redirect (no-JS).
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(res)
    return RedirectResponse("/suivis", 303) if res.get("ok") else _redirect_with_error("/suivis", res.get("error", "missing_price"))


@app.post("/suivis/{trade_id}/sell")
def mark_tracked_trade_sold(trade_id: int, sell_price_kamas: str = Form("")):
    res = _post(f"/v1/suivis/{trade_id}/sell", {"sell_price_kamas": sell_price_kamas})
    return RedirectResponse("/suivis", 303) if res.get("ok") else _redirect_with_error("/suivis", res.get("error", "missing_price"))


@app.post("/suivis/{trade_id}/reopen")
def reopen_tracked_trade(trade_id: int):
    _post(f"/v1/suivis/{trade_id}/reopen", {})
    return RedirectResponse("/suivis", status_code=303)


@app.post("/suivis/{trade_id}/delete")
def delete_tracked_trade(trade_id: int):
    _post(f"/v1/suivis/{trade_id}/delete", {})
    return RedirectResponse("/suivis", status_code=303)


# ── actions POST : atelier (fetch -> JSON, sauf remove -> redirect) ──────
@app.post("/atelier/add")
def atelier_add(item_id: int = Form(...), kind: str = Form(...), quantity: str = Form("1")):
    return JSONResponse(_post("/v1/atelier", {"item_id": item_id, "kind": kind, "quantity": quantity}))


@app.post("/atelier/{atelier_id}/quantity")
def atelier_quantity(atelier_id: int, quantity: str = Form("")):
    return JSONResponse(_post(f"/v1/atelier/{atelier_id}/quantity", {"quantity": quantity}))


@app.post("/atelier/{atelier_id}/remove")
def atelier_remove(atelier_id: int):
    _post(f"/v1/atelier/{atelier_id}/remove", {})
    return RedirectResponse("/atelier", status_code=303)


@app.post("/atelier/resource-owned")
def atelier_resource_owned(resource_item_id: int = Form(...), quantity: str = Form("")):
    return JSONResponse(_post("/v1/atelier/resource-owned", {"resource_item_id": resource_item_id, "quantity": quantity}))


@app.post("/atelier/{atelier_id}/feed")
def atelier_feed(atelier_id: int, feed_resource_item_id: str = Form(""), feed_qty_owned: str = Form("")):
    return JSONResponse(_post(f"/v1/atelier/{atelier_id}/feed", {"feed_resource_item_id": feed_resource_item_id, "feed_qty_owned": feed_qty_owned}))
