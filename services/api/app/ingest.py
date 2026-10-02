"""Price API — endpoints d'INGESTION (Phase 2).

Reçoivent les relevés produits par le Collector (capture + vision, sur la
machine du joueur) et les persistent via src/db/reading_store.py — même
garde-fou item_id + low_confidence. Body JSON validé par le contrat partagé
(packages/shared). Distinct des endpoints d'écriture "front" (/v1/suivis...,
form-encoded) : ici c'est machine-à-machine, JSON, protégé par une clé
d'ingestion (X-Ingest-Key) si KAMAS_INGEST_KEY est défini (durci en phase 4).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from app.db import get_conn as get_connection
import reading_store
from kamas_shared import (
    EquipmentReading,
    FamiliarReading,
    HdvDetailReading,
    MarketReading,
)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])
_INGEST_KEY = os.environ.get("KAMAS_INGEST_KEY", "")


def _auth(key: str | None) -> None:
    if _INGEST_KEY and key != _INGEST_KEY:
        raise HTTPException(status_code=401, detail="clé d'ingestion invalide")


def _store(persist_fn, reading) -> dict:
    # Le collector filtre déjà les low_confidence ; on rejette ici par sécurité
    # (défense en profondeur — un relevé peu sûr ne doit jamais entrer en base).
    if reading.low_confidence:
        raise HTTPException(status_code=422, detail="relevé low_confidence rejeté")
    conn = get_connection()
    stored = persist_fn(conn, reading.server_id, reading.model_dump())
    conn.close()
    return {"ok": True, "stored": stored}


@router.post("/hdv")
def ingest_hdv(reading: HdvDetailReading, x_ingest_key: str | None = Header(default=None)):
    """Relevé du panneau détail ressource (prix par lot)."""
    _auth(x_ingest_key)
    return _store(reading_store.persist_hdv, reading)


@router.post("/equipment")
def ingest_equipment(reading: EquipmentReading, x_ingest_key: str | None = Header(default=None)):
    """Relevé du panneau détail équipement (prix unitaire)."""
    _auth(x_ingest_key)
    return _store(reading_store.persist_equipment, reading)


@router.post("/familiar")
def ingest_familiar(reading: FamiliarReading, x_ingest_key: str | None = Header(default=None)):
    """Relevé du panneau détail familier (prix niveaux 0/100 confirmés)."""
    _auth(x_ingest_key)
    return _store(reading_store.persist_familiar, reading)


@router.post("/market")
def ingest_market(reading: MarketReading, x_ingest_key: str | None = Header(default=None)):
    """Relevé du panneau "Cours du marché" (médian/moyen/vendus)."""
    _auth(x_ingest_key)
    return _store(reading_store.persist_market, reading)
