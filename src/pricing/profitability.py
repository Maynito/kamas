"""Calcul de rentabilité des crafts : combine les prix de ressources
collectés par OCR (price_snapshots) avec les recettes statiques sourcées
de DofusDB (recipes/recipe_ingredients) pour estimer la marge nette d'un
équipement, réglable via la taxe HDV et une marge de sécurité manuelle.

    prix_ressource_unitaire(item) = MIN, parmi les lots connus (1/10/100/1000),
        du dernier prix connu de chaque lot ramené à l'unité
    cout_craft(recette)  = Σ quantite_ingredient × prix_ressource_unitaire(ingredient)
    prix_vente_net(item) = dernier_prix_moyen(item) × (1 − taxe_hdv − marge_securite)
    marge(recette)       = prix_vente_net(recette.result_item) − cout_craft(recette)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


def get_config(conn: sqlite3.Connection, key: str, default: float) -> float:
    row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    return float(row[0]) if row else default


def get_unit_price(conn: sqlite3.Connection, item_id: int, server_id: int, price_cache=None) -> float | None:
    """Prix à l'unité le plus avantageux parmi les lots connus (1/10/100/1000) :
    pour chaque taille de lot, on prend son dernier prix connu et on le
    ramène à l'unité, puis on garde le MINIMUM plutôt que simplement le lot
    le plus récemment capturé. Un joueur peut afficher un prix délirant sur
    un seul lot (ex. x1 à 1 000 000 kamas alors que le x10 est à 1 000, donc
    100/u) sans que ça reflète le vrai marché ; comparer tous les lots
    connus et garder le moins cher évite qu'un prix aberrant isolé fausse le
    coût de craft estimé.

    `price_cache` (PriceCache, optionnel) : version pré-calculée en LOT pour
    éviter une requête par item quand on traite des milliers de recettes
    (cf. build_price_cache) — None = requête par item, comportement d'origine."""
    if price_cache is not None:
        return price_cache.unit_price.get(item_id)
    rows = conn.execute(
        """
        SELECT lot_size, price_kamas FROM price_snapshots
        WHERE item_id = ? AND server_id = ? AND pet_level IS NULL
        ORDER BY captured_at DESC
        """,
        (item_id, server_id),
    ).fetchall()
    if not rows:
        return None
    best_per_lot: dict[int, float] = {}
    for lot_size, price_kamas in rows:
        best_per_lot.setdefault(lot_size, price_kamas / lot_size)  # 1re rencontre = plus récente (ORDER BY DESC)
    return min(best_per_lot.values())


# Le "nombre d'articles vendus" du panneau "Cours du marché" est PROPRE À
# L'ONGLET actif (24h / 7j / 30j), pas une valeur cumulée — vérifié sur les
# données réelles (ex. même objet : 9 vendus sur 24h vs 143 sur 30j). Pour
# ramener n'importe quel onglet à une estimation MENSUELLE comparable, on met
# à l'échelle par le nombre de fenêtres dans ~30 jours. Le 30j est direct
# (facteur 1), les fenêtres plus courtes sont des estimations (extrapolées),
# préférées uniquement à défaut de 30j.
_PERIOD_TO_MONTHLY_SCALE = (("30j", 1.0), ("7j", 30 / 7), ("24h", 30.0))


def get_monthly_quantity_sold(conn: sqlite3.Connection, item_id: int, server_id: int, price_cache=None) -> int | None:
    """Nombre d'articles vendus sur ~30 jours, tel qu'affiché par le panneau
    "Cours du marché" — sert de proxy au volume de vente réel d'un objet, faute
    d'un historique de transactions individuelles.

    On préfère l'onglet "30 jours" (valeur mensuelle directe) ; à défaut, on
    retombe sur l'onglet "7 jours" puis "24h" en EXTRAPOLANT à 30 jours (le
    compteur est par onglet, pas cumulé — cf. commentaire ci-dessus). Sans ce
    repli, un craft dont le panneau a été capturé avec un autre onglet actif
    (cas réel rencontré : une ressource craftable capturée en "24h") n'avait
    jamais de bénéfice mensuel, alors que la donnée existait — d'où
    l'impression que "seuls les équipements fonctionnent".

    `None` tant qu'AUCUNE capture (quel que soit l'onglet) n'a été faite pour
    cet objet (pas 0 : on ne sait pas, on n'a pas constaté une absence de vente).

    `price_cache` : cf. get_unit_price (batch, None = requête par item)."""
    if price_cache is not None:
        return price_cache.monthly_qty.get(item_id)
    for period, scale in _PERIOD_TO_MONTHLY_SCALE:
        row = conn.execute(
            """
            SELECT quantity_sold FROM market_history_points
            WHERE item_id = ? AND server_id = ? AND period = ? AND quantity_sold IS NOT NULL
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (item_id, server_id, period),
        ).fetchone()
        if row is not None:
            return round(row[0] * scale)
    return None


def get_average_sale_price(conn: sqlite3.Connection, item_id: int, server_id: int, price_cache=None) -> float | None:
    """Prix de vente de référence d'un objet : le PRIX HDV le moins cher relevé
    (`get_unit_price`). Pour un équipement crafté, c'est le prix d'un craft
    "propre" (statistiques de base — la 1re annonce, la moins chère, cf.
    equipment_hdv_handler), donc ce qu'on peut réellement en tirer.

    On N'UTILISE PLUS le prix MOYEN du panneau "Cours du marché" comme référence
    principale : il agrège TOUTES les annonces de l'objet, y compris les
    variantes exotiques (exo PA/PM, forgemagie coûteuse...) BIEN plus chères
    qu'un craft de base qu'on ne cherche pas à reproduire — ce qui gonflait
    artificiellement la "vente nette" (cas réel signalé : Anneau du Chêne Mou
    affiché ~730k via le moyen alors qu'il se vend ~180k à l'HDV). Le moyen du
    marché n'est plus qu'un REPLI, utilisé seulement si aucun relevé HDV
    n'existe pour l'objet.

    `price_cache` : cf. get_unit_price (batch, None = requête par item)."""
    if price_cache is not None:
        p = price_cache.unit_price.get(item_id)
        return p if p is not None else price_cache.market_mean.get(item_id)
    price = get_unit_price(conn, item_id, server_id)
    if price is not None:
        return price
    row = conn.execute(
        """
        SELECT price_kamas FROM market_history_points
        WHERE item_id = ? AND server_id = ? AND metric = 'mean'
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (item_id, server_id),
    ).fetchone()
    return float(row[0]) if row is not None else None


@dataclass
class PriceCache:
    """Prix pré-calculés en LOT pour un serveur — évite ~N requêtes par recette
    quand on chiffre des milliers de recettes d'un coup (page Crafts). Reproduit
    EXACTEMENT la logique unitaire de get_unit_price / get_average_sale_price /
    get_monthly_quantity_sold, mais en 3 requêtes groupées au lieu d'une par
    item. Injecté via le param `price_cache` de ces fonctions (None = requête
    par item, comportement d'origine — inchangé pour pets/élevage/tests)."""
    unit_price: dict[int, float]    # item_id -> prix unitaire le moins cher (min sur lots)
    market_mean: dict[int, float]   # item_id -> dernier prix moyen "Cours du marché"
    monthly_qty: dict[int, int]     # item_id -> volume mensuel estimé


def build_price_cache(conn: sqlite3.Connection, server_id: int) -> PriceCache:
    """Construit un PriceCache en 3 requêtes groupées (cf. sa docstring)."""
    # 1) prix unitaire = MIN sur lots du dernier prix par lot ramené à l'unité.
    best_per_lot: dict[tuple[int, int], float] = {}
    for item_id, lot_size, price in conn.execute(
        "SELECT item_id, lot_size, price_kamas FROM price_snapshots "
        "WHERE server_id = ? AND pet_level IS NULL ORDER BY captured_at DESC",
        (server_id,),
    ):
        key = (item_id, lot_size)
        if key not in best_per_lot:  # 1re rencontre = plus récente (ORDER BY DESC)
            best_per_lot[key] = price / lot_size
    unit_price: dict[int, float] = {}
    for (item_id, _lot), per in best_per_lot.items():
        if item_id not in unit_price or per < unit_price[item_id]:
            unit_price[item_id] = per

    # 2) prix moyen du Cours du marché (repli de get_average_sale_price).
    market_mean: dict[int, float] = {}
    for item_id, price in conn.execute(
        "SELECT item_id, price_kamas FROM market_history_points "
        "WHERE server_id = ? AND metric = 'mean' ORDER BY captured_at DESC",
        (server_id,),
    ):
        if item_id not in market_mean:
            market_mean[item_id] = float(price)

    # 3) volume mensuel : dernier quantity_sold par (item, période), période
    #    préférée 30j > 7j > 24h avec mise à l'échelle (cf. get_monthly_quantity_sold).
    latest_qty: dict[tuple[int, str], int] = {}
    for item_id, period, qty in conn.execute(
        "SELECT item_id, period, quantity_sold FROM market_history_points "
        "WHERE server_id = ? AND quantity_sold IS NOT NULL ORDER BY captured_at DESC",
        (server_id,),
    ):
        key = (item_id, period)
        if key not in latest_qty:
            latest_qty[key] = qty
    monthly_qty: dict[int, int] = {}
    for item_id, _period in latest_qty:
        if item_id in monthly_qty:
            continue
        for pref_period, scale in _PERIOD_TO_MONTHLY_SCALE:
            if (item_id, pref_period) in latest_qty:
                monthly_qty[item_id] = round(latest_qty[(item_id, pref_period)] * scale)
                break
    return PriceCache(unit_price, market_mean, monthly_qty)


@dataclass
class IngredientCost:
    item_id: int
    name: str
    quantity: int
    unit_price: float | None  # None si prix inconnu — cf. CraftCost.missing_ingredients


@dataclass
class CraftCost:
    total_cost: float
    ingredients: list[IngredientCost] = field(default_factory=list)  # TOUS les ingrédients, prix connu ou non
    missing_ingredients: list[tuple[int, str, int]] = field(default_factory=list)  # (item_id, name, quantity)


def compute_craft_cost(conn: sqlite3.Connection, recipe_id: int, server_id: int, price_cache=None) -> CraftCost:
    total = 0.0
    ingredients: list[IngredientCost] = []
    missing: list[tuple[int, str, int]] = []
    rows = conn.execute(
        """
        SELECT ri.ingredient_item_id, i.name, ri.quantity
        FROM recipe_ingredients ri
        LEFT JOIN items i ON i.id = ri.ingredient_item_id
        WHERE ri.recipe_id = ?
        """,
        (recipe_id,),
    ).fetchall()
    for ingredient_item_id, name, quantity in rows:
        display_name = name or f"item-{ingredient_item_id}"
        unit_price = get_unit_price(conn, ingredient_item_id, server_id, price_cache)
        ingredients.append(IngredientCost(ingredient_item_id, display_name, quantity, unit_price))
        if unit_price is None:
            missing.append((ingredient_item_id, display_name, quantity))
            continue
        total += unit_price * quantity
    return CraftCost(total_cost=total, ingredients=ingredients, missing_ingredients=missing)


@dataclass
class RecipeMargin:
    recipe_id: int
    result_item_id: int
    result_name: str
    craft_cost: float
    sale_price_net: float | None
    margin: float | None
    ingredients: list[IngredientCost]
    missing_ingredients: list[tuple[int, str, int]]
    quantity_sold_30j: int | None = None
    score: float | None = None


def net_sale_factor(conn: sqlite3.Connection) -> float:
    """1 − taxe HDV − marge de sécurité, à lire une seule fois par appelant
    qui traite plusieurs recettes plutôt qu'à chaque recette (évite de
    refaire 2 SELECT sur app_config par recette dans rank_profitable_recipes,
    négligeable sur ~5000 lignes mais inutile)."""
    hdv_tax = get_config(conn, "hdv_tax_percent", 2.0) / 100
    safety_margin = get_config(conn, "safety_margin_percent", 5.0) / 100
    return 1 - hdv_tax - safety_margin


def compute_recipe_margin(
    conn: sqlite3.Connection, recipe_id: int, server_id: int, net_factor: float | None = None, price_cache=None
) -> RecipeMargin:
    recipe = conn.execute(
        "SELECT result_item_id FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    result_item_id = recipe[0]
    result_name = conn.execute(
        "SELECT name FROM items WHERE id = ?", (result_item_id,)
    ).fetchone()[0]

    cost = compute_craft_cost(conn, recipe_id, server_id, price_cache)

    if net_factor is None:
        net_factor = net_sale_factor(conn)

    avg_price = get_average_sale_price(conn, result_item_id, server_id, price_cache)
    sale_price_net = avg_price * net_factor if avg_price is not None else None
    margin = sale_price_net - cost.total_cost if sale_price_net is not None else None

    quantity_sold_30j = get_monthly_quantity_sold(conn, result_item_id, server_id, price_cache)
    # Bénéfice mensuel total estimé (marge unitaire × volume vendu/mois),
    # pas seulement la marge à l'unité : un objet peu rentable mais qui se
    # vend en masse doit surclasser un objet très rentable mais invendable
    # — cf. rank_profitable_recipes(), qui trie sur ce score en priorité.
    score = margin * quantity_sold_30j if margin is not None and quantity_sold_30j is not None else None

    return RecipeMargin(
        recipe_id=recipe_id,
        result_item_id=result_item_id,
        result_name=result_name,
        craft_cost=cost.total_cost,
        sale_price_net=sale_price_net,
        margin=margin,
        ingredients=cost.ingredients,
        missing_ingredients=cost.missing_ingredients,
        quantity_sold_30j=quantity_sold_30j,
        score=score,
    )


def ranking_key(margin: RecipeMargin) -> tuple[int, float]:
    """Classe par score (marge × volume vendu/mois) en priorité : un craft ou
    un familier avec un volume confirmé passe toujours devant celui dont on
    ignore encore s'il se vend (pas de capture "Cours du marché" pour lui
    pour l'instant) — celles-ci restent visibles, triées entre elles par
    marge unitaire à défaut de mieux, plutôt que d'être masquées ou
    arbitrairement mêlées à des scores qui, eux, reposent sur une donnée
    de volume réelle. Partagé entre RecipeMargin (crafts) et PetMargin
    (familiers, cf. pet_profitability.py) — mêmes champs score/margin,
    même sémantique de tri, le panneau "Cours du marché" existant pour
    n'importe quelle catégorie d'objet (cf. market_history_handler.py)."""
    if margin.score is not None:
        return (1, margin.score)
    return (0, margin.margin)


def rank_profitable_recipes(conn: sqlite3.Connection, server_id: int) -> list[RecipeMargin]:
    """Classe toutes les recettes calculables (aucun ingrédient sans prix
    connu) par bénéfice mensuel estimé décroissant (marge unitaire × volume
    vendu/mois, cf. compute_recipe_margin) — pas la marge unitaire seule :
    un objet peu rentable mais qui se vend énormément doit surclasser un
    objet très rentable mais invendable. C'est la vue "d'un coup d'œil ce
    qui rapporte le plus à craft" visée par l'outil."""
    net_factor = net_sale_factor(conn)
    recipe_ids = [row[0] for row in conn.execute("SELECT id FROM recipes")]
    results = [compute_recipe_margin(conn, rid, server_id, net_factor) for rid in recipe_ids]
    calculable = [r for r in results if r.margin is not None and not r.missing_ingredients]
    calculable.sort(key=ranking_key, reverse=True)
    return calculable
