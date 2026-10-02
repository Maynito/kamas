# Kamas — suivi des prix & rentabilité Dofus

Site web qui suit les prix de l'Hôtel de Vente de Dofus et calcule la
rentabilité des crafts et de la montée de niveau des familiers. Voir `README.md`
pour la présentation et `DEV.md` pour les commandes.

Les relevés de prix sont produits par un **service de capture séparé** (le
*collector*, dans un dépôt privé) qui tourne sur la machine du joueur et POST
les données à la Price API (`/v1/ingest`). Ce dépôt-ci contient tout sauf ce
collector : l'API, le site, les calculs, le déploiement.

## Architecture (producteur / consommateur)

- **`services/api/`** — Price API (FastAPI). Seule propriétaire de la base.
  Ingestion (`app/ingest.py`), lecture publique (`app/data.py`), écritures front
  (`app/writes.py`), auth par clés/scopes (`app/auth.py`), rate-limit
  (`app/ratelimit.py`), migrations Alembic (`alembic/`). Tests hermétiques dans
  `tests/`.
- **`services/front/`** — Site (FastAPI + Jinja + httpx). Présentation seule :
  il appelle l'API, ne touche **jamais** la base. Réutilise les templates et la
  feuille de style de `src/web/`.
- **`src/web/`** — `templates/` (Jinja) + `static/theme.css` (design system à
  tokens, thèmes clair/sombre) + `static/app.js`. Servis par le front.
- **`src/pricing/`** — `profitability.py` (marges de craft) et
  `pet_profitability.py` (coût de la montée des familiers). Aucune dépendance
  web : pure logique métier.
- **`src/db/`** — schéma SQL, `connection.py` (SQLite dev), `reading_store.py`
  (persistance des relevés, SQL pur).
- **`src/sync/`** — `dofusdb_sync.py` : peuple le catalogue statique
  (items / recettes / métiers + icônes) depuis l'API DofusDB.
- **`packages/shared/`** — `kamas_shared` : schémas Pydantic du contrat
  d'ingestion, partagés avec le collector (forme des données, pas de logique).

## Données

- **Dev** : SQLite (`data/kamas.db`). **Prod** : PostgreSQL. `services/api/app/db.py`
  gère les deux dialectes via `DATABASE_URL`.
- `data/` est gitignoré et régénéré par `python tasks.py sync`.
- Tables clés : `items`, `recipes`/`recipe_ingredients`, `price_snapshots`
  (un relevé par objet × taille de lot OU niveau de familier),
  `market_history_points` (médian/moyen/quantité vendue), `tracked_trades`
  (suivis achat → revente), `pet_feed_xp`, `api_keys`, `servers`, `app_config`.

## Conventions front

- Pages à rafraîchissement auto : une route HTML + une route `/api/*` jumelle
  qui partagent un helper de données côté API (jamais de logique dupliquée).
- Signature Starlette récente : `templates.TemplateResponse(request, "x.html", {...})`
  (request en 1er argument positionnel).
- Le cache CSS est busté via `static_version` (mtime de `theme.css`) injecté
  dans le `<link>` — pas de Ctrl+F5 à la main après une édition de style.

## Lancer

```bash
python tasks.py setup && python tasks.py sync && python tasks.py stack
python tasks.py test
```

## Notes

- `src/pricing/` contient des commentaires qui citent les modules du collector
  (ex. `familiar_hdv_handler.py`) à titre de contexte — ces modules vivent dans
  le dépôt privé, pas ici.
- La montée de familier utilise une table d'XP cumulative réelle
  (`PET_CUMULATIVE_XP_BY_LEVEL`), pas une interpolation linéaire.
