"""Price API — endpoints d'ÉCRITURE (Phase 1).

Versions JSON propres des routes POST du monolithe : elles renvoient
`{"ok": true, ...}` ou `{"ok": false, "error": "..."}` au lieu de redirections
(l'UX — toasts, ré-affichage, messages — est gérée par le front). Le serveur
"actif" est résolu côté API (comme dans le monolithe) via data._active_server_id.
Aucune dépendance vision : l'upsert XP manuel est inliné (il vit sinon dans un
module vision, cf. pet_feed_handler.upsert_feed_xp).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form
from sqlalchemy.exc import IntegrityError

from app.db import get_conn as get_connection

from app import data

router = APIRouter(prefix="/v1", tags=["write"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_int_str(raw: str) -> str:
    # Retire TOUS les espaces (séparateurs de milliers "1 000" saisis côté UI)
    # avant conversion — filet de sécurité si le strip côté client rate.
    return re.sub(r"\s+", "", raw or "")


def _parse_positive_int(raw: str) -> int | None:
    try:
        value = int(_clean_int_str(raw))
    except (ValueError, AttributeError):
        return None
    return value if value > 0 else None


def _parse_nonneg_int(raw: str) -> int:
    try:
        value = int(_clean_int_str(raw))
    except (ValueError, AttributeError):
        return 0
    return value if value >= 0 else 0


def _split_evenly(total: int | None, n: int) -> list[int | None]:
    """Répartit un total en n parts entières (les `reste` premières reçoivent +1),
    de sorte que la somme reste EXACTEMENT `total`. Renvoie [None]*n si total est
    None (le prix de vente visé est optionnel)."""
    if total is None:
        return [None] * n
    per, rem = divmod(total, n)
    return [per + (1 if i < rem else 0) for i in range(n)]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "serveur"


def _unique_slug(conn, base_slug: str) -> str:
    slug, n = base_slug, 2
    while conn.execute("SELECT 1 FROM servers WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{n}"
        n += 1
    return slug


# ── réglages ──────────────────────────────────────────────────────────────
@router.post("/settings")
def update_settings(safety_margin_percent: float = Form(...)):
    safety_margin_percent = max(0.0, min(20.0, safety_margin_percent))
    conn = get_connection()
    conn.execute(
        "UPDATE app_config SET value = ? WHERE key = 'safety_margin_percent'",
        (str(safety_margin_percent),),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "safety_margin_percent": safety_margin_percent}


# ── serveurs ────────────────────────────────────────────────────────────────
@router.post("/servers/active")
def set_active_server(server_id: int = Form(...)):
    conn = get_connection()
    if conn.execute("SELECT 1 FROM servers WHERE id = ?", (server_id,)).fetchone():
        conn.execute("UPDATE app_config SET value = ? WHERE key = 'active_server_id'", (str(server_id),))
        conn.commit()
    conn.close()
    return {"ok": True, "active_id": server_id}


@router.post("/servers")
def create_server(name: str = Form("")):
    name = name.strip()
    if not name:
        return {"ok": False, "error": "empty_name"}
    conn = get_connection()
    slug = _unique_slug(conn, _slugify(name))
    conn.execute("INSERT INTO servers (name, slug) VALUES (?, ?)", (name, slug))
    conn.commit()
    new_id = conn.execute("SELECT id FROM servers WHERE slug = ?", (slug,)).fetchone()[0]
    conn.close()
    return {"ok": True, "id": new_id, "name": name}


@router.post("/servers/{server_id}/rename")
def rename_server(server_id: int, name: str = Form("")):
    name = name.strip()
    if not name:
        return {"ok": False, "error": "empty_name"}
    conn = get_connection()
    conn.execute("UPDATE servers SET name = ? WHERE id = ?", (name, server_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/servers/{server_id}/delete")
def delete_server(server_id: int):
    conn = get_connection()
    if conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0] <= 1:
        conn.close()
        return {"ok": False, "error": "last_server"}
    try:
        conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        conn.commit()
    except IntegrityError:
        conn.close()
        return {"ok": False, "error": "has_data"}
    conn.close()
    return {"ok": True}


# ── prix / xp manuels ───────────────────────────────────────────────────────
@router.post("/history/price")
def update_manual_price(
    item_id: int = Form(...),
    price_kamas: int = Form(...),
    lot_size: int | None = Form(None),
    pet_level: int | None = Form(None),
):
    if price_kamas <= 0:
        return {"ok": False, "error": "invalid_price"}
    conn = get_connection()
    server_id = data._active_server_id(conn)
    if pet_level is not None and pet_level in (0, 100):
        conn.execute(
            "INSERT INTO price_snapshots (server_id, item_id, lot_size, pet_level, price_kamas, source, confidence, captured_at) "
            "VALUES (?, ?, 1, ?, ?, 'manual', 1.0, ?)",
            (server_id, item_id, pet_level, price_kamas, _now()),
        )
        conn.commit()
    elif lot_size in (1, 10, 100, 1000):
        conn.execute(
            "INSERT INTO price_snapshots (server_id, item_id, lot_size, price_kamas, source, confidence, captured_at) "
            "VALUES (?, ?, ?, ?, 'manual', 1.0, ?)",
            (server_id, item_id, lot_size, price_kamas, _now()),
        )
        conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/history/xp")
def update_manual_xp(item_id: int = Form(...), xp_value: int = Form(...)):
    if xp_value <= 0:
        return {"ok": False, "error": "invalid_xp"}
    conn = get_connection()
    # Inliné (cf. pet_feed_handler.upsert_feed_xp) pour garder l'API sans vision.
    conn.execute(
        "INSERT INTO pet_feed_xp (resource_item_id, xp_value, source, updated_at) VALUES (?, ?, 'manual', ?) "
        "ON CONFLICT(resource_item_id) DO UPDATE SET xp_value = excluded.xp_value, source = excluded.source, updated_at = excluded.updated_at",
        (item_id, xp_value, _now()),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ── suivis (tracked_trades) ─────────────────────────────────────────────────
@router.post("/suivis")
def add_tracked_trade(
    item_id: int = Form(...),
    quantity: str = Form(""),
    buy_price_kamas: str = Form(""),
    sell_price_kamas: str = Form(""),
    atelier_id: str = Form(""),
):
    parsed_quantity = _parse_positive_int(quantity)
    parsed_buy_price = _parse_positive_int(buy_price_kamas)
    parsed_sell_price = _parse_positive_int(sell_price_kamas)
    if parsed_quantity is None or parsed_buy_price is None:
        return {"ok": False, "error": "invalid_quantity_or_price"}
    conn = get_connection()
    server_id = data._active_server_id(conn)
    # Les ÉQUIPEMENTS passent d'abord par l'étape "forgemagie" (saisie du coût
    # en runes avant mise en vente) ; ressources/familiers vont direct en vente.
    super_row = conn.execute("SELECT super_type_name FROM items WHERE id = ?", (item_id,)).fetchone()
    stage = "forge" if (super_row and data._category_for_super_type(super_row[0]) == "equipement") else "live"
    try:
        # Une position PAR EXEMPLAIRE : quantité N -> N lignes de quantité 1
        # (prix d'achat total réparti à l'unité), pour que chaque exemplaire ait
        # SON propre prix de vente et soit vendable indépendamment.
        buys = _split_evenly(parsed_buy_price, parsed_quantity)
        sells = _split_evenly(parsed_sell_price, parsed_quantity)
        now = _now()
        for i in range(parsed_quantity):
            conn.execute(
                "INSERT INTO tracked_trades (server_id, item_id, quantity, buy_price_kamas, sell_price_kamas, status, stage, created_at) "
                "VALUES (?, ?, 1, ?, ?, 'holding', ?, ?)",
                (server_id, item_id, buys[i], sells[i], stage, now),
            )
        parsed_atelier_id = _parse_positive_int(atelier_id)
        if parsed_atelier_id is not None:
            entry = conn.execute("SELECT kind, item_id FROM atelier_items WHERE id = ?", (parsed_atelier_id,)).fetchone()
            if entry and entry[0] == "equipment":
                recipe_id = data._recipe_id_for_item(conn, entry[1])
                if recipe_id is not None:
                    for ing_id, base_qty in conn.execute(
                        "SELECT ingredient_item_id, quantity FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
                    ):
                        owned_row = conn.execute(
                            "SELECT quantity_owned FROM atelier_owned_resources "
                            "WHERE server_id = ? AND resource_item_id = ?",
                            (server_id, ing_id),
                        ).fetchone()
                        if owned_row is not None:
                            conn.execute(
                                "UPDATE atelier_owned_resources SET quantity_owned = ? "
                                "WHERE server_id = ? AND resource_item_id = ?",
                                (max(0, owned_row[0] - base_qty * parsed_quantity), server_id, ing_id),
                            )
            conn.execute("DELETE FROM atelier_items WHERE id = ?", (parsed_atelier_id,))
        conn.commit()
    except IntegrityError:
        conn.close()
        return {"ok": False, "error": "unknown_item"}
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/forge-confirm")
def confirm_forgemagie(trade_id: int, rune_cost_kamas: str = Form(""), sell_price_kamas: str = Form("")):
    """Fin de forgemagie : enregistre le coût en runes (0 si vide) ET le prix de
    vente visé (le prix n'est fixé qu'une fois l'item forgemagé), puis fait
    passer l'équipement de l'étape 'forge' à 'live' (En cours). Le prix de vente
    est optionnel : vide -> on garde celui déjà présent (COALESCE)."""
    rune = _parse_nonneg_int(rune_cost_kamas)
    sell = _parse_positive_int(sell_price_kamas)  # None si vide/invalide
    conn = get_connection()
    conn.execute(
        "UPDATE tracked_trades SET rune_cost_kamas = ?, "
        "sell_price_kamas = COALESCE(?, sell_price_kamas), stage = 'live' "
        "WHERE id = ? AND stage = 'forge'",
        (rune, sell, trade_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/buy-total")
def update_buy_total(trade_id: int, buy_price_kamas: str = Form("")):
    """Ajuste le prix d'achat TOTAL à l'unité d'un item déjà sorti de forgemagie
    (En cours / Vendu). La valeur affichée = coût effectif = buy + runes ; on
    stocke donc buy = total - runes (runes préservées, plancher 0). Réservé au
    stage 'live' (les items en 'forge' passent par /buy-price qui édite le buy
    brut, les runes y étant saisies à part)."""
    total = _parse_positive_int(buy_price_kamas)
    if total is None:
        return {"ok": False, "error": "missing_price"}
    conn = get_connection()
    conn.execute(
        "UPDATE tracked_trades SET buy_price_kamas = MAX(0, ? - COALESCE(rune_cost_kamas, 0)) "
        "WHERE id = ? AND stage = 'live'",
        (total, trade_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/buy-price")
def update_buy_price(trade_id: int, buy_price_kamas: str = Form("")):
    """Ajuste le prix d'achat/craft d'un item EN FORGEMAGIE (le "Suivre" direct
    le pré-remplit avec le coût de craft estimé — l'utilisateur peut corriger
    vers ce qu'il a réellement payé). Sans effet hors stage 'forge'. Un prix
    vide/invalide est ignoré (on ne remet pas 0 par accident)."""
    price = _parse_positive_int(buy_price_kamas)
    if price is None:
        return {"ok": False, "error": "missing_price"}
    conn = get_connection()
    conn.execute(
        "UPDATE tracked_trades SET buy_price_kamas = ? WHERE id = ? AND stage = 'forge'",
        (price, trade_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/rune-cost")
def update_rune_cost(trade_id: int, rune_cost_kamas: str = Form("")):
    """Auto-sauvegarde du coût en runes d'un item EN FORGEMAGIE, SANS le faire
    passer en vente (contrairement à forge-confirm). Permet de saisir les coûts
    runes de plusieurs items avant d'en confirmer un seul, sans perdre les
    autres au rechargement de la page."""
    rune = _parse_nonneg_int(rune_cost_kamas)
    conn = get_connection()
    conn.execute(
        "UPDATE tracked_trades SET rune_cost_kamas = ? WHERE id = ? AND stage = 'forge'",
        (rune, trade_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/price")
def update_tracked_trade_price(trade_id: int, sell_price_kamas: str = Form("")):
    price = _parse_positive_int(sell_price_kamas)
    if price is None:
        return {"ok": False, "error": "missing_price"}
    conn = get_connection()
    conn.execute("UPDATE tracked_trades SET sell_price_kamas = ? WHERE id = ?", (price, trade_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/sell")
def mark_tracked_trade_sold(trade_id: int, sell_price_kamas: str = Form("")):
    price = _parse_positive_int(sell_price_kamas)
    if price is None:
        return {"ok": False, "error": "missing_price"}
    conn = get_connection()
    conn.execute(
        "UPDATE tracked_trades SET sell_price_kamas = ?, status = 'sold', sold_at = ? WHERE id = ?",
        (price, _now(), trade_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/reopen")
def reopen_tracked_trade(trade_id: int):
    """Remet une position vendue en vente (En cours) : status 'sold' -> 'holding',
    stage 'live', date de vente effacée. Le prix de vente est conservé comme prix
    visé. Sans effet si la position n'est pas vendue."""
    conn = get_connection()
    conn.execute(
        "UPDATE tracked_trades SET status = 'holding', stage = 'live', sold_at = NULL WHERE id = ? AND status = 'sold'",
        (trade_id,),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/suivis/{trade_id}/delete")
def delete_tracked_trade(trade_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM tracked_trades WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── atelier ─────────────────────────────────────────────────────────────────
@router.post("/atelier")
def atelier_add(item_id: int = Form(...), kind: str = Form(...), quantity: str = Form("1")):
    if kind not in ("equipment", "pet"):
        return {"ok": False, "error": "invalid_input"}
    qty = max(1, _parse_nonneg_int(quantity) or 1)
    conn = get_connection()
    server_id = data._active_server_id(conn)
    name_row = conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
    if name_row is None:
        conn.close()
        return {"ok": False, "error": "unknown_item"}
    conn.execute(
        "INSERT INTO atelier_items (server_id, item_id, kind, quantity, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(server_id, item_id, kind) DO UPDATE SET quantity = quantity + excluded.quantity",
        (server_id, item_id, kind, qty, _now()),
    )
    total = conn.execute(
        "SELECT quantity FROM atelier_items WHERE server_id = ? AND item_id = ? AND kind = ?",
        (server_id, item_id, kind),
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return {"ok": True, "name": name_row[0], "added": qty, "total": total}


@router.post("/atelier/{atelier_id}/quantity")
def atelier_quantity(atelier_id: int, quantity: str = Form("")):
    qty = max(1, _parse_nonneg_int(quantity) or 1)
    conn = get_connection()
    conn.execute("UPDATE atelier_items SET quantity = ? WHERE id = ?", (qty, atelier_id))
    conn.commit()
    conn.close()
    return {"ok": True, "quantity": qty}


@router.post("/atelier/{atelier_id}/remove")
def atelier_remove(atelier_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM atelier_items WHERE id = ?", (atelier_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/atelier/resource-owned")
def atelier_resource_owned(resource_item_id: int = Form(...), quantity: str = Form("")):
    qty = _parse_nonneg_int(quantity)
    conn = get_connection()
    server_id = data._active_server_id(conn)
    conn.execute(
        "INSERT INTO atelier_owned_resources (server_id, resource_item_id, quantity_owned) VALUES (?, ?, ?) "
        "ON CONFLICT(server_id, resource_item_id) DO UPDATE SET quantity_owned = excluded.quantity_owned",
        (server_id, resource_item_id, qty),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "quantity": qty}


@router.post("/atelier/{atelier_id}/feed")
def atelier_feed(atelier_id: int, feed_resource_item_id: str = Form(""), feed_qty_owned: str = Form("")):
    res = _parse_positive_int(feed_resource_item_id)
    qty = _parse_nonneg_int(feed_qty_owned)
    conn = get_connection()
    conn.execute(
        "UPDATE atelier_items SET feed_resource_item_id = ?, feed_qty_owned = ? WHERE id = ?",
        (res, qty, atelier_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}
