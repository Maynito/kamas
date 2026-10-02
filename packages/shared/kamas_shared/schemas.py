"""Contrat d'ingestion partagé entre le Collector (qui produit les relevés
depuis la vision/OCR) et la Price API (qui les valide et les stocke).

Ces modèles reflètent EXACTEMENT ce que renvoient aujourd'hui les handlers
vision (`src/watcher/*_handler.py`), moins la partie écriture DB : le collector
n'écrit plus en base, il POST l'un de ces payloads à `/v1/ingest/*` et c'est
l'API qui persiste. `item_id` est requis : le collector ne POST QUE des relevés
confiants (il filtre `low_confidence` avant l'envoi, cf. garde-fous existants).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Panel = Literal["hdv_detail", "familiar_hdv", "equipment_hdv", "market_history", "pet_feed"]
Period = Literal["24h", "7j", "30j"]


class ReadingBase(BaseModel):
    server_id: int
    item_id: int
    item_name: Optional[str] = None
    confidence: Optional[float] = None
    low_confidence: bool = False
    raw_name_text: Optional[str] = None
    captured_at: Optional[str] = None  # ISO 8601 ; l'API met "maintenant" si absent


class HdvDetailReading(ReadingBase):
    """Panneau détail ressource : un prix par taille de lot (1/10/100/1000)."""
    panel: Literal["hdv_detail"] = "hdv_detail"
    prices: dict[int, int]  # lot_size -> prix


class EquipmentReading(ReadingBase):
    """Panneau détail équipement : prix unitaire de la 1re annonce."""
    panel: Literal["equipment_hdv"] = "equipment_hdv"
    price: int


class FamiliarReading(ReadingBase):
    """Panneau détail familier : prix confirmés aux niveaux 0 et/ou 100."""
    panel: Literal["familiar_hdv"] = "familiar_hdv"
    price_level_0: Optional[int] = None
    price_level_100: Optional[int] = None


class MarketReading(ReadingBase):
    """Panneau "Cours du marché" : médian/moyen/quantité vendue pour un onglet."""
    panel: Literal["market_history"] = "market_history"
    period: Period
    median: Optional[int] = None
    mean: Optional[int] = None
    quantity_sold: Optional[int] = None
