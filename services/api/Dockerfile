# Price API — service léger (pas de vision). Contexte de build = racine du repo.
FROM python:3.13-slim
WORKDIR /app
COPY services/api/requirements.txt services/api/requirements.txt
RUN pip install --no-cache-dir -r services/api/requirements.txt
COPY packages/ packages/
COPY src/db/ src/db/
COPY src/pricing/ src/pricing/
COPY services/api/ services/api/
RUN chmod +x services/api/entrypoint.sh
EXPOSE 8100
# entrypoint.sh attend la base, applique les migrations Alembic, seed si vide,
# puis lance uvicorn. La source de données (Postgres) vient de DATABASE_URL ;
# en dev sans Postgres, repli SQLite sur le volume monté /app/data.
CMD ["services/api/entrypoint.sh"]
