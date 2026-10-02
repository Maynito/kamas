# Kamas — suivi des prix & rentabilité Dofus

Outil web qui suit les prix de l'Hôtel de Vente de Dofus et calcule, à partir
de ces relevés, la **rentabilité des crafts** et de la **montée de niveau des
familiers**. Il aide à décider quoi crafter / élever pour maximiser le bénéfice.

Les prix sont alimentés par un **service de capture séparé** (le *collector*,
hébergé dans un dépôt privé) qui tourne sur la machine du joueur, reconnaît
l'objet affiché à l'écran et POST les relevés à la Price API de ce dépôt. Ce
dépôt contient tout le reste : l'API, le site, les calculs et le déploiement.

## Architecture

Trois services déployables indépendamment, reliés par HTTP — un vrai schéma
**producteur / consommateur** :

```
 ┌────────────────────┐        POST /v1/ingest        ┌──────────────────────┐
 │  Collector (privé) │ ───────────────────────────▶  │  Price API (ce repo) │
 │  capture + reco     │                               │  FastAPI + SQL        │
 │  → tourne chez le   │                               │  seule propriétaire   │
 │    joueur (Windows) │                               │  de la base           │
 └────────────────────┘                               └───────────┬──────────┘
                                                                   │ HTTP (httpx)
                                                       ┌───────────▼──────────┐
                                                       │  Front (ce repo)     │
                                                       │  Jinja SSR, pas de    │
                                                       │  build JS             │
                                                       └──────────────────────┘
```

Pourquoi ce découpage : le collector **doit** être local (il lit l'écran du
jeu), tandis que l'API et le site doivent être **toujours en ligne**. La
frontière suit donc une contrainte réelle. Côté concurrence, l'API est **seule
à écrire** la base → pas de multi-écrivains sur un fichier.

| Dossier            | Rôle                                                                 |
|--------------------|----------------------------------------------------------------------|
| `services/api/`    | Price API (FastAPI) : ingestion, lecture publique, auth par clés/scopes, migrations Alembic. |
| `services/front/`  | Site (FastAPI + Jinja + httpx) : présentation seule, ne touche jamais la base. |
| `src/web/`         | Templates + feuille de style (design system) réutilisés par le front. |
| `src/pricing/`     | Calcul des marges (crafts) et du coût de la montée des familiers.     |
| `src/db/`          | Schéma SQL, connexion, persistance des relevés.                      |
| `src/sync/`        | Synchronisation du catalogue statique (items / recettes / métiers) depuis l'API DofusDB. |
| `packages/shared/` | Contrat d'ingestion (schémas Pydantic) partagé avec le collector.   |

## Stack technique

- **Backend** : Python 3.13, FastAPI, Uvicorn (ASGI), Pydantic.
- **Données** : SQLite en dev, PostgreSQL en prod ; SQLAlchemy 2.0 + Alembic (migrations). Dialectes gérés via `services/api/app/db.py`.
- **Front** : rendu côté serveur (Jinja2) + JavaScript vanille (pas d'étape de build), design system CSS à tokens (thèmes clair/sombre).
- **Sécurité API** : clés hashées (SHA-256) à scopes (`read` / `owner` / `admin`), rate-limiting, CORS configurable, secret d'ingestion dédié.
- **Infra** : Docker + docker-compose (api + front + Postgres), blueprint Render (`render.yaml`), CI GitHub Actions (tests d'API hermétiques + build des images).

## Démarrage local

```bash
python tasks.py setup     # crée le venv et installe les dépendances
python tasks.py sync      # peuple la base (items/recettes/icônes) depuis DofusDB
python tasks.py stack     # lance API (:8100) + Front (:8200) dans une console
```

- Site : http://127.0.0.1:8200
- API + Swagger : http://127.0.0.1:8100/v1/docs

```bash
python tasks.py test      # suite de tests hermétiques de la Price API
```

## Déploiement

`docker compose up` lance Postgres + API + Front en local. En hébergé, le
blueprint `render.yaml` provisionne une base Postgres managée + les deux
services web (le collector n'est **pas** déployé : il tourne chez le joueur).
Voir les commentaires de `render.yaml` pour le seed initial et la création des
clés d'API.
