"""Calcul de rentabilité de la montée de niveau d'un familier : compare le
coût en ressources pour le monter au niveau max à la plus-value de revente
(prix au niveau max moins prix à bas niveau), après taxe HDV et marge de
sécurité — même logique que pricing/profitability.py mais appliquée aux
familiers plutôt qu'aux équipements craftés.

Bénéfice mensuel estimé (quantity_sold_30j/score) : le panneau "Cours du
marché" n'est PAS réservé aux ressources — il existe pour n'importe quelle
catégorie d'objet, familiers compris (identify_item() sans filtre de
catégorie dans market_history_handler.py, cf. hdvcreature3.png/
hdvequipement3.png dans image/). get_monthly_quantity_sold() (profitability.py)
est donc directement réutilisable ici sans changement — même mécanisme que
pour les crafts (compute_recipe_margin), classé par le même ranking_key().

XP requise pour monter un familier, niveau par niveau : table RÉELLE
(cumulative, XP totale pour ATTEINDRE chaque niveau depuis 0) fournie par
l'utilisateur — remplace l'ancienne approximation ("394 croquettes
enrichies à 500 XP" sourcée de dofuspourlesnoobs.com, répartie ensuite par
interpolation LINÉAIRE entre deux niveaux observés). Cette interpolation
était une SOUS-ESTIMATION majeure vérifiée sur des cas réels : pour l'écart
80→100 (le plus courant, cf. mécanisme de confirmation niveau 0/100 dans
familiar_hdv_handler.py — en pratique low_level/high_level valent presque
toujours 0/100), l'ancienne formule donnait xp_span=39400 quand la vraie
valeur (table réelle) est 149773 — un facteur ~3,8× qui faisait apparaître
des familiers comme deux fois plus rentables qu'ils ne le sont vraiment.
`PET_CUMULATIVE_XP_BY_LEVEL[N] - PET_CUMULATIVE_XP_BY_LEVEL[M]` donne
maintenant le coût XP EXACT entre deux niveaux quelconques, plus une
approximation sur une courbe agrégée.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from profitability import get_monthly_quantity_sold, get_unit_price, net_sale_factor, ranking_key  # noqa: E402

# XP cumulée nécessaire pour ATTEINDRE chaque niveau depuis 0 (niveau 0 =
# familier fraîchement acheté, jamais nourri = 0 XP par construction — la
# table fournie par l'utilisateur commence au niveau 1, cf. docstring du
# module). Vérifié auto-cohérent (cumulative[N] - cumulative[N-1] == le
# coût de montée annoncé pour le niveau N, sur les 100 niveaux) avant
# intégration — voir aussi test_pet_profitability.py.
PET_CUMULATIVE_XP_BY_LEVEL = {
    0: 0,
    1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8, 7: 10, 8: 12, 9: 14, 10: 16,
    11: 19, 12: 22, 13: 25, 14: 29, 15: 33, 16: 38, 17: 43, 18: 49, 19: 56, 20: 63,
    21: 71, 22: 80, 23: 90, 24: 102, 25: 115, 26: 129, 27: 145, 28: 163, 29: 183, 30: 205,
    31: 229, 32: 256, 33: 286, 34: 319, 35: 356, 36: 397, 37: 442, 38: 492, 39: 547, 40: 608,
    41: 676, 42: 751, 43: 833, 44: 924, 45: 1024, 46: 1135, 47: 1257, 48: 1392, 49: 1540, 50: 1703,
    51: 1883, 52: 2081, 53: 2299, 54: 2538, 55: 2801, 56: 3092, 57: 3408, 58: 3757, 59: 4141, 60: 4562,
    61: 5024, 62: 5531, 63: 6088, 64: 6699, 65: 7369, 66: 8103, 67: 8908, 68: 9792, 69: 10756, 70: 11815,
    71: 12975, 72: 14245, 73: 15635, 74: 17157, 75: 18823, 76: 20648, 77: 22641, 78: 24825, 79: 27209, 80: 29819,
    81: 32673, 82: 35793, 83: 39205, 84: 42931, 85: 47005, 86: 51455, 87: 56315, 88: 61628, 89: 67427, 90: 73764,
    91: 80681, 92: 88233, 93: 96476, 94: 105478, 95: 115299, 96: 126021, 97: 137718, 98: 150481, 99: 164404, 100: 179592,
}


def cheapest_feed_option(conn: sqlite3.Connection, server_id: int) -> tuple[int, int, float] | None:
    """Ressource connue (pet_feed_xp) au meilleur ratio kamas/XP, parmi
    celles dont on a un prix récent. C'est la ressource à utiliser pour
    monter un familier au moindre coût."""
    best: tuple[int, int, float] | None = None
    for resource_item_id, xp_value in conn.execute(
        "SELECT resource_item_id, xp_value FROM pet_feed_xp WHERE xp_value > 0"
    ):
        unit_price = get_unit_price(conn, resource_item_id, server_id)
        if unit_price is None:
            continue
        cost_per_xp = unit_price / xp_value
        if best is None or cost_per_xp < best[2]:
            best = (resource_item_id, xp_value, cost_per_xp)
    return best


@dataclass
class PetMargin:
    species_item_id: int
    species_name: str
    price_low_level: float
    price_max_level: float
    feed_cost: float
    margin: float | None
    quantity_sold_30j: int | None = None
    score: float | None = None


def compute_pet_margin(
    conn: sqlite3.Connection, species_item_id: int, server_id: int, net_factor: float | None = None
) -> PetMargin | None:
    species_name = conn.execute(
        "SELECT name FROM items WHERE id = ?", (species_item_id,)
    ).fetchone()[0]

    rows = conn.execute(
        """
        SELECT pet_level, price_kamas FROM price_snapshots
        WHERE item_id = ? AND server_id = ? AND pet_level IS NOT NULL
        ORDER BY captured_at DESC
        """,
        (species_item_id, server_id),
    ).fetchall()
    if len(rows) < 2:
        return None

    # dernier prix connu au plus bas et au plus haut niveau observés
    by_level: dict[int, float] = {}
    for pet_level, price_kamas in rows:
        by_level.setdefault(pet_level, price_kamas)
    low_level, high_level = min(by_level), max(by_level)
    if low_level == high_level:
        return None
    price_low, price_high = by_level[low_level], by_level[high_level]

    cheapest = cheapest_feed_option(conn, server_id)
    if cheapest is None:
        feed_cost = None
    else:
        _, _, cost_per_xp = cheapest
        xp_span = PET_CUMULATIVE_XP_BY_LEVEL[high_level] - PET_CUMULATIVE_XP_BY_LEVEL[low_level]
        feed_cost = xp_span * cost_per_xp

    if net_factor is None:
        net_factor = net_sale_factor(conn)
    gain_net = (price_high - price_low) * net_factor
    margin = gain_net - feed_cost if feed_cost is not None else None

    quantity_sold_30j = get_monthly_quantity_sold(conn, species_item_id, server_id)
    # Bénéfice mensuel total estimé, même principe que compute_recipe_margin :
    # un familier peu rentable à monter mais qui se vend beaucoup doit
    # surclasser un familier très rentable mais invendable.
    score = margin * quantity_sold_30j if margin is not None and quantity_sold_30j is not None else None

    return PetMargin(
        species_item_id=species_item_id,
        species_name=species_name,
        price_low_level=price_low,
        price_max_level=price_high,
        feed_cost=feed_cost or 0.0,
        margin=margin,
        quantity_sold_30j=quantity_sold_30j,
        score=score,
    )


def rank_profitable_pets(conn: sqlite3.Connection, server_id: int) -> list[PetMargin]:
    """Classe tous les familiers pour lesquels on a au moins deux relevés de
    prix à des niveaux différents, par bénéfice mensuel estimé décroissant
    (marge × volume vendu/mois, cf. ranking_key) — même logique que
    rank_profitable_recipes()."""
    species_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT item_id FROM price_snapshots WHERE server_id = ? AND pet_level IS NOT NULL",
            (server_id,),
        )
    ]
    net_factor = net_sale_factor(conn)
    results = [compute_pet_margin(conn, sid, server_id, net_factor) for sid in species_ids]
    calculable = [r for r in results if r is not None and r.margin is not None]
    calculable.sort(key=ranking_key, reverse=True)
    return calculable
