#!/bin/sh
# Entrypoint de la Price API en conteneur.
# 1) attend que la base (Postgres) réponde ; 2) applique les migrations Alembic ;
# 3) seed depuis la SQLite montée SI la base est vide ; 4) lance uvicorn.
# En dev sans Postgres (DATABASE_URL absent), db.py/alembic retombent sur la
# SQLite locale — ce script marche pareil.
set -e
cd /app/services/api

echo "[entrypoint] attente de la base..."
python - <<'PY'
import os, time, sys
from sqlalchemy import create_engine, text
url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit(0)  # SQLite locale : rien à attendre
for i in range(30):
    try:
        create_engine(url).connect().execute(text("SELECT 1"))
        print("[entrypoint] base prête")
        sys.exit(0)
    except Exception as e:
        print(f"[entrypoint] base indisponible ({i+1}/30): {e.__class__.__name__}")
        time.sleep(2)
sys.exit("[entrypoint] base injoignable après 60s")
PY

echo "[entrypoint] alembic upgrade head"
python -m alembic upgrade head

# Seed une seule fois : uniquement si la table items est vide.
python - <<'PY'
import os
from app.db import get_conn
conn = get_conn()
empty = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
conn.close()
if empty and os.path.exists("/app/data/kamas.db"):
    print("[entrypoint] base vide -> seed depuis /app/data/kamas.db")
    import subprocess, sys
    subprocess.check_call([sys.executable, "scripts/seed_from_sqlite.py", "/app/data/kamas.db"])
else:
    print("[entrypoint] base déjà peuplée (ou pas de SQLite source) -> pas de seed")
PY

echo "[entrypoint] démarrage uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port 8100
