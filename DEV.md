# Commandes de dev

Tout passe par `python tasks.py <commande>` (Python pur, marche pareil sous
PowerShell / Git Bash / cmd — pas besoin de `make`).

| Commande                     | Effet                                                                 |
|------------------------------|----------------------------------------------------------------------|
| `python tasks.py setup`      | Crée `.venv` et installe `requirements.txt`.                         |
| `python tasks.py sync`       | Peuple la base locale (items / recettes / métiers + icônes) depuis l'API DofusDB. À faire une fois sur une machine neuve, avant `stack`. |
| `python tasks.py stack`      | Lance la Price API (:8100) + le Front (:8200) dans une seule console (Ctrl+C arrête tout). `stack -- --windows` : une console par service. |
| `python tasks.py test`       | Suite de tests hermétiques de la Price API.                         |

## Sur une machine neuve

`data/` (base SQLite + icônes) est gitignoré et régénéré localement :

```bash
python tasks.py setup
python tasks.py sync      # télécharge le catalogue + les icônes
python tasks.py stack
```

## Base de données

- **Dev** : SQLite (`data/kamas.db`), ouverte via `src/db/connection.py`.
- **Prod** : PostgreSQL. `services/api/app/db.py` et `alembic/env.py` lisent
  tous deux `DATABASE_URL` ; la bascule est transparente.
- Migrations (Alembic) :
  ```bash
  cd services/api
  python -m alembic upgrade head      # applique
  python -m alembic downgrade base    # annule
  python -m alembic check             # détecte un décalage schéma ↔ migrations
  ```

## Docker

```bash
docker compose up        # Postgres + API (:8100) + Front (:8200)
```
