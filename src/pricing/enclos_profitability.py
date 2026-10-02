"""Rentabilité de remplissage des jauges d'enclos (élevage de dragodindes).

Chaque enclos a 6 jauges (Caresseur, Baffeur, Mangeoire, Abreuvoir, Dragofesse,
Foudroyeur) qui montent jusqu'à 100 000 et se remplissent avec des "Carburants
d'enclos" (Extraits/Philtres/Potions/Élixirs, tailles Minuscule→Gigantesque).
Chaque carburant AJOUTE une valeur fixe à sa jauge (+1000 à +5000) MAIS ne peut
la remplir que jusqu'à un PLAFOND (40 000, 70 000, 90 000, ou 100 000) : un
carburant "max 40 000" ne sert que pour les premiers 40 000, au-delà il faut un
carburant à plafond plus élevé. On cherche donc la façon la MOINS CHÈRE de
remplir chaque jauge de 0 à 100 000, en découpant en tranches et en prenant pour
chaque tranche le carburant au meilleur prix par point (prix unitaire / valeur).

`ENCLOS_FUELS` est une donnée de jeu STATIQUE (comme PET_CUMULATIVE_XP_BY_LEVEL),
extraite une fois de DofusDB (typeId 326, effets "Jauge de X +val (Max plafond)")
— pas de table/sync dédiés. Les PRIX, eux, viennent de price_snapshots (les
carburants sont des ressources, captées à l'HDV comme les autres).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from profitability import compute_craft_cost, get_unit_price

GAUGE_MAX = 100000

# item_id -> (jauge, valeur ajoutée par unité, plafond de remplissage)
ENCLOS_FUELS: dict[int, tuple[str, int, int]] = {
    33309: ("Baffeur", 1000, 40000),
    33310: ("Caresseur", 1000, 40000),
    33311: ("Dragofesse", 1000, 40000),
    33312: ("Foudroyeur", 1000, 40000),
    33313: ("Abreuvoir", 1000, 40000),
    33314: ("Mangeoire", 1000, 40000),
    33315: ("Baffeur", 2000, 40000),
    33316: ("Caresseur", 2000, 40000),
    33317: ("Dragofesse", 2000, 40000),
    33318: ("Foudroyeur", 2000, 40000),
    33319: ("Abreuvoir", 2000, 40000),
    33320: ("Mangeoire", 2000, 40000),
    33326: ("Baffeur", 3000, 40000),
    33327: ("Caresseur", 3000, 40000),
    33328: ("Dragofesse", 3000, 40000),
    33329: ("Foudroyeur", 3000, 40000),
    33330: ("Abreuvoir", 3000, 40000),
    33331: ("Mangeoire", 3000, 40000),
    33336: ("Baffeur", 4000, 40000),
    33337: ("Caresseur", 4000, 40000),
    33338: ("Dragofesse", 4000, 40000),
    33339: ("Foudroyeur", 4000, 40000),
    33340: ("Abreuvoir", 4000, 40000),
    33341: ("Mangeoire", 4000, 40000),
    33347: ("Baffeur", 5000, 40000),
    33348: ("Caresseur", 5000, 40000),
    33349: ("Dragofesse", 5000, 40000),
    33350: ("Foudroyeur", 5000, 40000),
    33351: ("Abreuvoir", 5000, 40000),
    33352: ("Mangeoire", 5000, 40000),
    33357: ("Baffeur", 1000, 70000),
    33358: ("Caresseur", 1000, 70000),
    33359: ("Dragofesse", 1000, 70000),
    33360: ("Foudroyeur", 1000, 70000),
    33361: ("Abreuvoir", 1000, 70000),
    33362: ("Mangeoire", 1000, 70000),
    33368: ("Baffeur", 2000, 70000),
    33369: ("Caresseur", 2000, 70000),
    33370: ("Dragofesse", 2000, 70000),
    33371: ("Foudroyeur", 2000, 70000),
    33372: ("Abreuvoir", 2000, 70000),
    33373: ("Mangeoire", 2000, 70000),
    33378: ("Baffeur", 3000, 70000),
    33379: ("Caresseur", 3000, 70000),
    33380: ("Dragofesse", 3000, 70000),
    33381: ("Foudroyeur", 3000, 70000),
    33382: ("Abreuvoir", 3000, 70000),
    33383: ("Mangeoire", 3000, 70000),
    33389: ("Baffeur", 4000, 70000),
    33390: ("Caresseur", 4000, 70000),
    33391: ("Dragofesse", 4000, 70000),
    33392: ("Foudroyeur", 4000, 70000),
    33393: ("Abreuvoir", 4000, 70000),
    33394: ("Mangeoire", 4000, 70000),
    33399: ("Baffeur", 5000, 70000),
    33400: ("Caresseur", 5000, 70000),
    33401: ("Dragofesse", 5000, 70000),
    33402: ("Foudroyeur", 5000, 70000),
    33403: ("Abreuvoir", 5000, 70000),
    33404: ("Mangeoire", 5000, 70000),
    33410: ("Baffeur", 1000, 90000),
    33411: ("Caresseur", 1000, 90000),
    33412: ("Dragofesse", 1000, 90000),
    33413: ("Foudroyeur", 1000, 90000),
    33414: ("Abreuvoir", 1000, 90000),
    33415: ("Mangeoire", 1000, 90000),
    33420: ("Baffeur", 2000, 90000),
    33421: ("Caresseur", 2000, 90000),
    33422: ("Dragofesse", 2000, 90000),
    33423: ("Foudroyeur", 2000, 90000),
    33424: ("Abreuvoir", 2000, 90000),
    33425: ("Mangeoire", 2000, 90000),
    33431: ("Baffeur", 3000, 90000),
    33432: ("Caresseur", 3000, 90000),
    33433: ("Dragofesse", 3000, 90000),
    33434: ("Foudroyeur", 3000, 90000),
    33435: ("Abreuvoir", 3000, 90000),
    33436: ("Mangeoire", 3000, 90000),
    33441: ("Baffeur", 4000, 90000),
    33442: ("Caresseur", 4000, 90000),
    33443: ("Dragofesse", 4000, 90000),
    33444: ("Foudroyeur", 4000, 90000),
    33445: ("Abreuvoir", 4000, 90000),
    33446: ("Mangeoire", 4000, 90000),
    33452: ("Baffeur", 5000, 90000),
    33453: ("Caresseur", 5000, 90000),
    33454: ("Dragofesse", 5000, 90000),
    33455: ("Foudroyeur", 5000, 90000),
    33456: ("Abreuvoir", 5000, 90000),
    33457: ("Mangeoire", 5000, 90000),
    33462: ("Baffeur", 1000, 100000),
    33463: ("Caresseur", 1000, 100000),
    33464: ("Dragofesse", 1000, 100000),
    33465: ("Foudroyeur", 1000, 100000),
    33466: ("Abreuvoir", 1000, 100000),
    33467: ("Mangeoire", 1000, 100000),
    33471: ("Baffeur", 2000, 100000),
    33472: ("Caresseur", 2000, 100000),
    33473: ("Dragofesse", 2000, 100000),
    33474: ("Foudroyeur", 2000, 100000),
    33475: ("Abreuvoir", 2000, 100000),
    33476: ("Mangeoire", 2000, 100000),
    33480: ("Baffeur", 3000, 100000),
    33481: ("Caresseur", 3000, 100000),
    33482: ("Dragofesse", 3000, 100000),
    33483: ("Foudroyeur", 3000, 100000),
    33484: ("Abreuvoir", 3000, 100000),
    33485: ("Mangeoire", 3000, 100000),
    33489: ("Baffeur", 4000, 100000),
    33490: ("Caresseur", 4000, 100000),
    33491: ("Dragofesse", 4000, 100000),
    33492: ("Foudroyeur", 4000, 100000),
    33493: ("Abreuvoir", 4000, 100000),
    33494: ("Mangeoire", 4000, 100000),
    33498: ("Baffeur", 5000, 100000),
    33499: ("Caresseur", 5000, 100000),
    33500: ("Dragofesse", 5000, 100000),
    33501: ("Foudroyeur", 5000, 100000),
    33502: ("Abreuvoir", 5000, 100000),
    33503: ("Mangeoire", 5000, 100000),
}

# Ordre d'affichage des jauges.
GAUGES = ["Caresseur", "Baffeur", "Mangeoire", "Abreuvoir", "Dragofesse", "Foudroyeur"]

# Tranches [début, fin] déduites des plafonds distincts : un plafond P couvre
# toute tranche dont la fin <= P. (0-40k : tout ; 40k-70k : plafond >= 70k ; ...)
_CAP_THRESHOLDS = sorted({cap for _g, _f, cap in ENCLOS_FUELS.values()})  # [40000,70000,90000,100000]
SEGMENTS = list(zip([0] + _CAP_THRESHOLDS[:-1], _CAP_THRESHOLDS))


@dataclass
class SegmentPlan:
    from_value: int
    to_value: int
    item_id: int | None = None
    fill: int | None = None
    cap: int | None = None
    unit_price: float | None = None
    source: str | None = None      # 'buy' (HDV) ou 'craft' selon le moins cher
    units: int | None = None       # nombre de carburants (entier) pour la tranche
    cost: float | None = None      # coût de la tranche


@dataclass
class GaugePlan:
    gauge: str
    total_cost: float | None = None
    fillable: bool = True
    segments: list[SegmentPlan] = field(default_factory=list)


def _fuel_reference_price(conn: sqlite3.Connection, server_id: int, item_id: int) -> tuple[float | None, str | None]:
    """Prix de référence d'un carburant = le MOINS CHER entre l'ACHAT à l'HDV
    (get_unit_price) et le COÛT DE CRAFT (si la recette existe ET que tous ses
    ingrédients ont un prix relevé). Renvoie (prix, source) avec source
    'buy'|'craft' pour savoir directement quoi faire, ou (None, None) si aucun
    des deux n'est chiffrable. À égalité de prix, on privilégie l'achat (moins
    d'effort)."""
    candidates: list[tuple[float, str]] = []
    hdv = get_unit_price(conn, item_id, server_id)
    if hdv is not None:
        candidates.append((hdv, "buy"))
    recipe = conn.execute("SELECT id FROM recipes WHERE result_item_id = ? LIMIT 1", (item_id,)).fetchone()
    if recipe is not None:
        cost = compute_craft_cost(conn, recipe[0], server_id)
        if not cost.missing_ingredients:  # tous les ingrédients ont un prix
            candidates.append((cost.total_cost, "craft"))
    if not candidates:
        return None, None
    return min(candidates, key=lambda c: c[0])  # à égalité, 'buy' (listé en 1er) gagne


def _priced_fuels(conn: sqlite3.Connection, server_id: int, gauge: str) -> list[dict]:
    """Carburants de cette jauge avec un prix de référence connu (achat OU
    craft, le moins cher), et leur coût par point (prix / valeur ajoutée)."""
    out = []
    for item_id, (g, fill, cap) in ENCLOS_FUELS.items():
        if g != gauge:
            continue
        unit_price, source = _fuel_reference_price(conn, server_id, item_id)
        if unit_price is None:
            continue
        out.append(
            {"item_id": item_id, "fill": fill, "cap": cap, "unit_price": unit_price,
             "source": source, "cost_per_point": unit_price / fill}
        )
    return out


def _clamp_range(range_from: int, range_to: int) -> tuple[int, int]:
    """Borne l'intervalle demandé dans [0, 100 000] et garantit from < to
    (échange si inversé, garde-fou même si l'appelant a déjà validé)."""
    lo = max(0, min(GAUGE_MAX, int(range_from)))
    hi = max(0, min(GAUGE_MAX, int(range_to)))
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def cheapest_gauge_fill(
    conn: sqlite3.Connection, server_id: int, gauge: str,
    range_from: int = 0, range_to: int = GAUGE_MAX,
) -> GaugePlan:
    """Plan le moins cher pour remplir une jauge sur l'intervalle [range_from,
    range_to] (par défaut 0 → 100 000) : pour chaque tranche de plafond, le
    carburant au meilleur prix par point parmi ceux dont le plafond couvre la
    tranche. Chaque tranche de plafond est RESTREINTE à l'intervalle demandé —
    une tranche entièrement hors intervalle est simplement ignorée (elle ne rend
    donc pas la jauge non chiffrable). Une tranche DANS l'intervalle mais sans
    carburant pricé rend la jauge non entièrement chiffrable (fillable=False)."""
    range_from, range_to = _clamp_range(range_from, range_to)
    priced = _priced_fuels(conn, server_id, gauge)
    plan = GaugePlan(gauge=gauge)
    total = 0.0
    for lo, hi in SEGMENTS:
        # portion de cette tranche de plafond qui recoupe l'intervalle demandé
        clo, chi = max(lo, range_from), min(hi, range_to)
        if chi <= clo:
            continue  # tranche hors de l'intervalle choisi
        # L'éligibilité dépend du plafond de la BANDE (hi), pas de la portion
        # recoupée : pour remplir dans la bande lo→hi il faut un plafond >= hi.
        eligible = [f for f in priced if f["cap"] >= hi]
        if not eligible:
            plan.segments.append(SegmentPlan(from_value=clo, to_value=chi))
            plan.fillable = False
            continue
        best = min(eligible, key=lambda f: f["cost_per_point"])
        size = chi - clo
        units = math.ceil(size / best["fill"])
        cost = size * best["cost_per_point"]  # coût exact au prorata des points
        plan.segments.append(
            SegmentPlan(
                from_value=clo, to_value=chi, item_id=best["item_id"], fill=best["fill"],
                cap=best["cap"], unit_price=best["unit_price"], source=best["source"],
                units=units, cost=cost,
            )
        )
        total += cost
    plan.total_cost = total if plan.fillable else None
    return plan


def rank_enclos_gauges(
    conn: sqlite3.Connection, server_id: int,
    range_from: int = 0, range_to: int = GAUGE_MAX,
) -> list[GaugePlan]:
    """Plan de remplissage le moins cher des 6 jauges sur l'intervalle demandé."""
    return [cheapest_gauge_fill(conn, server_id, g, range_from, range_to) for g in GAUGES]
