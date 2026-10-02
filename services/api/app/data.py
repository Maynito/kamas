"""Couche de données de la Price API — extraite du monolithe src/web/app.py
(fonctions de LECTURE pures : prennent une connexion + un server_id, renvoient
des dicts/dataclasses JSON-sérialisables). Aucune dépendance FastAPI ni vision.
L'API en est désormais propriétaire ; le front (services/front) l'atteint via
HTTP, il n'importe plus rien d'ici.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from profitability import (
    build_price_cache,
    compute_craft_cost,
    compute_recipe_margin,
    get_average_sale_price,
    get_config,
    get_unit_price,
    net_sale_factor,
    ranking_key,
)
from pet_profitability import (
    PET_CUMULATIVE_XP_BY_LEVEL,
    compute_pet_margin,
    rank_profitable_pets,
)
from enclos_profitability import GAUGE_MAX, _clamp_range, rank_enclos_gauges

CATEGORIES = ("ressource", "familier", "equipement")
PET_MAX_LEVEL = 100
PET_XP_TO_MAX = PET_CUMULATIVE_XP_BY_LEVEL[PET_MAX_LEVEL]  # XP totale 0 -> 100


def _active_server_id(conn) -> int:
    """Serveur actif choisi par l'utilisateur — catégories nommées que
    l'utilisateur crée lui-même (l'utilisateur joue sur 2 serveurs, ne
    voulait pas d'un multi-serveur figé), remplace l'ancienne constante
    SERVER_ID=1. Lu depuis app_config à CHAQUE requête (pas mis en cache) :
    /servers/active doit prendre effet immédiatement sans redémarrer le
    process. Retombe sur le premier serveur existant si la config pointe
    vers un serveur supprimé entretemps — jamais une valeur absente de
    `servers`, qui casserait toutes les requêtes FK-dépendantes en aval."""
    row = conn.execute("SELECT value FROM app_config WHERE key = 'active_server_id'").fetchone()
    if row is not None:
        candidate = int(row[0])
        if conn.execute("SELECT 1 FROM servers WHERE id = ?", (candidate,)).fetchone():
            return candidate
    fallback = conn.execute("SELECT id FROM servers ORDER BY id LIMIT 1").fetchone()
    if fallback is None:
        raise RuntimeError("Aucun serveur en base — lancer `python tasks.py sync` d'abord.")
    return fallback[0]


def _category_filter_sql(category: str, alias: str = "i") -> str:
    """"Équipement" n'est pas un super_type_name DofusDB unique (c'est un
    fourre-tout côté jeu : Arme, Bottes, Chapeau, Amulette, Cape, etc.) —
    on le définit donc par exclusion plutôt que par une liste explicite."""
    if category == "ressource":
        return f"{alias}.super_type_name = 'Ressource'"
    if category == "familier":
        return f"{alias}.super_type_name = 'Familier'"
    return f"{alias}.super_type_name NOT IN ('Ressource', 'Familier')"


def _category_for_super_type(super_type_name: str) -> str:
    """Sens inverse de _category_filter_sql() : à partir d'un super_type_name
    DofusDB déjà connu (pas besoin de requêter), donne la catégorie web
    correspondante — même définition par exclusion pour "équipement". Sert à
    construire un lien /history?category=... correct depuis une liste
    d'objets de catégories mélangées (cf. _recent_captures)."""
    if super_type_name == "Ressource":
        return "ressource"
    if super_type_name == "Familier":
        return "familier"
    return "equipement"


# Cache mémoire court des marges (le calcul parcourt ~4800 recettes avec des
# requêtes imbriquées : ~0,4 s). La page Crafts se rafraîchit toutes les 2 s et
# on y revient souvent — sans cache on recalcule tout à chaque fois. Les marges
# ne dépendent que des prix (price_snapshots) et de la config (marge/taxe), qui
# bougent lentement : un TTL de quelques secondes est transparent. Les pastilles
# "déjà engagé" (atelier/suivis), elles, restent recalculées à CHAQUE appel
# (hors cache) pour rester instantanées. Process unique en dev ; pour du
# multi-worker/prod on brancherait un cache partagé (Redis) — limite assumée.
_CRAFT_MARGINS_CACHE: dict = {}   # (server_id, job_id, level_min, level_max) -> (ts_monotonic, [RecipeMargin])
_CRAFT_MARGINS_TTL = 5.0


def _all_recipe_margins(conn, server_id: int, job_id=None, level_min=None, level_max=None):
    """Marge de toutes les recettes, calculables ou non — contrairement à
    rank_profitable_recipes qui n'expose que les calculables, on en a besoin
    ici pour aussi lister celles qui attendent un prix de ressource. Résultat
    mis en cache quelques secondes (cf. _CRAFT_MARGINS_TTL).

    Filtres optionnels appliqués DIRECTEMENT en SQL (métier via recipes.job_id,
    niveau via items.level de l'objet crafté) : on ne calcule alors la marge que
    des recettes retenues — donc une vue filtrée est plus RAPIDE, pas plus lente.
    Le filtre de niveau exclut naturellement les objets sans niveau connu
    (comparaison SQL fausse sur NULL), ce qui est le comportement voulu."""
    cache_key = (server_id, job_id, level_min, level_max)
    now = time.monotonic()
    cached = _CRAFT_MARGINS_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _CRAFT_MARGINS_TTL:
        return cached[1]

    where, params = [], []
    if job_id is not None:
        where.append("r.job_id = ?")
        params.append(job_id)
    if level_min is not None:
        where.append("i.level >= ?")
        params.append(level_min)
    if level_max is not None:
        where.append("i.level <= ?")
        params.append(level_max)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    net_factor = net_sale_factor(conn)
    recipe_ids = [
        row[0]
        for row in conn.execute(
            f"SELECT r.id FROM recipes r JOIN items i ON i.id = r.result_item_id {clause}", params
        )
    ]
    # Prix pré-chargés en LOT (3 requêtes) plutôt qu'une requête par ingrédient/
    # objet sur ~4800 recettes — cf. build_price_cache. C'est ce qui fait passer
    # le calcul "à froid" (cache TTL expiré) de ~580 ms à quelques dizaines de ms.
    price_cache = build_price_cache(conn, server_id)
    result = [compute_recipe_margin(conn, rid, server_id, net_factor, price_cache) for rid in recipe_ids]
    _CRAFT_MARGINS_CACHE[cache_key] = (now, result)
    if len(_CRAFT_MARGINS_CACHE) > 32:  # borne : purge des entrées périmées
        for k in [k for k, v in _CRAFT_MARGINS_CACHE.items() if now - v[0] >= _CRAFT_MARGINS_TTL]:
            _CRAFT_MARGINS_CACHE.pop(k, None)
    return result


def _craft_jobs(conn):
    """Métiers ayant au moins une recette (pour le sélecteur de /crafts)."""
    rows = conn.execute(
        "SELECT DISTINCT j.id, j.name FROM jobs j JOIN recipes r ON r.job_id = j.id "
        "WHERE j.name IS NOT NULL ORDER BY j.name"
    ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


def _crafts_recipe_margins(conn, server_id: int, query: str = "", job_id=None, level_min=None, level_max=None):
    """Répartit TOUTES les recettes en calculables / incomplètes en un seul
    passage (une recette qui a tous ses ingrédients pricés mais dont
    l'ÉQUIPEMENT résultat lui-même n'a jamais été vu en vente — donc sans
    marge calculable — disparaissait auparavant complètement : ni dans les
    calculables (marge inconnue), ni dans les incomplètes (qui ne
    regardaient que missing_ingredients, pas l'absence de prix de vente).
    "Incomplète" couvre maintenant les deux cas.

    `query` filtre par nom d'équipement (sous-chaîne, insensible à la casse)
    avant tri/troncature, pour que la recherche manuelle porte sur
    l'ensemble des recettes et pas seulement sur le top 20 par défaut."""
    all_margins = _all_recipe_margins(conn, server_id, job_id, level_min, level_max)
    if query:
        needle = query.strip().lower()
        all_margins = [m for m in all_margins if needle in m.result_name.lower()]

    # "Calculable" = COÛT DE CRAFT connu (tous les ingrédients ont un prix),
    # que le prix de VENTE de l'objet crafté soit relevé ou non. On voit ainsi
    # le coût d'un craft rien qu'en relevant le prix des ressources, sans devoir
    # aller relever le prix de l'équipement à l'HDV. Ceux dont la marge est
    # connue (prix de vente relevé) passent devant, triés par bénéfice ; ceux
    # sans prix de vente (marge inconnue) suivent, triés par coût de craft.
    priced = [m for m in all_margins if not m.missing_ingredients]
    with_margin = [m for m in priced if m.margin is not None]
    without_margin = [m for m in priced if m.margin is None]
    with_margin.sort(key=ranking_key, reverse=True)
    without_margin.sort(key=lambda m: m.craft_cost)
    calculable = with_margin + without_margin

    incomplete = [m for m in all_margins if m.missing_ingredients]
    incomplete.sort(key=lambda m: len(m.missing_ingredients))
    # Le plafond de 20 (anti-déluge sur ~4000 recettes) ne s'applique qu'à la
    # vue non filtrée : dès qu'un filtre restreint l'ensemble (recherche, métier
    # ou niveau), on montre tout ce qui correspond.
    if not query and job_id is None and level_min is None and level_max is None:
        incomplete = incomplete[:20]

    return calculable, incomplete


def _incomplete_reason_rows(incomplete):
    """Aplati chaque recette incomplète en lignes "raison" pour l'affichage —
    UNIQUEMENT deux causes réelles existent dans le modèle de calcul
    (profitability.py), pas de 3e état "en attente" inventé : soit un
    ingrédient précis n'a AUCUN prix référencé (missing_ingredients, cf.
    compute_craft_cost), soit tous les ingrédients sont pricés mais l'objet
    CRAFTÉ lui-même n'a jamais de prix de vente référencé (margin is None
    avec missing_ingredients vide, cf. compute_recipe_margin — déjà le badge
    "Prix de vente inconnu" affiché séparément avant cette refonte)."""
    rows = []
    for m in incomplete:
        if m.missing_ingredients:
            for item_id, name, qty in m.missing_ingredients:
                rows.append(
                    {
                        "result_item_id": m.result_item_id,
                        "result_name": m.result_name,
                        "resource_item_id": item_id,
                        "resource_name": name,
                        "quantity": qty,
                        "reason": "Prix non référencé pour cette ressource",
                        "reason_kind": "missing_ingredient",
                    }
                )
        else:
            rows.append(
                {
                    "result_item_id": m.result_item_id,
                    "result_name": m.result_name,
                    "resource_item_id": None,
                    "resource_name": None,
                    "quantity": None,
                    "reason": "Prix de vente de l'objet crafté non référencé",
                    "reason_kind": "missing_sale_price",
                }
            )
    return rows


def _ingredient_display_info(conn, item_ids, server_id: int):
    """Niveau/type + détail des prix par lot pour un ensemble d'ingrédients —
    sert uniquement à remplir la pop-up au survol des pastilles d'ingrédients
    de /crafts (recettes incomplètes). Volontairement séparé de
    IngredientCost (profitability.py), qui reste concentré sur le calcul de
    coût (prix unitaire seul) et n'a pas à connaître ce détail d'affichage.
    Une seule requête groupée par table plutôt qu'un appel _lot_prices() par
    ingrédient : plusieurs recettes partagent souvent les mêmes ressources
    courantes, pas la peine de reredemander leurs prix à chaque occurrence."""
    item_ids = sorted(item_ids)
    if not item_ids:
        return {}
    placeholders = ",".join("?" * len(item_ids))
    info = {
        item_id: {"level": None, "type_name": None, "lot_prices": {1: None, 10: None, 100: None, 1000: None}}
        for item_id in item_ids
    }
    for item_id, level, type_name in conn.execute(
        f"SELECT id, level, type_name FROM items WHERE id IN ({placeholders})", item_ids
    ):
        info[item_id]["level"] = level
        info[item_id]["type_name"] = type_name
    for item_id, lot_size, price in conn.execute(
        f"""
        SELECT item_id, lot_size, price_kamas FROM price_snapshots
        WHERE item_id IN ({placeholders}) AND server_id = ? AND pet_level IS NULL
        ORDER BY captured_at DESC
        """,
        (*item_ids, server_id),
    ):
        if info[item_id]["lot_prices"][lot_size] is None:
            info[item_id]["lot_prices"][lot_size] = price  # 1re rencontre = plus récente (ORDER BY DESC)
    return info


def _pending_pets(conn, server_id: int, limit: int = 20):
    """Familiers pas encore calculables (compute_pet_margin renvoie None) —
    la RAISON exacte se déduit du nombre de niveaux distincts observés,
    seul signal qui distingue les deux causes réelles de ce module (cf.
    pet_profitability.compute_pet_margin) : moins de 2 niveaux → pas assez
    de relevés pour comparer un prix bas/haut ; 2+ niveaux mais quand même
    None → c'est forcément la branche feed_cost (aucune ressource de
    pet_feed_xp n'a de prix récent), la seule autre façon d'obtenir None une
    fois les niveaux réunis. Pas de raison inventée au-delà de ces deux cas."""
    species_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT item_id FROM price_snapshots WHERE server_id = ? AND pet_level IS NOT NULL",
            (server_id,),
        )
    ]
    pending = []
    for item_id in species_ids:
        if compute_pet_margin(conn, item_id, server_id) is not None:
            continue
        levels = sorted(
            {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT pet_level FROM price_snapshots WHERE item_id = ? AND server_id = ?",
                    (item_id, server_id),
                )
            }
        )
        name = conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()[0]
        reason = (
            f"Un seul niveau observé (niveau {levels[0]}) — il en faut deux pour estimer une marge"
            if len(levels) < 2
            else "Coût de montée en niveau inconnu — aucune ressource de nourrissage n'a de prix récent"
        )
        pending.append({"name": name, "item_id": item_id, "levels": levels, "reason": reason})
        if len(pending) >= limit:
            break
    return pending


def _recent_captures(conn, server_id: int, limit: int = 12):
    rows = conn.execute(
        """
        SELECT i.id, i.name, i.super_type_name, p.lot_size, p.pet_level, p.price_kamas,
               p.source, p.confidence, p.captured_at
        FROM price_snapshots p JOIN items i ON i.id = p.item_id
        WHERE p.server_id = ?
        ORDER BY p.captured_at DESC
        LIMIT ?
        """,
        (server_id, limit),
    ).fetchall()
    out = []
    for item_id, name, super_type_name, lot_size, pet_level, price, source, confidence, captured_at in rows:
        label = f"{name} · niv. {pet_level}" if pet_level is not None else name
        out.append(
            {
                "item_id": item_id,
                "category": _category_for_super_type(super_type_name),
                "label": label,
                "price": price,
                "confidence": confidence,
                "high_confidence": confidence is None or confidence >= 0.6,
                "captured_at": captured_at,
            }
        )
    return out


def _resource_prices(conn, server_id: int, category: str = "ressource"):
    """Dernier prix connu par lot (1/10/100/1000) pour chaque objet déjà
    capturé dans la catégorie choisie — vue "tous les objets" de l'onglet
    Prix, triée par dernière mise à jour décroissante (pratique pour suivre
    le watcher en direct : l'objet qui vient d'être capturé remonte en
    premier, pas besoin de retrier/rafraîchir à la main). Les familiers
    n'ont pas de prix par lot (chaque annonce est individuelle, cf.
    familiar_hdv_handler.py) : cette catégorie passe par _familiar_level_prices()."""
    if category == "familier":
        return _familiar_level_prices(conn, server_id)
    rows = conn.execute(
        f"""
        SELECT i.id, i.name, p.lot_size, p.price_kamas, p.captured_at
        FROM price_snapshots p JOIN items i ON i.id = p.item_id
        WHERE p.server_id = ? AND p.lot_size IN (1, 10, 100, 1000) AND p.pet_level IS NULL
        AND {_category_filter_sql(category)}
        ORDER BY p.captured_at DESC
        """,
        (server_id,),
    ).fetchall()
    by_item: dict[int, dict] = {}
    for item_id, name, lot_size, price, captured_at in rows:
        entry = by_item.setdefault(
            item_id, {"item_id": item_id, "name": name, "prices": {}, "captured_at": captured_at}
        )
        entry["prices"].setdefault(lot_size, price)  # 1re rencontre = plus récente (ORDER BY DESC)
    resources = sorted(by_item.values(), key=lambda e: e["captured_at"], reverse=True)
    if category == "ressource":
        _attach_xp_info(conn, resources, server_id)
    return resources


def _attach_xp_info(conn, resources: list[dict], server_id: int) -> None:
    """Ajoute xp_value (XP totale donnée au nourrissage d'un familier) et
    price_per_xp (prix au kamas d'1 XP, = prix unitaire le plus avantageux —
    get_unit_price() — divisé par l'XP) à chaque entrée, en place. XP
    seedée uniquement à la main (aucune source fiable côté DofusDB, cf.
    pet_feed_xp et /history/xp) : `None` tant que l'utilisateur ne l'a pas
    renseignée pour cette ressource."""
    xp_by_item = dict(conn.execute("SELECT resource_item_id, xp_value FROM pet_feed_xp").fetchall())
    for entry in resources:
        xp_value = xp_by_item.get(entry["item_id"])
        entry["xp_value"] = xp_value
        unit_price = get_unit_price(conn, entry["item_id"], server_id) if xp_value else None
        entry["price_per_xp"] = (unit_price / xp_value) if unit_price is not None else None


def _familiar_level_prices(conn, server_id: int):
    """Équivalent de _resource_prices() pour la catégorie "familier" :
    dernier prix connu aux niveaux 0 (tel que capturé) et 100 (maximal) par
    espèce, plutôt que par lot — cf. familiar_hdv_handler.py."""
    rows = conn.execute(
        """
        SELECT i.id, i.name, p.pet_level, p.price_kamas, p.captured_at
        FROM price_snapshots p JOIN items i ON i.id = p.item_id
        WHERE p.server_id = ? AND p.pet_level IN (0, 100)
        ORDER BY p.captured_at DESC
        """,
        (server_id,),
    ).fetchall()
    by_item: dict[int, dict] = {}
    for item_id, name, pet_level, price, captured_at in rows:
        entry = by_item.setdefault(
            item_id, {"item_id": item_id, "name": name, "prices": {}, "captured_at": captured_at}
        )
        entry["prices"].setdefault(pet_level, price)
    return sorted(by_item.values(), key=lambda e: e["captured_at"], reverse=True)


def _lot_prices(conn, item_id: int, server_id: int) -> dict[int, int | None]:
    prices = {}
    for lot in (1, 10, 100, 1000):
        row = conn.execute(
            "SELECT price_kamas FROM price_snapshots WHERE item_id=? AND server_id=? AND lot_size=? "
            "AND pet_level IS NULL ORDER BY captured_at DESC LIMIT 1",
            (item_id, server_id, lot),
        ).fetchone()
        prices[lot] = row[0] if row else None
    return prices


def _pet_level_prices(conn, item_id: int, server_id: int) -> dict[int, int | None]:
    prices = {}
    for level in (0, 100):
        row = conn.execute(
            "SELECT price_kamas FROM price_snapshots WHERE item_id=? AND server_id=? AND pet_level=? "
            "ORDER BY captured_at DESC LIMIT 1",
            (item_id, server_id, level),
        ).fetchone()
        prices[level] = row[0] if row else None
    return prices


def _dashboard_data(conn, server_id: int):
    # rank_profitable_recipes()/rank_profitable_pets() renvoient tout ce qui
    # est CALCULABLE (marge connue, positive ou négative) — correct pour les
    # pages /crafts et /pets ("recette calculable"), mais le dashboard promet
    # "Crafts/Familiers RENTABLES" : sans ce filtre sur margin > 0, un objet
    # à marge négative gonflait le compteur et pouvait apparaître dans le
    # top 3 "rentable" — régression réelle constatée sur la base de
    # l'utilisateur (6 familiers calculables, 0 réellement rentables en
    # marge positive à ce moment-là).
    # Réutilise _all_recipe_margins (BATCHÉ + CACHÉ, et MÊME clé de cache que
    # /crafts → les deux pages partagent le calcul) plutôt que
    # rank_profitable_recipes, qui refaisait un N+1 non caché (~4800 marges
    # recalculées à CHAQUE rafraîchissement 2 s du dashboard, ~580 ms). Le filtre
    # calculable + le tri reproduisent exactement rank_profitable_recipes.
    craft_margins = _all_recipe_margins(conn, server_id)
    calculable_crafts = sorted(
        (c for c in craft_margins if c.margin is not None and not c.missing_ingredients),
        key=ranking_key, reverse=True,
    )
    profitable_crafts = [c for c in calculable_crafts if c.margin > 0]
    profitable_pets = [p for p in rank_profitable_pets(conn, server_id) if p.margin > 0]
    recent = _recent_captures(conn, server_id, limit=6)
    # Section "Suivis" du dashboard (remplace l'ancienne "Activité récente") :
    # aperçu des positions suivies, en cours d'abord puis vendues récentes —
    # mêmes données que /suivis, simplement tronquées. `recent` reste utilisé
    # pour la tuile "Dernière capture".
    trades = _tracked_trades_data(conn, server_id)
    suivis = (trades["holding"] + trades["sold"])[:6]
    return {
        "crafts": profitable_crafts[:3],
        "pets": profitable_pets[:3],
        "recent": recent,
        "suivis": suivis,
        "craft_count": len(profitable_crafts),
        "pet_count": len(profitable_pets),
    }


def _crafts_data(conn, server_id: int, query: str = "", job_id=None, level_min=None, level_max=None):
    calculable, incomplete = _crafts_recipe_margins(
        conn, server_id, query=query, job_id=job_id, level_min=level_min, level_max=level_max
    )
    # Objets déjà "engagés" ailleurs, pour signaler sur /crafts qu'on n'a pas
    # besoin de re-crafter : préparés dans l'Atelier, en forgemagie, ou en vente.
    in_atelier = [r[0] for r in conn.execute(
        "SELECT DISTINCT item_id FROM atelier_items WHERE server_id = ?", (server_id,))]
    in_forge = [r[0] for r in conn.execute(
        "SELECT DISTINCT item_id FROM tracked_trades WHERE server_id = ? AND status = 'holding' AND stage = 'forge'", (server_id,))]
    in_holding = [r[0] for r in conn.execute(
        "SELECT DISTINCT item_id FROM tracked_trades WHERE server_id = ? AND status = 'holding' AND stage = 'live'", (server_id,))]
    # Projection LÉGÈRE : le front n'affiche que ces champs. On ne renvoie plus
    # les ingrédients / incomplete_rows / ingredient_info (la section "recettes
    # incomplètes" a été retirée) — JSON bien plus court à sérialiser/transférer,
    # et une requête ingredient_info en moins.
    return {
        "crafts": [{
            "result_item_id": m.result_item_id, "result_name": m.result_name,
            "craft_cost": m.craft_cost, "sale_price_net": m.sale_price_net,
            "margin": m.margin, "quantity_sold_30j": m.quantity_sold_30j, "score": m.score,
        } for m in calculable],
        "incomplete": [{"result_item_id": m.result_item_id, "result_name": m.result_name} for m in incomplete],
        "jobs": _craft_jobs(conn),
        "in_atelier": in_atelier,
        "in_forge": in_forge,
        "in_holding": in_holding,
    }


def _pets_data(conn, server_id: int):
    ranked = rank_profitable_pets(conn, server_id)
    pending = _pending_pets(conn, server_id)
    return {"pets": ranked, "pending": pending}


def _elevage_data(conn, server_id: int, range_from: int = 0, range_to: int = GAUGE_MAX):
    """Remplissage le moins cher des 6 jauges d'enclos sur l'intervalle demandé
    (par défaut 0 → 100 000) avec les carburants d'enclos, aux prix HDV actuels
    — cf. enclos_profitability.py."""
    plans = rank_enclos_gauges(conn, server_id, range_from, range_to)
    ids = {s.item_id for p in plans for s in p.segments if s.item_id is not None}
    names = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        for iid, nm in conn.execute(f"SELECT id, name FROM items WHERE id IN ({placeholders})", tuple(ids)):
            names[iid] = nm
    gauges = [
        {
            "gauge": p.gauge,
            "total_cost": p.total_cost,
            "fillable": p.fillable,
            "segments": [
                {
                    "from": s.from_value, "to": s.to_value, "item_id": s.item_id,
                    "item_name": names.get(s.item_id), "fill": s.fill, "cap": s.cap,
                    "unit_price": s.unit_price, "source": s.source, "units": s.units, "cost": s.cost,
                }
                for s in p.segments
            ],
        }
        for p in plans
    ]
    costs = [p.total_cost for p in plans]
    total_all = sum(costs) if costs and all(c is not None for c in costs) else None
    # renvoie l'intervalle EFFECTIF (borné/échangé) pour que le front réaffiche
    # exactement ce qui a été calculé.
    eff_from, eff_to = _clamp_range(range_from, range_to)
    return {
        "gauges": gauges, "gauge_max": GAUGE_MAX, "total_all": total_all,
        "range_from": eff_from, "range_to": eff_to,
    }


def _history_item_data(conn, server_id: int, selected_id: int | None, category: str):
    """Toutes les valeurs de /history qui dépendent du dernier état en base
    pour l'objet sélectionné (pas la liste `available`/`selected_name`, qui
    ne varie pas au fil des captures du watcher) — factorisé pour être
    partagé entre la page et /api/history (rafraîchissement automatique).

    Le panneau "Cours du marché" (median/mean/quantity_sold) n'est PAS
    spécifique aux ressources : il existe pour n'importe quelle catégorie
    d'objet, familiers compris (cf. hdvcreature3.png, market_history_handler.py
    identifie sans filtre de catégorie) — calculé ici pour les 3 catégories.
    lot_prices/xp restent spécifiques aux ressources/équipements (pas de lot
    ni de nourrissage pour un familier), pet_level_prices spécifique aux
    familiers (pas de niveau pour le reste)."""
    lot_prices = {1: None, 10: None, 100: None, 1000: None}
    pet_level_prices = {0: None, 100: None}
    xp_value = None
    price_per_xp = None
    median = mean = quantity_sold = None

    if selected_id is not None:
        if category == "familier":
            pet_level_prices = _pet_level_prices(conn, selected_id, server_id)
        else:
            lot_prices = _lot_prices(conn, selected_id, server_id)
            if category == "ressource":
                xp_row = conn.execute(
                    "SELECT xp_value FROM pet_feed_xp WHERE resource_item_id = ?", (selected_id,)
                ).fetchone()
                xp_value = xp_row[0] if xp_row else None
                unit_price = get_unit_price(conn, selected_id, server_id) if xp_value else None
                price_per_xp = (unit_price / xp_value) if unit_price is not None else None

        median_row = conn.execute(
            "SELECT price_kamas FROM market_history_points WHERE item_id=? AND server_id=? AND metric='median' "
            "ORDER BY captured_at DESC LIMIT 1",
            (selected_id, server_id),
        ).fetchone()
        mean_row = conn.execute(
            "SELECT price_kamas FROM market_history_points WHERE item_id=? AND server_id=? AND metric='mean' "
            "ORDER BY captured_at DESC LIMIT 1",
            (selected_id, server_id),
        ).fetchone()
        qty_row = conn.execute(
            "SELECT quantity_sold FROM market_history_points WHERE item_id=? AND server_id=? AND quantity_sold IS NOT NULL "
            "ORDER BY captured_at DESC LIMIT 1",
            (selected_id, server_id),
        ).fetchone()
        median = median_row[0] if median_row else None
        mean = mean_row[0] if mean_row else None
        quantity_sold = qty_row[0] if qty_row else None

    return {
        "median": median,
        "mean": mean,
        "quantity_sold": quantity_sold,
        "lot_prices": lot_prices,
        "pet_level_prices": pet_level_prices,
        "xp_value": xp_value,
        "price_per_xp": price_per_xp,
    }


def _tracked_trades_data(conn, server_id: int):
    """Positions suivies (achat -> revente manuelle) pour le serveur actif,
    réparties en cours/vendues + résumé agrégé. Indépendant de
    price_snapshots par construction : une transaction RÉELLE de
    l'utilisateur (ce qu'il a effectivement payé/reçu), pas un relevé HDV."""
    rows = conn.execute(
        """
        SELECT t.id, t.item_id, i.name, i.super_type_name, t.quantity, t.buy_price_kamas,
               t.sell_price_kamas, t.status, t.stage, t.rune_cost_kamas, t.created_at, t.sold_at
        FROM tracked_trades t JOIN items i ON i.id = t.item_id
        WHERE t.server_id = ?
        ORDER BY t.created_at DESC
        """,
        (server_id,),
    ).fetchall()

    holding = []       # positions en vente (stage 'live'), à plat — pour le dashboard
    sold = []          # positions vendues, à plat — pour le dashboard
    forgemagie = []    # équipements en forgemagie (stage 'forge')
    holding_groups: dict = {}  # item_id -> groupe (accordéon /suivis)
    sold_groups: dict = {}
    total_invested = 0
    total_realized = 0
    total_potential = 0          # bénéfice si les positions "en cours" partent à leur prix visé
    total_potential_revenue = 0  # ce que tu ENCAISSES à la revente (= somme des prix visés)
    holding_invested = 0         # investi réparti par section (pour les sous-totaux)
    sold_invested = 0
    sold_revenue = 0             # ce que tu as réellement encaissé sur le vendu

    def _group(groups: dict, trade: dict):
        """Agrège un trade dans le groupe de son objet (case principale =
        somme des achats/ventes de tous ses exemplaires)."""
        g = groups.get(trade["item_id"])
        if g is None:
            g = {
                "item_id": trade["item_id"], "name": trade["name"], "category": trade["category"],
                "n_trades": 0, "total_quantity": 0, "total_buy": 0,
                "total_sell": 0, "sell_known": 0, "total_profit": 0, "trades": [],
            }
            groups[trade["item_id"]] = g  # ordre d'insertion = plus récent d'abord (rows triées DESC)
        g["trades"].append(trade)
        g["n_trades"] += 1
        g["total_quantity"] += trade["quantity"]
        g["total_buy"] += trade["effective_buy"]
        if trade["sell_price_kamas"] is not None:
            g["total_sell"] += trade["sell_price_kamas"]
            g["sell_known"] += 1
        if trade.get("profit") is not None:
            g["total_profit"] += trade["profit"]

    for tid, item_id, name, super_type_name, qty, buy, sell, status, stage, rune, created_at, sold_at in rows:
        rune = rune or 0
        effective_buy = buy + rune  # coût réel = achat/craft + forgemagie
        trade = {
            "id": tid, "item_id": item_id, "name": name,
            "category": _category_for_super_type(super_type_name),
            "quantity": qty, "buy_price_kamas": buy, "rune_cost_kamas": rune,
            "effective_buy": effective_buy, "sell_price_kamas": sell,
            "status": status, "stage": stage, "created_at": created_at, "sold_at": sold_at,
        }
        total_invested += effective_buy
        if status == "sold":
            trade["profit"] = (sell or 0) - effective_buy
            total_realized += trade["profit"]
            sold_invested += effective_buy
            sold_revenue += sell or 0
            sold.append(trade)
            _group(sold_groups, trade)
        elif stage == "forge":
            forgemagie.append(trade)
        else:  # holding 'live'
            holding_invested += effective_buy
            holding.append(trade)
            _group(holding_groups, trade)
            if sell is not None:  # prix de vente visé renseigné
                total_potential += sell - effective_buy
                total_potential_revenue += sell

    return {
        "holding": holding, "sold": sold, "forgemagie": forgemagie,
        "holding_groups": list(holding_groups.values()),
        "sold_groups": list(sold_groups.values()),
        "total_invested": total_invested,
        "total_realized": total_realized,
        "total_potential": total_potential,
        "total_potential_revenue": total_potential_revenue,
        "holding_invested": holding_invested,
        "sold_invested": sold_invested,
        "sold_revenue": sold_revenue,
        "holding_count": len(holding),
        "sold_count": len(sold),
        "forge_count": len(forgemagie),
    }


def _pet_level_reached(total_xp: float) -> int:
    """Niveau atteint pour une XP cumulée donnée (le plus haut niveau dont le
    coût cumulé est <= total_xp)."""
    reached = 0
    for level in range(PET_MAX_LEVEL + 1):
        if PET_CUMULATIVE_XP_BY_LEVEL[level] <= total_xp:
            reached = level
        else:
            break
    return reached


def _recipe_id_for_item(conn, item_id: int) -> int | None:
    row = conn.execute("SELECT id FROM recipes WHERE result_item_id = ? LIMIT 1", (item_id,)).fetchone()
    return row[0] if row else None


def _feed_options(conn, server_id: int) -> list[dict]:
    """Toutes les ressources de nourrissage (pet_feed_xp) dont on connaît un
    prix, triées par meilleur ratio XP/kama décroissant. Sert à la fois au
    tableau "top 20" et aux choix de ressource par familier."""
    rows = conn.execute(
        "SELECT pf.resource_item_id, i.name, pf.xp_value FROM pet_feed_xp pf "
        "JOIN items i ON i.id = pf.resource_item_id WHERE pf.xp_value > 0"
    ).fetchall()
    out = []
    for rid, name, xp in rows:
        unit_price = get_unit_price(conn, rid, server_id)
        if unit_price is None or unit_price <= 0:
            continue
        out.append({
            "item_id": rid, "name": name, "xp_value": xp,
            "unit_price": unit_price, "xp_per_kama": xp / unit_price,
        })
    out.sort(key=lambda r: r["xp_per_kama"], reverse=True)
    return out


def _atelier_data(conn, server_id: int) -> dict:
    """Le client agrège lui-même la liste de courses (Σ base_qty × nombre de
    crafts par équipement) et recalcule barrés/vert/coûts en direct — on
    envoie donc les ingrédients PAR CRAFT (base_qty) + le nombre de crafts +
    la quantité possédée par ressource, pas une liste pré-agrégée figée."""
    owned = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT resource_item_id, quantity_owned FROM atelier_owned_resources WHERE server_id = ?",
            (server_id,),
        )
    }
    entries = conn.execute(
        "SELECT a.id, a.item_id, i.name, a.kind, a.quantity, a.feed_resource_item_id, a.feed_qty_owned "
        "FROM atelier_items a JOIN items i ON i.id = a.item_id "
        "WHERE a.server_id = ? ORDER BY a.created_at",
        (server_id,),
    ).fetchall()

    equipments, pets = [], []
    for aid, item_id, name, kind, quantity, feed_res, feed_qty in entries:
        if kind == "equipment":
            recipe_id = _recipe_id_for_item(conn, item_id)
            ingredients, craft_cost_unit = [], 0.0
            if recipe_id is not None:
                cost = compute_craft_cost(conn, recipe_id, server_id)
                craft_cost_unit = cost.total_cost
                for ing in cost.ingredients:
                    ingredients.append({
                        "item_id": ing.item_id, "name": ing.name,
                        "base_qty": ing.quantity, "unit_price": ing.unit_price,
                    })
            equipments.append({
                "atelier_id": aid, "item_id": item_id, "name": name, "recipe_id": recipe_id,
                "craft_count": quantity, "craft_cost_unit": craft_cost_unit,
                "ingredients": ingredients,
                "sell_default": get_average_sale_price(conn, item_id, server_id),
            })
        else:  # pet — le client calcule qty_for_100 / niveau / prêt via feed_options
            row100 = conn.execute(
                "SELECT price_kamas FROM price_snapshots WHERE item_id = ? AND server_id = ? "
                "AND pet_level = 100 ORDER BY captured_at DESC LIMIT 1",
                (item_id, server_id),
            ).fetchone()
            pets.append({
                "atelier_id": aid, "item_id": item_id, "name": name,
                "feed_resource_item_id": feed_res, "feed_qty_owned": feed_qty,
                "sell_default": row100[0] if row100 else None,
            })

    feed_all = _feed_options(conn, server_id)
    return {
        "equipments": equipments,
        "pets": pets,
        "owned": owned,
        "feed_leaderboard": feed_all[:20],
        "feed_options": feed_all,
        "xp_table": PET_CUMULATIVE_XP_BY_LEVEL,
        "xp_to_100": PET_XP_TO_MAX,
    }



def _history_available(conn, server_id: int, category: str) -> list[dict]:
    """Objets d'une catégorie ayant au moins un relevé (prix HDV OU cours du
    marché) — la liste de gauche / le combo de recherche de /history.
    Réplique la requête de history_page du monolithe."""
    if category == "familier":
        rows = conn.execute(
            "SELECT DISTINCT i.id, i.name FROM items i "
            "WHERE i.super_type_name = 'Familier' AND i.id IN ("
            "  SELECT item_id FROM price_snapshots WHERE server_id = ? AND pet_level IS NOT NULL "
            "  UNION SELECT item_id FROM market_history_points WHERE server_id = ?) "
            "ORDER BY i.name",
            (server_id, server_id),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT DISTINCT i.id, i.name FROM items i "
            f"WHERE {_category_filter_sql(category)} AND i.id IN ("
            f"  SELECT item_id FROM price_snapshots WHERE server_id = ? "
            f"  UNION SELECT item_id FROM market_history_points WHERE server_id = ?) "
            f"ORDER BY i.name",
            (server_id, server_id),
        ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]
