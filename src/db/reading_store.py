"""Persistance des relevés (readings) de la vision -> base.

Extrait des handlers (devenus PURS) : c'est désormais le SEUL endroit qui écrit
un relevé de prix. Réutilisé par la Price API (endpoints /v1/ingest/*) ET par
les tests (pour poser un état de base après un handler, sans dupliquer le SQL).
SQL pur, AUCUNE dépendance vision : l'API reste légère en l'important.

Le garde-fou historique est ici : un relevé sans item_id ou `low_confidence`
n'est JAMAIS écrit (régression réelle — un prix attribué au mauvais objet
pollue durablement son historique, cf. commentaires des handlers d'origine).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skip(reading: dict) -> bool:
    return reading.get("item_id") is None or reading.get("low_confidence", True)


def persist_hdv(conn, server_id: int, reading: dict) -> int:
    """Panneau détail ressource : un price_snapshot par lot renseigné."""
    if _skip(reading):
        return 0
    ts, n = _now(), 0
    for lot, price in (reading.get("prices") or {}).items():
        if price is None:
            continue
        conn.execute(
            "INSERT INTO price_snapshots (server_id, item_id, lot_size, price_kamas, source, confidence, captured_at) "
            "VALUES (?, ?, ?, ?, 'detail_panel', ?, ?)",
            (server_id, reading["item_id"], int(lot), price, reading.get("confidence"), ts),
        )
        n += 1
    conn.commit()
    return n


def persist_equipment(conn, server_id: int, reading: dict) -> int:
    """Panneau détail équipement : un price_snapshot lot 1."""
    if _skip(reading) or reading.get("price") is None:
        return 0
    conn.execute(
        "INSERT INTO price_snapshots (server_id, item_id, lot_size, price_kamas, source, confidence, captured_at) "
        "VALUES (?, ?, 1, ?, 'detail_panel', ?, ?)",
        (server_id, reading["item_id"], reading["price"], reading.get("confidence"), _now()),
    )
    conn.commit()
    return 1


def persist_familiar(conn, server_id: int, reading: dict) -> int:
    """Panneau détail familier : price_snapshot(s) aux niveaux 0 et/ou 100."""
    if _skip(reading):
        return 0
    ts, n = _now(), 0
    for pet_level, price in ((0, reading.get("price_level_0")), (100, reading.get("price_level_100"))):
        if price is None:
            continue
        conn.execute(
            "INSERT INTO price_snapshots (server_id, item_id, lot_size, pet_level, price_kamas, source, confidence, captured_at) "
            "VALUES (?, ?, 1, ?, ?, 'detail_panel', ?, ?)",
            (server_id, reading["item_id"], pet_level, price, reading.get("confidence"), ts),
        )
        n += 1
    conn.commit()
    return n


def persist_market(conn, server_id: int, reading: dict) -> int:
    """Panneau "Cours du marché" : points médian/moyen + quantité vendue."""
    if _skip(reading):
        return 0
    period, qty, ts, n = reading.get("period") or "30j", reading.get("quantity_sold"), _now(), 0
    for metric, value in (("median", reading.get("median")), ("mean", reading.get("mean"))):
        if value is None:
            continue
        conn.execute(
            "INSERT INTO market_history_points (server_id, item_id, period, point_date, price_kamas, quantity_sold, metric, source, captured_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, 'ocr_summary', ?)",
            (server_id, reading["item_id"], period, value, qty, metric, ts),
        )
        n += 1
    conn.commit()
    return n


_DISPATCH = {
    "hdv_detail": persist_hdv,
    "equipment_hdv": persist_equipment,
    "familiar_hdv": persist_familiar,
    "market_history": persist_market,
}


def persist(conn, server_id: int, reading: dict) -> int:
    """Dispatch par `reading['panel']` — pour un relevé issu de process_capture()."""
    fn = _DISPATCH.get(reading.get("panel"))
    return fn(conn, server_id, reading) if fn else 0
